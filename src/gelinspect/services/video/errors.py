"""视频管线的异常。"""

from __future__ import annotations

from gelinspect.interfaces.video import VideoError


class VideoPipelineError(VideoError):
    """视频管线异常基类。"""


class PipelineStateError(VideoPipelineError):
    """管线状态不允许当前操作，例如未启动就开始录制。"""


class InvalidIdentifierError(VideoPipelineError, ValueError):
    """试验标识或排号不合法，拒绝用于拼接路径。"""


class RecorderError(VideoPipelineError):
    """录制失败：写入器打不开、写盘出错、录制段为空或超时未结束。"""


class NoActiveSegmentError(RecorderError):
    """没有进行中（或尚未取走）的录制段。"""


class SnapshotError(VideoPipelineError):
    """抓帧失败。"""


class InsufficientFramesError(SnapshotError):
    """翻转前或翻转后的帧数不足 n 帧。

    Attributes:
        phase: ``"before"`` 或 ``"after"``。
        required: 需要的帧数。
        available: 实际取到的帧数。
    """

    def __init__(self, phase: str, required: int, available: int, detail: str = "") -> None:
        """创建异常。

        Args:
            phase: ``"before"`` 或 ``"after"``。
            required: 需要的帧数。
            available: 实际取到的帧数。
            detail: 补充说明。
        """
        label = "翻转前" if phase == "before" else "翻转后"
        message = f"{label}的帧数不足：需要 {required} 帧，实际 {available} 帧"
        if detail:
            message = f"{message}（{detail}）"
        super().__init__(message)
        self.phase = phase
        self.required = required
        self.available = available
