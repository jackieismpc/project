"""“尚未接入”的占位后端：发布包（不含 ``simulation/``）的默认配置使用它们。

真实的设备通信协议、工业相机和算法交付之前，发布包仍要能启动、创建管理员、管理用户和模板、
查看历史和审计。这些后端让软件正常启动，但**任何需要硬件或算法的操作都会明确失败**，
说明要在配置里接入哪一项：

* 设备：``read_status`` 报告未连接（仪器状态显示“未连接”），其余操作抛 ``DeviceConnectionError``。
* 视频：``open`` 抛 ``VideoOpenError``。
* 算法：``load`` 抛 ``AnalyzerError``。

它们绝不返回假的结果，不会被误当成真实检测。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    RowAnalysisRequest,
    RowAnalysisResult,
)
from gelinspect.interfaces.device import (
    DeviceConnectionError,
    DeviceStatus,
    FlipResult,
    TrayPosition,
)
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoFrame,
    VideoInfo,
    VideoNotOpenError,
    VideoOpenError,
)

_DEVICE_HINT = "设备通信尚未接入：请在配置文件的 device.backend 里指定真实的设备适配器"
_VIDEO_HINT = (
    "相机尚未接入：请在“仪器设置”里选择视频文件或图片文件夹，"
    "或在配置文件的 video.source 里指定真实相机"
)
_ANALYZER_HINT = "算法尚未接入：请在配置文件的 analysis.backend 里指定真实的算法模块"


class UnconfiguredDevice:
    """未接入的设备：状态为未连接，操作都失败。"""

    def connect(self) -> None:
        """总是失败。

        Raises:
            DeviceConnectionError: 设备尚未接入。
        """
        raise DeviceConnectionError(_DEVICE_HINT)

    def move_tray(self, position: TrayPosition) -> None:
        """总是失败。

        Args:
            position: 目标位置。

        Raises:
            DeviceConnectionError: 设备尚未接入。
        """
        raise DeviceConnectionError(_DEVICE_HINT)

    def select_row(self, row_index: int) -> None:
        """总是失败。

        Args:
            row_index: 排号。

        Raises:
            DeviceConnectionError: 设备尚未接入。
        """
        raise DeviceConnectionError(_DEVICE_HINT)

    def flip_row(self, row_index: int) -> FlipResult:
        """总是失败。

        Args:
            row_index: 排号。

        Raises:
            DeviceConnectionError: 设备尚未接入。
        """
        raise DeviceConnectionError(_DEVICE_HINT)

    def restore_row(self, row_index: int) -> FlipResult:
        """总是失败。

        Args:
            row_index: 排号。

        Raises:
            DeviceConnectionError: 设备尚未接入。
        """
        raise DeviceConnectionError(_DEVICE_HINT)

    def read_status(self) -> DeviceStatus:
        """报告未连接。

        Returns:
            ``connected=False`` 的状态。
        """
        return DeviceStatus(
            connected=False,
            faults=[],
            read_at=datetime.now(UTC),
        )

    def close(self) -> None:
        """没有资源需要释放。"""


class UnconfiguredVideoSource:
    """未接入的相机：打开即失败。"""

    def open(self, config: VideoConfig) -> VideoInfo:
        """总是失败。

        Args:
            config: 视频源配置。

        Raises:
            VideoOpenError: 相机尚未接入。
        """
        raise VideoOpenError(_VIDEO_HINT)

    def read(self) -> VideoFrame | None:
        """没有打开过，总是失败。

        Raises:
            VideoNotOpenError: 没有打开。
        """
        raise VideoNotOpenError(_VIDEO_HINT)

    def close(self) -> None:
        """没有资源需要释放。"""


class UnconfiguredAnalyzer:
    """未接入的算法：加载即失败，绝不给出结果。"""

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        """总是失败。

        Args:
            config_path: 算法配置。

        Raises:
            AnalyzerError: 算法尚未接入。
        """
        raise AnalyzerError(_ANALYZER_HINT)

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        """总是失败。

        Args:
            request: 分析请求。

        Raises:
            AnalyzerError: 算法尚未接入。
        """
        raise AnalyzerError(_ANALYZER_HINT)

    def close(self) -> None:
        """没有资源需要释放。"""
