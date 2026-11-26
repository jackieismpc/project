"""海康威视工业相机视频源（``VideoSource`` 实现）。

支持 macOS 开发机与 Windows 部署机通过 ``hikvision_sdk`` 自动发现并加载 MVS SDK，
按配置中的设备标识（``auto``、序列号、自定义名称、IP 或枚举序号）连接相机并持续取帧。
构造时不做任何耗时操作，也不在导入时加载原生库。
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.infrastructure.video.hikvision_sdk import (
    MV_ACCESS_EXCLUSIVE,
    MV_FRAME_OUT,
    MV_GIGE_DEVICE,
    MV_OK,
    MV_USB_DEVICE,
    PIXEL_TYPE_BGR8_PACKED,
    PIXEL_TYPE_MONO8,
    PIXEL_TYPE_RGB8_PACKED,
    TIMEOUT_ERRORS,
    HikvisionCameraDevice,
    HikvisionSdkError,
    HikvisionSdkLocation,
    load_mvs_library,
)
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)

_log = logging.getLogger(__name__)

_EXPOSURE_AUTO_MAP: dict[str, str] = {
    "off": "Off",
    "once": "Once",
    "continuous": "Continuous",
}


def _select_device(devices: list[HikvisionCameraDevice], selector: str) -> HikvisionCameraDevice:
    key = selector.strip()
    lower = key.lower()
    if lower in ("auto", "first"):
        return devices[0]
    for dev in devices:
        candidates = (
            dev.serial_number.lower(),
            dev.user_defined_name.lower(),
            (dev.ip_address or "").lower(),
        )
        if lower in candidates and lower:
            return dev
    if key.isdigit():
        idx = int(key)
        if 0 <= idx < len(devices):
            return devices[idx]
    available = "、".join(
        f"{d.model_name}({d.serial_number or d.ip_address or d.index})" for d in devices
    )
    raise VideoOpenError(f"未找到匹配 {selector!r} 的海康相机；当前在线：{available}")


def _copy_bgr(
    lib: Any,
    handle: Any,
    frame_out: MV_FRAME_OUT,
) -> NDArray[np.uint8]:
    info = frame_out.stFrameInfo
    width = int(info.nExtendWidth or info.nWidth)
    height = int(info.nExtendHeight or info.nHeight)
    pixel_type = int(info.enPixelType)
    buf_addr = frame_out.pBufAddr
    if width <= 0 or height <= 0 or not buf_addr:
        raise VideoDisconnectedError("海康相机返回空图像缓冲")

    if pixel_type == PIXEL_TYPE_BGR8_PACKED:
        raw = ctypes.string_at(buf_addr, width * height * 3)
        return np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()
    if pixel_type == PIXEL_TYPE_MONO8:
        raw = ctypes.string_at(buf_addr, width * height)
        gray = np.frombuffer(raw, dtype=np.uint8).reshape((height, width))
        return np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    if pixel_type == PIXEL_TYPE_RGB8_PACKED:
        raw = ctypes.string_at(buf_addr, width * height * 3)
        rgb = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
        return np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dtype=np.uint8)
    return np.asarray(lib.convert_to_bgr8(handle, frame_out), dtype=np.uint8)


class HikvisionCameraSource:
    """``VideoSource`` 的海康威视 MVS 工业相机实现，可无参构造。"""

    def __init__(
        self,
        *,
        sdk_path: str | None = None,
        pixel_format: str | None = None,
        trigger_mode: str = "Off",
        image_node_num: int = 4,
        transport: Literal["all", "gige", "usb"] = "all",
        sdk_loader: Callable[[str | Path | None], tuple[Any, HikvisionSdkLocation]] | None = None,
    ) -> None:
        """创建相机视频源，不在构造时加载 SDK 或连接硬件。

        Args:
            sdk_path: 可选的显式 SDK 路径或目录，为 None 时跨平台自动发现。
            pixel_format: 可选像素格式节点名（例如 ``Mono8``、``BayerRG8``）。
            trigger_mode: 触发模式节点名，默认 ``Off``（连续取流）。
            image_node_num: SDK 内部缓冲节点数量。
            transport: 枚举传输层：``all``、``gige`` 或 ``usb``。
            sdk_loader: 可注入的 SDK 加载器（单元测试用）。
        """
        self._sdk_path = sdk_path
        self._pixel_format = pixel_format
        self._trigger_mode = trigger_mode
        self._image_node_num = image_node_num
        self._transport = transport
        self._sdk_loader = sdk_loader or load_mvs_library

        self._lib: Any | None = None
        self._handle: Any | None = None
        self._device_opened = False
        self._grabbing = False
        self._config: VideoConfig | None = None
        self._sequence = 0

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开海康工业相机并启动取流。

        Args:
            config: 视频源配置，``device`` 支持 ``auto``、序列号、自定义名称、IP 或序号。

        Returns:
            相机实际生效的图像参数，``is_live`` 为 True。

        Raises:
            VideoOpenError: SDK 未找到、未检测到相机、参数越界或设备打开失败。
        """
        self.close()
        try:
            lib, location = self._sdk_loader(self._sdk_path)
            layer = {
                "gige": MV_GIGE_DEVICE,
                "usb": MV_USB_DEVICE,
                "all": MV_GIGE_DEVICE | MV_USB_DEVICE,
            }[self._transport]
            devices, dev_list = lib.enum_devices(layer)
        except HikvisionSdkError as exc:
            raise VideoOpenError(str(exc)) from exc

        if not devices:
            raise VideoOpenError(f"未发现海康工业相机（SDK：{location.path}）")

        selected = _select_device(devices, config.device)
        dev_info = dev_list.pDeviceInfo[selected.index].contents

        handle: Any | None = None
        try:
            handle = lib.create_handle(dev_info)
            self._lib = lib
            self._handle = handle
            lib.open_device(handle, MV_ACCESS_EXCLUSIVE)
            self._device_opened = True

            if selected.transport == "gige":
                packet_size = lib.get_optimal_packet_size(handle)
                if packet_size > 0:
                    try:
                        lib.set_int_value(handle, "GevSCPSPacketSize", packet_size)
                    except HikvisionSdkError:
                        _log.debug("设置 GigE 最佳包长失败，继续使用相机默认值")

            self._apply_camera_settings(lib, handle, config)
            lib.set_image_node_num(handle, self._image_node_num)
            lib.start_grabbing(handle)
            self._grabbing = True
            self._config = config
            self._sequence = 0

            width, _, _ = lib.get_int_value(handle, "Width")
            height, _, _ = lib.get_int_value(handle, "Height")
            fps = self._read_fps(lib, handle, config.fps)
            label = f"{selected.model_name} ({selected.serial_number or selected.index})".strip()
            return VideoInfo(
                width=width,
                height=height,
                fps=fps,
                is_live=True,
                description=f"Hikvision {label}",
            )
        except HikvisionSdkError as exc:
            self.close()
            raise VideoOpenError(str(exc)) from exc
        except Exception:
            self.close()
            raise

    def _apply_camera_settings(self, lib: Any, handle: Any, config: VideoConfig) -> None:
        try:
            lib.set_enum_value_by_string(handle, "TriggerMode", self._trigger_mode)
        except HikvisionSdkError:
            _log.debug("相机不支持设置 TriggerMode=%s，已忽略", self._trigger_mode)

        if self._pixel_format:
            lib.set_enum_value_by_string(handle, "PixelFormat", self._pixel_format)
        if config.width is not None:
            self._set_dimension(lib, handle, "Width", config.width)
        if config.height is not None:
            self._set_dimension(lib, handle, "Height", config.height)
        # 先设曝光再设帧率：长曝光会动态压低相机的最大允许帧率。
        if config.exposure_auto is not None:
            lib.set_enum_value_by_string(
                handle, "ExposureAuto", _EXPOSURE_AUTO_MAP[config.exposure_auto]
            )
        if config.exposure_us is not None and config.exposure_auto in (None, "off"):
            if config.exposure_auto is None:
                with contextlib.suppress(HikvisionSdkError):
                    lib.set_enum_value_by_string(handle, "ExposureAuto", "Off")
            _, min_us, max_us = lib.get_float_value(handle, "ExposureTime")
            if not min_us <= config.exposure_us <= max_us:
                raise VideoOpenError(
                    f"曝光时间 {config.exposure_us:g} µs 超出相机范围（{min_us:g}–{max_us:g} µs）"
                )
            lib.set_float_value(handle, "ExposureTime", config.exposure_us)
        if config.fps is not None:
            self._apply_fps(lib, handle, config.fps)

    @staticmethod
    def _set_dimension(lib: Any, handle: Any, key: str, value: int) -> None:
        try:
            lib.set_int_value(handle, key, value)
        except HikvisionSdkError as exc:
            try:
                _, min_val, max_val = lib.get_int_value(handle, key)
            except HikvisionSdkError:
                raise exc from None
            raise VideoOpenError(
                f"相机 {key}={value} 超出支持范围或步进无效（{min_val}–{max_val}）"
            ) from exc

    @staticmethod
    def _apply_fps(lib: Any, handle: Any, target_fps: float) -> None:
        with contextlib.suppress(HikvisionSdkError):
            lib.set_bool_value(handle, "AcquisitionFrameRateEnable", True)
        applied = float(target_fps)
        try:
            _, min_fps, max_fps = lib.get_float_value(handle, "AcquisitionFrameRate")
            if min_fps <= max_fps and not min_fps <= applied <= max_fps:
                clamped = min(max(applied, min_fps), max_fps)
                _log.warning(
                    "目标帧率 %.2f fps 超出当前曝光下相机范围（%.2f–%.2f fps），"
                    "已自动调整为 %.2f fps",
                    applied,
                    min_fps,
                    max_fps,
                    clamped,
                )
                applied = clamped
        except HikvisionSdkError:
            pass
        try:
            lib.set_float_value(handle, "AcquisitionFrameRate", applied)
        except HikvisionSdkError as exc:
            _log.warning("设置相机帧率 %.2f fps 失败，沿用相机默认帧率：%s", applied, exc)

    @staticmethod
    def _read_fps(lib: Any, handle: Any, fallback: float | None) -> float | None:
        for key in ("ResultingFrameRate", "AcquisitionFrameRate"):
            try:
                cur, _, _ = lib.get_float_value(handle, key)
                if cur > 0:
                    return float(cur)
            except HikvisionSdkError:
                continue
        return fallback

    def read(self) -> VideoFrame | None:
        """读取下一帧 BGR 图像；超时返回 None，断流抛 ``VideoDisconnectedError``。"""
        if not self._grabbing or self._lib is None or self._handle is None or self._config is None:
            raise VideoNotOpenError("海康相机未打开或已关闭")
        lib = self._lib
        handle = self._handle
        frame_out = MV_FRAME_OUT()
        code = lib.get_image_buffer(handle, frame_out, self._config.read_timeout_ms)
        captured_at = datetime.now(UTC)
        monotonic_s = time.monotonic()

        if code in TIMEOUT_ERRORS:
            if not lib.is_device_connected(handle):
                raise VideoDisconnectedError("海康相机连接已断开")
            return None
        if code != MV_OK:
            raise VideoDisconnectedError(f"海康相机取帧失败（错误码 0x{code:08X}）")

        try:
            image = _copy_bgr(lib, handle, frame_out)
        finally:
            lib.free_image_buffer(handle, frame_out)

        frame = VideoFrame(
            image=image,
            sequence=self._sequence,
            captured_at=captured_at,
            monotonic_s=monotonic_s,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        """停止取流并释放相机句柄，可重复调用。"""
        lib, self._lib = self._lib, None
        handle, self._handle = self._handle, None
        grabbing, self._grabbing = self._grabbing, False
        opened, self._device_opened = self._device_opened, False
        self._config = None
        if lib is None or handle is None:
            return
        try:
            if grabbing:
                try:
                    lib.stop_grabbing(handle)
                except Exception:
                    _log.exception("停止海康相机取流失败（已忽略）")
            if opened:
                try:
                    lib.close_device(handle)
                except Exception:
                    _log.exception("关闭海康相机设备失败（已忽略）")
        finally:
            try:
                lib.destroy_handle(handle)
            except Exception:
                _log.exception("销毁海康相机句柄失败（已忽略）")
