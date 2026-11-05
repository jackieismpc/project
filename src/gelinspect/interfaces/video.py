"""视频源接口。

``VideoSource`` 只负责按帧取图。环形缓冲、预览、录制、抓帧和质量检查都在视频管线，
不属于视频源的职责。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, Field

from gelinspect.interfaces._base import BgrImage, ContractModel


class VideoError(Exception):
    """视频源异常基类。"""


class VideoOpenError(VideoError):
    """视频源无法打开，例如设备不存在、被占用、没有授权或文件损坏。"""


class VideoNotOpenError(VideoError):
    """视频源未打开或已关闭时调用了 ``read``。"""


class VideoDisconnectedError(VideoError):
    """已打开的实时视频源断流。``read`` 抛出后，需要先 ``close`` 再 ``open`` 才能恢复。"""


class VideoConfig(ContractModel):
    """视频源配置。

    Attributes:
        device: 设备标识：UVC 相机为序号字符串，文件为路径，工业相机为序列号。
        width: 期望宽度，None 表示使用设备默认值。
        height: 期望高度，None 表示使用设备默认值。
        fps: 期望帧率，None 表示使用设备默认值。
        read_timeout_ms: ``read`` 的最长阻塞时间，单位毫秒。
    """

    device: str = Field(min_length=1)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    read_timeout_ms: int = Field(default=1000, gt=0)


class VideoInfo(ContractModel):
    """视频源打开后的实际参数，可能与 :class:`VideoConfig` 的期望值不同。

    Attributes:
        width: 实际宽度。
        height: 实际高度。
        fps: 实际帧率，未知时为 None。
        is_live: True 表示实时相机，False 表示文件回放。
        frame_count: 文件总帧数，实时相机或未知时为 None。
        description: 设备或文件的描述，写入日志。
    """

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float | None = Field(default=None, gt=0)
    is_live: bool
    frame_count: int | None = Field(default=None, ge=0)
    description: str = ""


class VideoFrame(ContractModel):
    """视频源产出的一帧。

    Attributes:
        image: H×W×3、uint8、BGR 的图像，由调用方持有，视频源之后不得改写。
        sequence: 帧序号，从 0 递增；出现跳号表示驱动层掉帧。
        captured_at: 采集时间，必须带时区，与设备 ``FlipResult`` 使用同一时钟。
        monotonic_s: 单调时钟时间，单位秒，用于计算间隔和帧率。
    """

    image: BgrImage
    sequence: int = Field(ge=0)
    captured_at: AwareDatetime
    monotonic_s: float


@runtime_checkable
class VideoSource(Protocol):
    """视频源。全部方法都在采集线程上调用，实现无需加锁。"""

    def open(self, config: VideoConfig) -> VideoInfo:
        """打开视频源。

        Args:
            config: 视频源配置。

        Returns:
            实际生效的参数。

        Raises:
            VideoOpenError: 无法打开。
        """
        ...

    def read(self) -> VideoFrame | None:
        """读取下一帧，最多阻塞 ``read_timeout_ms``。

        Returns:
            下一帧。返回 None 时，实时相机表示本次超时，可以继续调用；
            文件回放（``is_live=False``）表示已到文件末尾。

        Raises:
            VideoNotOpenError: 未打开或已关闭。
            VideoDisconnectedError: 实时视频源断流。
        """
        ...

    def close(self) -> None:
        """关闭视频源并释放设备，可重复调用。"""
        ...
