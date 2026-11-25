"""海康威视 MVS SDK 跨平台自动发现与 HikvisionCameraSource 离线单元测试。"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gelinspect.infrastructure.video.factory import create_video_source
from gelinspect.infrastructure.video.hikvision_sdk import (
    MV_E_GC_TIMEOUT,
    MV_E_NETER,
    MV_OK,
    PIXEL_TYPE_BGR8_PACKED,
    PIXEL_TYPE_MONO8,
    PIXEL_TYPE_RGB8_PACKED,
    HikvisionCameraDevice,
    HikvisionSdkLocation,
    HikvisionSdkNotFoundError,
    discover_sdk_candidates,
    find_sdk_library,
    probe_hikvision_sdk,
)
from gelinspect.infrastructure.video.hikvision_source import HikvisionCameraSource
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoNotOpenError,
    VideoOpenError,
    VideoSource,
)


def test_discover_sdk_candidates_windows_and_macos(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    env_win = {
        "MVCAM_COMMON_RUNENV": r"D:\MVS\Common",
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "ProgramFiles": r"C:\Program Files",
        "GELINSPECT_HIKVISION_SDK": str(tmp_path / "override"),
    }
    win_candidates = discover_sdk_candidates(platform="win32", environ=env_win, app_dir=app_dir)
    win_paths = [str(c.path) for c in win_candidates]
    assert win_candidates[0].source == "env:GELINSPECT_HIKVISION_SDK"
    assert any(c.source == "env:MVCAM_COMMON_RUNENV" for c in win_candidates)
    assert any("MvCameraControl.dll" in p for p in win_paths)

    mac_candidates = discover_sdk_candidates(
        platform="darwin", environ={"HOME": "/Users/tester"}, app_dir=app_dir
    )
    mac_paths = [str(c.path) for c in mac_candidates]
    assert "/Library/Frameworks/MvCameraControl.framework/MvCameraControl" in mac_paths
    assert "/Users/tester/Library/Frameworks/MvCameraControl.framework/MvCameraControl" in mac_paths


def test_find_sdk_library_prefers_explicit_then_app_bundled(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    bundled = app_dir / "sdk" / "MvCameraControl"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"stub")

    found_bundled = find_sdk_library(platform="darwin", environ={}, app_dir=app_dir)
    assert found_bundled.path == bundled
    assert found_bundled.source == "app_bundled"

    explicit = tmp_path / "custom" / "MvCameraControl"
    explicit.parent.mkdir(parents=True)
    explicit.write_bytes(b"stub")
    found_explicit = find_sdk_library(explicit, platform="darwin", environ={}, app_dir=app_dir)
    assert found_explicit.path == explicit
    assert found_explicit.source == "config:sdk_path"


def test_find_sdk_library_raises_clear_error_when_missing(tmp_path: Path) -> None:
    with pytest.raises(HikvisionSdkNotFoundError, match="未找到海康 MVS SDK"):
        find_sdk_library(
            tmp_path / "missing.dll",
            platform="win32",
            environ={},
            app_dir=tmp_path / "empty_app",
        )


def test_local_macos_mvs_sdk_probe_if_installed() -> None:
    framework = Path("/Library/Frameworks/MvCameraControl.framework/MvCameraControl")
    if not framework.is_file():
        pytest.skip("本机未安装海康 MVS framework")
    info = probe_hikvision_sdk()
    assert info["found"] is True
    assert info["path"] == str(framework)
    assert isinstance(info["version"], str) and "." in info["version"]


class _FakeDevInfo:
    def __init__(self) -> None:
        self.contents = object()


class _FakeDevList:
    def __init__(self, count: int) -> None:
        self.pDeviceInfo = [_FakeDevInfo() for _ in range(count)]


class _FakeMvsLibrary:
    def __init__(self, *, pixel_type: int = PIXEL_TYPE_BGR8_PACKED) -> None:
        self.pixel_type = pixel_type
        self.devices = [
            HikvisionCameraDevice(
                index=0,
                transport="gige",
                serial_number="SN-GIGE-001",
                model_name="MV-CA060-10GC",
                manufacturer="Hikvision",
                user_defined_name="LeftCam",
                ip_address="192.168.1.10",
            ),
            HikvisionCameraDevice(
                index=1,
                transport="usb",
                serial_number="SN-USB-002",
                model_name="MV-CE120-10UM",
                manufacturer="Hikvision",
                user_defined_name="MainCam",
                ip_address=None,
            ),
        ]
        self.set_calls: list[tuple[str, Any]] = []
        self.connected = True
        self.next_code = MV_OK
        self.grabbing = False
        self.opened = False
        self.destroyed = False
        self._buf: Any = None
        self._exposure_range = (20.0, 100000.0)

    def enum_devices(self, _layer: int) -> tuple[list[HikvisionCameraDevice], _FakeDevList]:
        return list(self.devices), _FakeDevList(len(self.devices))

    def create_handle(self, _info: Any) -> object:
        return object()

    def destroy_handle(self, _handle: Any) -> None:
        self.destroyed = True

    def open_device(self, _handle: Any, _mode: int) -> None:
        self.opened = True

    def close_device(self, _handle: Any) -> None:
        self.opened = False

    def is_device_connected(self, _handle: Any) -> bool:
        return self.connected

    def get_optimal_packet_size(self, _handle: Any) -> int:
        return 8192

    def set_image_node_num(self, _handle: Any, num: int) -> None:
        self.set_calls.append(("ImageNodeNum", num))

    def start_grabbing(self, _handle: Any) -> None:
        self.grabbing = True

    def stop_grabbing(self, _handle: Any) -> None:
        self.grabbing = False

    def get_int_value(self, _handle: Any, key: str) -> tuple[int, int, int]:
        if key == "Width":
            return 64, 16, 4000
        if key == "Height":
            return 48, 16, 3000
        return 0, 0, 0

    def set_int_value(self, _handle: Any, key: str, value: int) -> None:
        self.set_calls.append((key, value))

    def get_float_value(self, _handle: Any, key: str) -> tuple[float, float, float]:
        if key == "ExposureTime":
            return 5000.0, self._exposure_range[0], self._exposure_range[1]
        if key in ("ResultingFrameRate", "AcquisitionFrameRate"):
            return 15.0, 1.0, 60.0
        return 0.0, 0.0, 0.0

    def set_float_value(self, _handle: Any, key: str, value: float) -> None:
        self.set_calls.append((key, value))

    def set_enum_value_by_string(self, _handle: Any, key: str, value: str) -> None:
        self.set_calls.append((key, value))

    def set_bool_value(self, _handle: Any, key: str, value: bool) -> None:
        self.set_calls.append((key, value))

    def get_image_buffer(self, _handle: Any, frame_out: Any, _timeout_ms: int) -> int:
        if self.next_code != MV_OK:
            return self.next_code
        w, h = 64, 48
        if self.pixel_type == PIXEL_TYPE_MONO8:
            arr = np.full((h, w), 123, dtype=np.uint8)
        elif self.pixel_type == PIXEL_TYPE_RGB8_PACKED:
            arr = np.zeros((h, w, 3), dtype=np.uint8)
            arr[:, :, 0] = 10
            arr[:, :, 2] = 200
        else:
            arr = np.zeros((h, w, 3), dtype=np.uint8)
            arr[:, :, 0] = 200
            arr[:, :, 2] = 10
        self._buf = ctypes.create_string_buffer(arr.tobytes())
        frame_out.pBufAddr = ctypes.addressof(self._buf)
        frame_out.stFrameInfo.nWidth = w
        frame_out.stFrameInfo.nHeight = h
        frame_out.stFrameInfo.enPixelType = self.pixel_type
        frame_out.stFrameInfo.nFrameLen = len(arr.tobytes())
        return MV_OK

    def free_image_buffer(self, _handle: Any, _frame_out: Any) -> int:
        return MV_OK

    def convert_to_bgr8(self, _handle: Any, _frame_out: Any) -> np.ndarray:
        return np.full((48, 64, 3), 77, dtype=np.uint8)


def _loader_for(lib: _FakeMvsLibrary) -> Any:
    loc = HikvisionSdkLocation(Path("/fake/MvCameraControl"), "test", "darwin")
    return lambda _p: (lib, loc)


def test_factory_creates_hikvision_source_without_loading_sdk() -> None:
    source = create_video_source("hikvision")
    assert isinstance(source, HikvisionCameraSource)
    assert isinstance(source, VideoSource)


@pytest.mark.parametrize(
    "pixel_type",
    [PIXEL_TYPE_BGR8_PACKED, PIXEL_TYPE_MONO8, PIXEL_TYPE_RGB8_PACKED, 0x01080009],
)
def test_hikvision_source_lifecycle_and_pixel_conversions(pixel_type: int) -> None:
    lib = _FakeMvsLibrary(pixel_type=pixel_type)
    source = HikvisionCameraSource(sdk_loader=_loader_for(lib))
    with pytest.raises(VideoNotOpenError):
        source.read()

    info = source.open(
        VideoConfig(
            device="auto",
            fps=15.0,
            exposure_auto="off",
            exposure_us=6000.0,
        )
    )
    assert info.is_live is True
    assert info.width == 64 and info.height == 48
    assert ("GevSCPSPacketSize", 8192) in lib.set_calls
    assert ("ExposureAuto", "Off") in lib.set_calls
    assert ("ExposureTime", 6000.0) in lib.set_calls

    frame = source.read()
    assert frame is not None
    assert frame.image.shape == (48, 64, 3)
    assert frame.image.dtype == np.uint8
    if pixel_type in (PIXEL_TYPE_BGR8_PACKED, PIXEL_TYPE_RGB8_PACKED):
        assert tuple(frame.image[0, 0]) == (200, 0, 10)
    elif pixel_type == PIXEL_TYPE_MONO8:
        assert tuple(frame.image[0, 0]) == (123, 123, 123)
    else:
        assert tuple(frame.image[0, 0]) == (77, 77, 77)

    # 超时但设备在线 → 返回 None
    lib.next_code = MV_E_GC_TIMEOUT
    assert source.read() is None

    # 超时且设备掉线 → 抛 VideoDisconnectedError
    lib.connected = False
    with pytest.raises(VideoDisconnectedError):
        source.read()

    # 显式网络错误 → 抛 VideoDisconnectedError
    lib.next_code = MV_E_NETER
    with pytest.raises(VideoDisconnectedError):
        source.read()

    source.close()
    source.close()  # 幂等
    assert lib.destroyed is True


def test_hikvision_source_selects_by_serial_and_rejects_bad_exposure() -> None:
    lib = _FakeMvsLibrary()
    source = HikvisionCameraSource(sdk_loader=_loader_for(lib))
    info = source.open(VideoConfig(device="SN-USB-002"))
    assert "SN-USB-002" in info.description
    source.close()

    with pytest.raises(VideoOpenError, match="未找到匹配"):
        source.open(VideoConfig(device="NON-EXISTENT"))

    with pytest.raises(VideoOpenError, match="超出相机范围"):
        source.open(VideoConfig(device="auto", exposure_auto="off", exposure_us=999999.0))
