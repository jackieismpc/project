"""实时预览：latest-wins 单槽，限帧率，在自己的线程里缩放。

采集线程只调用 :meth:`PreviewWorker.offer`（限速后放入单槽，非阻塞）。缩放和回调都在预览线程中完成，
回调很慢时只会丢预览帧，不会影响采集、录制和抓帧。界面层再用 Qt Signal 桥接 ``on_preview``。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import PreviewConfig
from gelinspect.services.video.events import (
    EventSink,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)
from gelinspect.services.video.latest_slot import LatestSlotWorker

logger = logging.getLogger("gelinspect.services.video.preview")

_ERROR_REPORT_INTERVAL_S = 5.0


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    """一帧预览图。

    Attributes:
        image: 缩放后的 BGR 图像，只读。原图不大于预览尺寸时直接引用原图。
        sequence: 原帧序号。
        captured_at: 原帧采集时间。
        source_width: 原图宽度。
        source_height: 原图高度。
    """

    image: NDArray[np.uint8]
    sequence: int
    captured_at: datetime
    source_width: int
    source_height: int


def scaled_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    """按长边限制计算缩放后的尺寸，不放大，宽高至少为 1。

    Args:
        width: 原宽度。
        height: 原高度。
        long_side: 长边上限。

    Returns:
        ``(宽, 高)``。
    """
    longest = max(width, height)
    if longest <= long_side:
        return width, height
    scale = long_side / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize_to_long_side(image: NDArray[np.uint8], long_side: int) -> NDArray[np.uint8]:
    """把图像缩放到长边不超过 ``long_side``（INTER_AREA），不放大。

    Args:
        image: BGR 图像。
        long_side: 长边上限。

    Returns:
        缩放后的新数组；无需缩放时返回原数组本身。
    """
    height, width = image.shape[:2]
    new_w, new_h = scaled_size(width, height, long_side)
    if (new_w, new_h) == (width, height):
        return image
    resized = cast(
        "NDArray[np.uint8]", cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    )
    return resized


class PreviewWorker:
    """预览工作线程，同时是一个帧消费者（实现 ``offer``）。"""

    def __init__(
        self,
        config: PreviewConfig,
        callback: Callable[[PreviewFrame], None] | None,
        *,
        clock: Clock | None = None,
        emit: EventSink | None = None,
    ) -> None:
        """创建预览，不启动线程。

        Args:
            config: 预览配置。
            callback: 收到预览帧的回调，在预览线程中调用；抛出的异常会被记录并忽略。
            clock: 时钟，用于限速。
            emit: 事件接收函数。
        """
        self._config = config
        self._callback = callback
        self._clock: Clock = clock or SystemClock()
        self._emit = emit
        self._interval = 1.0 / config.max_fps
        self._last_accept: float | None = None
        self._last_error_report = -_ERROR_REPORT_INTERVAL_S
        self._skipped = 0
        self._offered = 0
        self._delivered = 0
        self._errors = 0
        self._worker: LatestSlotWorker[VideoFrame] = LatestSlotWorker(
            "video-preview", self._process, self._on_error
        )

    def set_callback(self, callback: Callable[[PreviewFrame], None] | None) -> None:
        """更换预览回调。

        Args:
            callback: 新回调，None 表示不再投递。
        """
        self._callback = callback

    @property
    def offered(self) -> int:
        """收到的帧数。"""
        return self._offered

    @property
    def skipped(self) -> int:
        """因限帧率而跳过的帧数。"""
        return self._skipped

    @property
    def dropped(self) -> int:
        """放入单槽后被更新的帧覆盖（慢消费者）而丢弃的预览帧数。"""
        return self._worker.replaced

    @property
    def delivered(self) -> int:
        """已成功交给回调的预览帧数。"""
        return self._delivered

    @property
    def errors(self) -> int:
        """回调或缩放抛出异常的次数。"""
        return self._errors

    def start(self) -> None:
        """启动预览线程。"""
        self._last_accept = None
        self._worker.start()

    def stop(self, timeout_s: float) -> bool:
        """停止预览线程。

        Args:
            timeout_s: 等待退出的最长时间。

        Returns:
            线程是否已退出。
        """
        return self._worker.stop(timeout_s)

    def offer(self, frame: VideoFrame) -> None:
        """提交一帧，由采集线程调用。非阻塞，超过帧率上限时直接跳过。

        Args:
            frame: 视频帧。
        """
        self._offered += 1
        now = self._clock.monotonic()
        if self._last_accept is not None and now - self._last_accept < self._interval:
            self._skipped += 1
            return
        self._last_accept = now
        self._worker.submit(frame)

    def _process(self, frame: VideoFrame) -> None:
        image = resize_to_long_side(frame.image, self._config.long_side)
        if image is not frame.image:
            image.flags.writeable = False
        height, width = frame.image.shape[:2]
        preview = PreviewFrame(
            image=image,
            sequence=frame.sequence,
            captured_at=frame.captured_at,
            source_width=width,
            source_height=height,
        )
        callback = self._callback
        if callback is not None:
            callback(preview)
        self._delivered += 1

    def _on_error(self, exc: Exception) -> None:
        self._errors += 1
        logger.error("预览处理或回调抛出异常（线程继续运行）：%r", exc, exc_info=exc)
        now = self._clock.monotonic()
        if self._emit is not None and now - self._last_error_report >= _ERROR_REPORT_INTERVAL_S:
            self._last_error_report = now
            self._emit(
                make_event(
                    self._clock,
                    VideoEventCode.CALLBACK_ERROR,
                    VideoEventLevel.ERROR,
                    f"预览回调抛出异常：{exc!r}",
                )
            )
