"""海康威视 MVS 相机 SDK 跨平台自动发现与 ctypes 封装。

设计原则：
- 模块导入时不加载原生库，不触碰硬件；
- 支持 Windows（``MvCameraControl.dll``）、macOS（``MvCameraControl.framework``）与
  Linux（``libMvCameraControl.so``）自动发现，无须手工拷贝厂商 ``MvImport`` 脚本；
- 全部结构体使用固定宽度整数类型（禁用 ``c_long``），并按 MVS 4.7 头文件对齐联合体，
  防止 macOS（LP64）与 Windows（LLP64）之间出现 ABI 错位。
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import ctypes.util
import os
import platform as platform_info
import struct
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.video import VideoError

MV_OK = 0x00000000
MV_GIGE_DEVICE = 0x00000001
MV_USB_DEVICE = 0x00000004
MV_ACCESS_EXCLUSIVE = 1

INFO_MAX_BUFFER_SIZE = 64
MV_MAX_DEVICE_NUM = 256
MV_MAX_XML_SYMBOLIC_NUM = 64
_SPECIAL_INFO_UNION_BYTES = 604  # _MV_GENTL_VIR_DEVICE_INFO_ 占 9*64 + 7*4 = 604 字节

PIXEL_TYPE_MONO8 = 0x01080001
PIXEL_TYPE_RGB8_PACKED = 0x02180014
PIXEL_TYPE_BGR8_PACKED = 0x02180015

MV_E_HANDLE = 0x80000000
MV_E_NODATA = 0x80000007
MV_E_NOOUTBUF = 0x8000000D
MV_E_GC_TIMEOUT = 0x80000107
MV_E_ACCESS_DENIED = 0x80000203
MV_E_NETER = 0x80000206
MV_E_USB_READ = 0x80000300
MV_E_USB_WRITE = 0x80000301
MV_E_USB_DEVICE = 0x80000302
MV_E_USB_DRIVER = 0x80000305
MV_E_USB_UNKNOW = 0x800003FF

TIMEOUT_ERRORS = frozenset({MV_E_NODATA, MV_E_GC_TIMEOUT, MV_E_NOOUTBUF})
"""取图超时的错误码集合（设备仍可能在线，应返回 None 并继续等待）。"""

DISCONNECT_ERRORS = frozenset(
    {
        MV_E_HANDLE,
        MV_E_NETER,
        MV_E_USB_READ,
        MV_E_USB_WRITE,
        MV_E_USB_DEVICE,
        MV_E_USB_DRIVER,
        MV_E_USB_UNKNOW,
    }
)
"""表明设备断流或连接失效的错误码集合。"""

SDK_ENV_VAR = "GELINSPECT_HIKVISION_SDK"
"""显式覆盖海康 MVS SDK 动态库路径（文件或目录）的环境变量。"""

_INIT_LOCK = threading.Lock()
_INITIALIZED_LIBS: set[int] = set()
_FINALIZE_REGISTERED = False
_DLL_DIR_HANDLES: list[object] = []


class HikvisionSdkError(VideoError):
    """海康 MVS SDK 调用或加载失败。"""


class HikvisionSdkNotFoundError(HikvisionSdkError):
    """未找到海康 MVS SDK 动态库。"""


@dataclass(frozen=True, slots=True)
class HikvisionSdkLocation:
    """发现到的海康 MVS SDK 位置。"""

    path: Path
    source: str
    platform: str


@dataclass(frozen=True, slots=True)
class HikvisionCameraDevice:
    """枚举到的一台海康工业相机。"""

    index: int
    transport: Literal["gige", "usb", "other"]
    serial_number: str
    model_name: str
    manufacturer: str
    user_defined_name: str
    ip_address: str | None = None


# ---------------------------------------------------------------------- C ABI 结构体


class MV_GIGE_DEVICE_INFO(ctypes.Structure):  # noqa: N801
    """GigE 设备信息。"""

    _fields_ = [
        ("nIpCfgOption", ctypes.c_uint32),
        ("nIpCfgCurrent", ctypes.c_uint32),
        ("nCurrentIp", ctypes.c_uint32),
        ("nCurrentSubNetMask", ctypes.c_uint32),
        ("nDefultGateWay", ctypes.c_uint32),
        ("chManufacturerName", ctypes.c_uint8 * 32),
        ("chModelName", ctypes.c_uint8 * 32),
        ("chDeviceVersion", ctypes.c_uint8 * 32),
        ("chManufacturerSpecificInfo", ctypes.c_uint8 * 48),
        ("chSerialNumber", ctypes.c_uint8 * 16),
        ("chUserDefinedName", ctypes.c_uint8 * 16),
        ("nNetExport", ctypes.c_uint32),
        ("nReserved", ctypes.c_uint32 * 4),
    ]


class MV_USB3_DEVICE_INFO(ctypes.Structure):  # noqa: N801
    """USB3 Vision 设备信息。"""

    _fields_ = [
        ("CrtlInEndPoint", ctypes.c_uint8),
        ("CrtlOutEndPoint", ctypes.c_uint8),
        ("StreamEndPoint", ctypes.c_uint8),
        ("EventEndPoint", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("nDeviceNumber", ctypes.c_uint32),
        ("chDeviceGUID", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chVendorName", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chModelName", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chFamilyName", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chDeviceVersion", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chManufacturerName", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chSerialNumber", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("chUserDefinedName", ctypes.c_uint8 * INFO_MAX_BUFFER_SIZE),
        ("nbcdUSB", ctypes.c_uint32),
        ("nDeviceAddress", ctypes.c_uint32),
        ("nReserved", ctypes.c_uint32 * 2),
    ]


class _SpecialInfoUnion(ctypes.Union):
    _fields_ = [
        ("stGigEInfo", MV_GIGE_DEVICE_INFO),
        ("stUsb3VInfo", MV_USB3_DEVICE_INFO),
        ("raw_padding", ctypes.c_uint8 * _SPECIAL_INFO_UNION_BYTES),
    ]


class MV_CC_DEVICE_INFO(ctypes.Structure):  # noqa: N801
    """通用相机设备信息。"""

    _fields_ = [
        ("nMajorVer", ctypes.c_uint16),
        ("nMinorVer", ctypes.c_uint16),
        ("nMacAddrHigh", ctypes.c_uint32),
        ("nMacAddrLow", ctypes.c_uint32),
        ("nTLayerType", ctypes.c_uint32),
        ("nDevTypeInfo", ctypes.c_uint32),
        ("nReserved", ctypes.c_uint32 * 3),
        ("SpecialInfo", _SpecialInfoUnion),
    ]


class MV_CC_DEVICE_INFO_LIST(ctypes.Structure):  # noqa: N801
    """枚举到的设备列表。"""

    _fields_ = [
        ("nDeviceNum", ctypes.c_uint32),
        ("pDeviceInfo", ctypes.POINTER(MV_CC_DEVICE_INFO) * MV_MAX_DEVICE_NUM),
    ]


class _AligningPtrUnion(ctypes.Union):
    _fields_ = [
        ("pPtr", ctypes.c_void_p),
        ("nAligning", ctypes.c_int64),
    ]


class MV_FRAME_OUT_INFO_EX(ctypes.Structure):  # noqa: N801
    """输出帧头信息。"""

    _fields_ = [
        ("nWidth", ctypes.c_uint16),
        ("nHeight", ctypes.c_uint16),
        ("enPixelType", ctypes.c_uint32),
        ("nFrameNum", ctypes.c_uint32),
        ("nDevTimeStampHigh", ctypes.c_uint32),
        ("nDevTimeStampLow", ctypes.c_uint32),
        ("nReserved0", ctypes.c_uint32),
        ("nHostTimeStamp", ctypes.c_int64),
        ("nFrameLen", ctypes.c_uint32),
        ("nSecondCount", ctypes.c_uint32),
        ("nCycleCount", ctypes.c_uint32),
        ("nCycleOffset", ctypes.c_uint32),
        ("fGain", ctypes.c_float),
        ("fExposureTime", ctypes.c_float),
        ("nAverageBrightness", ctypes.c_uint32),
        ("nRed", ctypes.c_uint32),
        ("nGreen", ctypes.c_uint32),
        ("nBlue", ctypes.c_uint32),
        ("nFrameCounter", ctypes.c_uint32),
        ("nTriggerIndex", ctypes.c_uint32),
        ("nInput", ctypes.c_uint32),
        ("nOutput", ctypes.c_uint32),
        ("nOffsetX", ctypes.c_uint16),
        ("nOffsetY", ctypes.c_uint16),
        ("nChunkWidth", ctypes.c_uint16),
        ("nChunkHeight", ctypes.c_uint16),
        ("nLostPacket", ctypes.c_uint32),
        ("nUnparsedChunkNum", ctypes.c_uint32),
        ("UnparsedChunkList", _AligningPtrUnion),
        ("nExtendWidth", ctypes.c_uint32),
        ("nExtendHeight", ctypes.c_uint32),
        ("nFrameLenEx", ctypes.c_uint64),
        ("nExtraType", ctypes.c_uint32),
        ("nSubImageNum", ctypes.c_uint32),
        ("SubImageList", _AligningPtrUnion),
        ("UserPtr", _AligningPtrUnion),
        ("nFirstLineEncoderCount", ctypes.c_uint32),
        ("nLastLineEncoderCount", ctypes.c_uint32),
        ("nReserved", ctypes.c_uint32 * 24),
    ]


class MV_FRAME_OUT(ctypes.Structure):  # noqa: N801
    """``MV_CC_GetImageBuffer`` 输出帧结构。"""

    _fields_ = [
        ("pBufAddr", ctypes.c_void_p),
        ("stFrameInfo", MV_FRAME_OUT_INFO_EX),
        ("nRes", ctypes.c_uint32 * 16),
    ]


class MV_CC_PIXEL_CONVERT_PARAM_EX(ctypes.Structure):  # noqa: N801
    """MVS v4 像素格式转换参数。"""

    _fields_ = [
        ("nWidth", ctypes.c_uint32),
        ("nHeight", ctypes.c_uint32),
        ("enSrcPixelType", ctypes.c_uint32),
        ("pSrcData", ctypes.c_void_p),
        ("nSrcDataLen", ctypes.c_uint32),
        ("enDstPixelType", ctypes.c_uint32),
        ("pDstBuffer", ctypes.c_void_p),
        ("nDstLen", ctypes.c_uint32),
        ("nDstBufferSize", ctypes.c_uint32),
        ("nRes", ctypes.c_uint32 * 4),
    ]


class MV_CC_PIXEL_CONVERT_PARAM(ctypes.Structure):  # noqa: N801
    """兼容旧版 MVS v3 像素格式转换参数。"""

    _fields_ = [
        ("nWidth", ctypes.c_uint16),
        ("nHeight", ctypes.c_uint16),
        ("enSrcPixelType", ctypes.c_uint32),
        ("pSrcData", ctypes.c_void_p),
        ("nSrcDataLen", ctypes.c_uint32),
        ("enDstPixelType", ctypes.c_uint32),
        ("pDstBuffer", ctypes.c_void_p),
        ("nDstLen", ctypes.c_uint32),
        ("nDstBufferSize", ctypes.c_uint32),
        ("nRes", ctypes.c_uint32 * 4),
    ]


class MVCC_INTVALUE_EX(ctypes.Structure):  # noqa: N801
    """64 位整型节点值。"""

    _fields_ = [
        ("nCurValue", ctypes.c_int64),
        ("nMax", ctypes.c_int64),
        ("nMin", ctypes.c_int64),
        ("nInc", ctypes.c_int64),
        ("nReserved", ctypes.c_uint32 * 16),
    ]


class MVCC_FLOATVALUE(ctypes.Structure):  # noqa: N801
    """浮点型节点值。"""

    _fields_ = [
        ("fCurValue", ctypes.c_float),
        ("fMax", ctypes.c_float),
        ("fMin", ctypes.c_float),
        ("nReserved", ctypes.c_uint32 * 4),
    ]


class MVCC_ENUMVALUE(ctypes.Structure):  # noqa: N801
    """枚举型节点值。"""

    _fields_ = [
        ("nCurValue", ctypes.c_uint32),
        ("nSupportedNum", ctypes.c_uint32),
        ("nSupportValue", ctypes.c_uint32 * MV_MAX_XML_SYMBOLIC_NUM),
        ("nReserved", ctypes.c_uint32 * 4),
    ]


# ---------------------------------------------------------------------- SDK 自动发现


def _default_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if "__compiled__" in globals():
        return Path(__file__).resolve().parents[3]
    return Path(__file__).resolve().parents[4]


def _library_candidates_in_dir(directory: Path, platform: str) -> list[Path]:
    if platform == "win32":
        arch = "Win64_x64" if struct.calcsize("P") == 8 else "Win32_i86"
        return [
            directory / "MvCameraControl.dll",
            directory / arch / "MvCameraControl.dll",
        ]
    if platform == "darwin":
        return [
            directory / "MvCameraControl",
            directory / "MvCameraControl.framework" / "MvCameraControl",
        ]
    return [directory / "libMvCameraControl.so"]


def _resolve_explicit_or_env(raw: str | Path, platform: str) -> list[Path]:
    path = Path(raw).expanduser()
    if path.suffix in (".dll", ".so", ".dylib") or path.name == "MvCameraControl":
        return [path]
    if path.name.endswith(".framework"):
        return [path / "MvCameraControl"]
    return _library_candidates_in_dir(path, platform)


def discover_sdk_candidates(
    explicit_path: str | Path | None = None,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    app_dir: Path | None = None,
) -> list[HikvisionSdkLocation]:
    """按优先级生成海康 MVS SDK 候选路径。

    Args:
        explicit_path: 显式传入的库路径或所在目录。
        platform: 目标平台标识，默认 ``sys.platform``。
        environ: 环境变量映射，默认 ``os.environ``。
        app_dir: 应用根目录（用于查找随包放置的 ``sdk/``）。

    Returns:
        按优先级去重后的候选位置列表。
    """
    target_platform = platform or sys.platform
    env = environ if environ is not None else os.environ
    root = app_dir if app_dir is not None else _default_app_dir()
    is_64bit = struct.calcsize("P") == 8
    raw_candidates: list[tuple[Path, str]] = []

    if explicit_path is not None and str(explicit_path).strip():
        for p in _resolve_explicit_or_env(explicit_path, target_platform):
            raw_candidates.append((p, "config:sdk_path"))

    for sub in (root / "sdk", root):
        for p in _library_candidates_in_dir(sub, target_platform):
            raw_candidates.append((p, "app_bundled"))

    env_path = env.get(SDK_ENV_VAR, "").strip()
    if env_path:
        for p in _resolve_explicit_or_env(env_path, target_platform):
            raw_candidates.append((p, f"env:{SDK_ENV_VAR}"))

    if target_platform == "win32":
        arch_dir = "Win64_x64" if is_64bit else "Win32_i86"
        dev_arch = "win64" if is_64bit else "win32"
        runenv = env.get("MVCAM_COMMON_RUNENV", "").strip()
        if runenv:
            raw_candidates.append(
                (Path(runenv) / arch_dir / "MvCameraControl.dll", "env:MVCAM_COMMON_RUNENV")
            )
        pf_x86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        pf = env.get("ProgramFiles", r"C:\Program Files")
        raw_candidates.extend(
            [
                (
                    Path(pf_x86)
                    / "Common Files"
                    / "MVS"
                    / "Runtime"
                    / arch_dir
                    / "MvCameraControl.dll",
                    "windows_common_runtime",
                ),
                (
                    Path(pf)
                    / "Common Files"
                    / "MVS"
                    / "Runtime"
                    / arch_dir
                    / "MvCameraControl.dll",
                    "windows_common_runtime",
                ),
                (
                    Path(pf_x86) / "MVS" / "Development" / "Bin" / dev_arch / "MvCameraControl.dll",
                    "windows_mvs_dev",
                ),
            ]
        )
    elif target_platform == "darwin":
        home = Path(env.get("HOME", str(Path.home())))
        raw_candidates.extend(
            [
                (
                    Path("/Library/Frameworks/MvCameraControl.framework/MvCameraControl"),
                    "macos_system_framework",
                ),
                (
                    home / "Library/Frameworks/MvCameraControl.framework/MvCameraControl",
                    "macos_user_framework",
                ),
                (
                    Path(
                        "/Applications/MVS.app/Contents/Frameworks/"
                        "MvCameraControl.framework/MvCameraControl"
                    ),
                    "macos_app_bundle",
                ),
            ]
        )
    else:
        arch_sub = "aarch64" if platform_info.machine().lower() in ("aarch64", "arm64") else "64"
        raw_candidates.extend(
            [
                (Path(f"/opt/MVS/lib/{arch_sub}/libMvCameraControl.so"), "linux_opt_mvs"),
                (Path("/opt/MVS/lib/64/libMvCameraControl.so"), "linux_opt_mvs"),
                (Path("/opt/MVS/lib/32/libMvCameraControl.so"), "linux_opt_mvs"),
            ]
        )

    if target_platform == sys.platform:
        found = ctypes.util.find_library("MvCameraControl")
        if found:
            raw_candidates.append((Path(found), "system_find_library"))

    seen: set[str] = set()
    locations: list[HikvisionSdkLocation] = []
    for candidate, source in raw_candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        locations.append(
            HikvisionSdkLocation(path=candidate, source=source, platform=target_platform)
        )
    return locations


def find_sdk_library(
    explicit_path: str | Path | None = None,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    app_dir: Path | None = None,
) -> HikvisionSdkLocation:
    """查找实际存在且可读的海康 MVS SDK 动态库。

    Raises:
        HikvisionSdkNotFoundError: 所有候选路径均不存在。
    """
    candidates = discover_sdk_candidates(
        explicit_path, platform=platform, environ=environ, app_dir=app_dir
    )
    for loc in candidates:
        if loc.path.is_file():
            return loc
    searched = "；".join(str(loc.path) for loc in candidates)
    raise HikvisionSdkNotFoundError(
        f"未找到海康 MVS SDK 动态库。请安装海康 MVS Runtime，或通过环境变量 {SDK_ENV_VAR} / "
        f"配置项 video.options.sdk_path 指定路径。已尝试：{searched}"
    )


def _u32(code: int) -> int:
    return code & 0xFFFFFFFF


def _decode_cstr(buf: Any) -> str:
    raw = bytes(buf)
    nul = raw.find(0)
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("utf-8", errors="replace").strip()


def _format_ip(ip_int: int) -> str:
    return f"{(ip_int >> 24) & 0xFF}.{(ip_int >> 16) & 0xFF}.{(ip_int >> 8) & 0xFF}.{ip_int & 0xFF}"


def _register_finalize(dll: Any) -> None:
    global _FINALIZE_REGISTERED
    finalize = getattr(dll, "MV_CC_Finalize", None)
    if finalize is None:
        return
    with _INIT_LOCK:
        lib_id = id(dll)
        if lib_id not in _INITIALIZED_LIBS:
            init = getattr(dll, "MV_CC_Initialize", None)
            if init is not None:
                init.restype = ctypes.c_int32
                init()
            _INITIALIZED_LIBS.add(lib_id)
        if not _FINALIZE_REGISTERED:
            _FINALIZE_REGISTERED = True

            def _cleanup() -> None:
                for _ in list(_INITIALIZED_LIBS):
                    with contextlib.suppress(Exception):
                        finalize()

            atexit.register(_cleanup)


class MvsLibrary:
    """海康 MVS C API 的轻量封装。"""

    def __init__(self, dll: Any, location: HikvisionSdkLocation) -> None:
        """初始化库封装并配置 C 函数签名。"""
        self._dll = dll
        self.location = location
        self._configure_prototypes()
        _register_finalize(dll)

    def _configure_prototypes(self) -> None:
        d = self._dll
        d.MV_CC_GetSDKVersion.restype = ctypes.c_uint32
        d.MV_CC_EnumDevices.argtypes = [ctypes.c_uint32, ctypes.POINTER(MV_CC_DEVICE_INFO_LIST)]
        d.MV_CC_EnumDevices.restype = ctypes.c_int32
        d.MV_CC_CreateHandle.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(MV_CC_DEVICE_INFO),
        ]
        d.MV_CC_CreateHandle.restype = ctypes.c_int32
        d.MV_CC_DestroyHandle.argtypes = [ctypes.c_void_p]
        d.MV_CC_DestroyHandle.restype = ctypes.c_int32
        d.MV_CC_OpenDevice.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint16]
        d.MV_CC_OpenDevice.restype = ctypes.c_int32
        d.MV_CC_CloseDevice.argtypes = [ctypes.c_void_p]
        d.MV_CC_CloseDevice.restype = ctypes.c_int32
        d.MV_CC_IsDeviceConnected.argtypes = [ctypes.c_void_p]
        d.MV_CC_IsDeviceConnected.restype = ctypes.c_bool
        d.MV_CC_StartGrabbing.argtypes = [ctypes.c_void_p]
        d.MV_CC_StartGrabbing.restype = ctypes.c_int32
        d.MV_CC_StopGrabbing.argtypes = [ctypes.c_void_p]
        d.MV_CC_StopGrabbing.restype = ctypes.c_int32
        d.MV_CC_GetImageBuffer.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(MV_FRAME_OUT),
            ctypes.c_uint32,
        ]
        d.MV_CC_GetImageBuffer.restype = ctypes.c_int32
        d.MV_CC_FreeImageBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(MV_FRAME_OUT)]
        d.MV_CC_FreeImageBuffer.restype = ctypes.c_int32
        if hasattr(d, "MV_CC_GetOptimalPacketSize"):
            d.MV_CC_GetOptimalPacketSize.argtypes = [ctypes.c_void_p]
            d.MV_CC_GetOptimalPacketSize.restype = ctypes.c_int32
        if hasattr(d, "MV_CC_SetImageNodeNum"):
            d.MV_CC_SetImageNodeNum.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            d.MV_CC_SetImageNodeNum.restype = ctypes.c_int32
        if hasattr(d, "MV_CC_ConvertPixelTypeEx"):
            d.MV_CC_ConvertPixelTypeEx.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(MV_CC_PIXEL_CONVERT_PARAM_EX),
            ]
            d.MV_CC_ConvertPixelTypeEx.restype = ctypes.c_int32
        elif hasattr(d, "MV_CC_ConvertPixelType"):
            d.MV_CC_ConvertPixelType.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(MV_CC_PIXEL_CONVERT_PARAM),
            ]
            d.MV_CC_ConvertPixelType.restype = ctypes.c_int32
        d.MV_CC_GetIntValueEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(MVCC_INTVALUE_EX),
        ]
        d.MV_CC_GetIntValueEx.restype = ctypes.c_int32
        d.MV_CC_SetIntValueEx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int64]
        d.MV_CC_SetIntValueEx.restype = ctypes.c_int32
        d.MV_CC_GetFloatValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(MVCC_FLOATVALUE),
        ]
        d.MV_CC_GetFloatValue.restype = ctypes.c_int32
        d.MV_CC_SetFloatValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_float]
        d.MV_CC_SetFloatValue.restype = ctypes.c_int32
        d.MV_CC_SetEnumValueByString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        d.MV_CC_SetEnumValueByString.restype = ctypes.c_int32
        if hasattr(d, "MV_CC_SetBoolValue"):
            d.MV_CC_SetBoolValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
            d.MV_CC_SetBoolValue.restype = ctypes.c_int32

    def sdk_version(self) -> str:
        """返回 ``major.minor.rev.build`` 格式的 SDK 版本号。"""
        v = int(self._dll.MV_CC_GetSDKVersion())
        return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"

    def enum_devices(
        self, layer_type: int = MV_GIGE_DEVICE | MV_USB_DEVICE
    ) -> tuple[list[HikvisionCameraDevice], MV_CC_DEVICE_INFO_LIST]:
        """枚举在线相机并保留底层设备信息列表。"""
        dev_list = MV_CC_DEVICE_INFO_LIST()
        code = _u32(self._dll.MV_CC_EnumDevices(layer_type, ctypes.byref(dev_list)))
        if code != MV_OK:
            raise HikvisionSdkError(f"枚举海康相机失败（错误码 0x{code:08X}）")
        devices: list[HikvisionCameraDevice] = []
        count = min(int(dev_list.nDeviceNum), MV_MAX_DEVICE_NUM)
        for idx in range(count):
            info = dev_list.pDeviceInfo[idx].contents
            tlayer = int(info.nTLayerType)
            if tlayer & MV_GIGE_DEVICE:
                gige = info.SpecialInfo.stGigEInfo
                devices.append(
                    HikvisionCameraDevice(
                        index=idx,
                        transport="gige",
                        serial_number=_decode_cstr(gige.chSerialNumber),
                        model_name=_decode_cstr(gige.chModelName),
                        manufacturer=_decode_cstr(gige.chManufacturerName),
                        user_defined_name=_decode_cstr(gige.chUserDefinedName),
                        ip_address=_format_ip(int(gige.nCurrentIp)),
                    )
                )
            elif tlayer & MV_USB_DEVICE:
                usb = info.SpecialInfo.stUsb3VInfo
                devices.append(
                    HikvisionCameraDevice(
                        index=idx,
                        transport="usb",
                        serial_number=_decode_cstr(usb.chSerialNumber),
                        model_name=_decode_cstr(usb.chModelName),
                        manufacturer=_decode_cstr(usb.chManufacturerName),
                        user_defined_name=_decode_cstr(usb.chUserDefinedName),
                        ip_address=None,
                    )
                )
            else:
                devices.append(
                    HikvisionCameraDevice(
                        index=idx,
                        transport="other",
                        serial_number="",
                        model_name="",
                        manufacturer="",
                        user_defined_name="",
                        ip_address=None,
                    )
                )
        return devices, dev_list

    def create_handle(self, dev_info: Any) -> ctypes.c_void_p:
        """创建设备句柄。"""
        handle = ctypes.c_void_p()
        code = _u32(self._dll.MV_CC_CreateHandle(ctypes.byref(handle), ctypes.byref(dev_info)))
        if code != MV_OK or not handle.value:
            raise HikvisionSdkError(f"创建相机句柄失败（错误码 0x{code:08X}）")
        return handle

    def destroy_handle(self, handle: ctypes.c_void_p) -> None:
        """销毁设备句柄。"""
        self._dll.MV_CC_DestroyHandle(handle)

    def open_device(self, handle: ctypes.c_void_p, access_mode: int = MV_ACCESS_EXCLUSIVE) -> None:
        """打开相机设备。"""
        code = _u32(self._dll.MV_CC_OpenDevice(handle, access_mode, 0))
        if code != MV_OK:
            raise HikvisionSdkError(f"打开海康相机失败（错误码 0x{code:08X}）")

    def close_device(self, handle: ctypes.c_void_p) -> None:
        """关闭相机设备。"""
        self._dll.MV_CC_CloseDevice(handle)

    def is_device_connected(self, handle: ctypes.c_void_p) -> bool:
        """检查相机是否仍保持连接。"""
        return bool(self._dll.MV_CC_IsDeviceConnected(handle))

    def get_optimal_packet_size(self, handle: ctypes.c_void_p) -> int:
        """获取 GigE 相机建议的最佳包长。"""
        fn = getattr(self._dll, "MV_CC_GetOptimalPacketSize", None)
        if fn is None:
            return 0
        return int(fn(handle))

    def set_image_node_num(self, handle: ctypes.c_void_p, num: int) -> None:
        """设置 SDK 内部取图缓存节点数。"""
        fn = getattr(self._dll, "MV_CC_SetImageNodeNum", None)
        if fn is not None:
            fn(handle, num)

    def start_grabbing(self, handle: ctypes.c_void_p) -> None:
        """开始取流。"""
        code = _u32(self._dll.MV_CC_StartGrabbing(handle))
        if code != MV_OK:
            raise HikvisionSdkError(f"启动相机取流失败（错误码 0x{code:08X}）")

    def stop_grabbing(self, handle: ctypes.c_void_p) -> None:
        """停止取流。"""
        self._dll.MV_CC_StopGrabbing(handle)

    def get_image_buffer(
        self, handle: ctypes.c_void_p, frame_out: MV_FRAME_OUT, timeout_ms: int
    ) -> int:
        """带超时获取一帧缓存，返回无符号 32 位状态码。"""
        return _u32(self._dll.MV_CC_GetImageBuffer(handle, ctypes.byref(frame_out), timeout_ms))

    def free_image_buffer(self, handle: ctypes.c_void_p, frame_out: MV_FRAME_OUT) -> int:
        """归还一帧缓存给 SDK。"""
        return _u32(self._dll.MV_CC_FreeImageBuffer(handle, ctypes.byref(frame_out)))

    def convert_to_bgr8(
        self, handle: ctypes.c_void_p, frame_out: MV_FRAME_OUT
    ) -> NDArray[np.uint8]:
        """使用 SDK 内置转换将原始帧转为独立 BGR8 ndarray。"""
        info = frame_out.stFrameInfo
        width = int(info.nExtendWidth or info.nWidth)
        height = int(info.nExtendHeight or info.nHeight)
        src_len = int(info.nFrameLenEx or info.nFrameLen)
        dst_size = width * height * 3
        dst_buf = np.empty((height, width, 3), dtype=np.uint8)
        dst_ptr = dst_buf.ctypes.data_as(ctypes.c_void_p)

        fn_ex = getattr(self._dll, "MV_CC_ConvertPixelTypeEx", None)
        if fn_ex is not None:
            param = MV_CC_PIXEL_CONVERT_PARAM_EX(
                nWidth=width,
                nHeight=height,
                enSrcPixelType=int(info.enPixelType),
                pSrcData=frame_out.pBufAddr,
                nSrcDataLen=src_len,
                enDstPixelType=PIXEL_TYPE_BGR8_PACKED,
                pDstBuffer=dst_ptr,
                nDstLen=0,
                nDstBufferSize=dst_size,
            )
            code = _u32(fn_ex(handle, ctypes.byref(param)))
        else:
            fn_legacy = getattr(self._dll, "MV_CC_ConvertPixelType", None)
            if fn_legacy is None:
                raise HikvisionSdkError("SDK 缺少像素格式转换接口 MV_CC_ConvertPixelTypeEx")
            param_v3 = MV_CC_PIXEL_CONVERT_PARAM(
                nWidth=width,
                nHeight=height,
                enSrcPixelType=int(info.enPixelType),
                pSrcData=frame_out.pBufAddr,
                nSrcDataLen=src_len,
                enDstPixelType=PIXEL_TYPE_BGR8_PACKED,
                pDstBuffer=dst_ptr,
                nDstLen=0,
                nDstBufferSize=dst_size,
            )
            code = _u32(fn_legacy(handle, ctypes.byref(param_v3)))
        if code != MV_OK:
            raise HikvisionSdkError(f"像素格式转换 BGR8 失败（错误码 0x{code:08X}）")
        return dst_buf

    def get_int_value(self, handle: ctypes.c_void_p, key: str) -> tuple[int, int, int]:
        """读取整型节点（当前值, 最小值, 最大值）。"""
        val = MVCC_INTVALUE_EX()
        code = _u32(self._dll.MV_CC_GetIntValueEx(handle, key.encode("ascii"), ctypes.byref(val)))
        if code != MV_OK:
            raise HikvisionSdkError(f"读取参数 {key} 失败（错误码 0x{code:08X}）")
        return int(val.nCurValue), int(val.nMin), int(val.nMax)

    def set_int_value(self, handle: ctypes.c_void_p, key: str, value: int) -> None:
        """设置整型节点。"""
        code = _u32(self._dll.MV_CC_SetIntValueEx(handle, key.encode("ascii"), int(value)))
        if code != MV_OK:
            raise HikvisionSdkError(f"设置参数 {key}={value} 失败（错误码 0x{code:08X}）")

    def get_float_value(self, handle: ctypes.c_void_p, key: str) -> tuple[float, float, float]:
        """读取浮点型节点（当前值, 最小值, 最大值）。"""
        val = MVCC_FLOATVALUE()
        code = _u32(self._dll.MV_CC_GetFloatValue(handle, key.encode("ascii"), ctypes.byref(val)))
        if code != MV_OK:
            raise HikvisionSdkError(f"读取参数 {key} 失败（错误码 0x{code:08X}）")
        return float(val.fCurValue), float(val.fMin), float(val.fMax)

    def set_float_value(self, handle: ctypes.c_void_p, key: str, value: float) -> None:
        """设置浮点型节点。"""
        code = _u32(self._dll.MV_CC_SetFloatValue(handle, key.encode("ascii"), float(value)))
        if code != MV_OK:
            raise HikvisionSdkError(f"设置参数 {key}={value} 失败（错误码 0x{code:08X}）")

    def set_enum_value_by_string(self, handle: ctypes.c_void_p, key: str, value: str) -> None:
        """按符号名设置枚举节点。"""
        code = _u32(
            self._dll.MV_CC_SetEnumValueByString(handle, key.encode("ascii"), value.encode("ascii"))
        )
        if code != MV_OK:
            raise HikvisionSdkError(f"设置参数 {key}={value} 失败（错误码 0x{code:08X}）")

    def set_bool_value(self, handle: ctypes.c_void_p, key: str, value: bool) -> None:
        """设置布尔型节点。"""
        fn = getattr(self._dll, "MV_CC_SetBoolValue", None)
        if fn is None:
            return
        code = _u32(fn(handle, key.encode("ascii"), bool(value)))
        if code != MV_OK:
            raise HikvisionSdkError(f"设置参数 {key}={value} 失败（错误码 0x{code:08X}）")


def load_mvs_library(
    explicit_path: str | Path | None = None,
) -> tuple[MvsLibrary, HikvisionSdkLocation]:
    """自动发现并加载海康 MVS SDK 动态库。

    Raises:
        HikvisionSdkNotFoundError: 找不到 SDK。
        HikvisionSdkError: 找到但加载失败（例如位数不匹配或缺少依赖库）。
    """
    location = find_sdk_library(explicit_path)
    location = HikvisionSdkLocation(
        path=location.path.resolve(), source=location.source, platform=location.platform
    )
    try:
        if sys.platform == "win32":
            add_dll_dir = getattr(os, "add_dll_directory", None)
            if add_dll_dir is not None:
                _DLL_DIR_HANDLES.append(add_dll_dir(str(location.path.parent)))
            windll_cls = getattr(ctypes, "WinDLL", ctypes.CDLL)
            dll = windll_cls(str(location.path))
        else:
            dll = ctypes.CDLL(str(location.path))
    except OSError as exc:
        raise HikvisionSdkError(f"无法加载海康 MVS SDK {location.path}：{exc}") from exc
    return MvsLibrary(dll, location), location


def enumerate_cameras(
    sdk_path: str | Path | None = None,
    *,
    layer_type: int = MV_GIGE_DEVICE | MV_USB_DEVICE,
) -> list[HikvisionCameraDevice]:
    """枚举当前在线的海康工业相机。"""
    lib, _ = load_mvs_library(sdk_path)
    devices, _ = lib.enum_devices(layer_type)
    return devices


def probe_hikvision_sdk(sdk_path: str | Path | None = None) -> dict[str, object]:
    """只读探测 SDK 发现状态与版本号（不打开相机），供 ``--diagnose`` 使用。"""
    try:
        lib, location = load_mvs_library(sdk_path)
        return {
            "found": True,
            "path": str(location.path),
            "source": location.source,
            "platform": location.platform,
            "version": lib.sdk_version(),
            "error": None,
        }
    except HikvisionSdkError as exc:
        return {
            "found": False,
            "path": None,
            "source": None,
            "platform": sys.platform,
            "version": None,
            "error": str(exc),
        }
