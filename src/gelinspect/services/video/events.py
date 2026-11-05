"""视频事件与异步事件总线。

管线内所有告警都用 :class:`VideoEvent` 表达。事件由 :class:`EventBus` 放入有界队列，
再由独立的分发线程投递给监听者，所以**任何监听者都不会阻塞采集线程**。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from gelinspect.services.video.clock import Clock

logger = logging.getLogger("gelinspect.services.video")


class VideoEventLevel(StrEnum):
    """事件级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class VideoEventCode(StrEnum):
    """事件码。同一事件码的 ``active=False`` 表示该状况已恢复。"""

    BLACK_FRAME = "black_frame"
    """黑屏：缩小图的平均亮度低于阈值。"""
    OVEREXPOSED = "overexposed"
    """过曝：饱和像素占比超过阈值。"""
    FROZEN_FRAME = "frozen_frame"
    """卡帧：连续多次采样的画面几乎没有变化。"""
    LOW_FPS = "low_fps"
    """实测帧率低于期望帧率的一定比例。"""
    FRAMES_DROPPED = "frames_dropped"
    """视频源的帧序号跳号，驱动层掉帧。"""
    STREAM_STALLED = "stream_stalled"
    """看门狗超时：很久没有收到新帧。"""
    DISCONNECTED = "disconnected"
    """视频源断流。"""
    RECONNECTING = "reconnecting"
    """正在尝试重连。"""
    RECONNECTED = "reconnected"
    """重连成功。"""
    RECONNECT_FAILED = "reconnect_failed"
    """采集不会自行恢复：重连放弃（达到最大次数）、重连被关闭，或采集线程异常退出。"""
    END_OF_STREAM = "end_of_stream"
    """文件回放结束。"""
    PIN_OVERFLOW = "pin_overflow"
    """被保护的帧超过硬上限，已强制淘汰最旧的受保护帧，抓拍帧可能丢失。"""
    CONSUMER_ERROR = "consumer_error"
    """帧消费者（预览、录制、质量检查）抛出异常。"""
    CALLBACK_ERROR = "callback_error"
    """用户回调（预览回调、事件监听者）抛出异常。"""
    RECORDING_DROPPED = "recording_dropped"
    """录制队列已满，丢弃了录制帧。"""
    SEGMENT_INTERRUPTED = "segment_interrupted"
    """录制段被异常终止（断流、管线停止等），文件已封存但不完整。"""
    STOP_TIMEOUT = "stop_timeout"
    """停止时某个工作线程没有在限时内退出。"""


@dataclass(frozen=True, slots=True)
class VideoEvent:
    """一条视频事件。

    Attributes:
        code: 事件码。
        level: 级别。
        message: 面向人的说明。
        at: 事件发生的墙钟时间（带时区）。
        monotonic_s: 事件发生的单调时钟读数。
        value: 触发事件的实测值，没有时为 None。
        threshold: 触发事件的阈值，没有时为 None。
        active: True 表示状况出现，False 表示状况已恢复。
    """

    code: VideoEventCode
    level: VideoEventLevel
    message: str
    at: datetime
    monotonic_s: float
    value: float | None = None
    threshold: float | None = None
    active: bool = True


EventSink = Callable[[VideoEvent], None]
"""事件接收函数。必须是非阻塞的。"""


def make_event(
    clock: Clock,
    code: VideoEventCode,
    level: VideoEventLevel,
    message: str,
    *,
    value: float | None = None,
    threshold: float | None = None,
    active: bool = True,
) -> VideoEvent:
    """用时钟当前读数创建事件。

    Args:
        clock: 时钟。
        code: 事件码。
        level: 级别。
        message: 说明。
        value: 实测值。
        threshold: 阈值。
        active: 状况是否仍然存在。

    Returns:
        事件对象。
    """
    return VideoEvent(
        code=code,
        level=level,
        message=message,
        at=clock.now(),
        monotonic_s=clock.monotonic(),
        value=value,
        threshold=threshold,
        active=active,
    )


_LOG_LEVELS = {
    VideoEventLevel.INFO: logging.INFO,
    VideoEventLevel.WARNING: logging.WARNING,
    VideoEventLevel.ERROR: logging.ERROR,
}


class EventBus:
    """有界、异步的事件总线。

    ``emit`` 只做入队和写日志，永不阻塞、永不抛异常。队列溢出时丢弃最旧的事件并计数。
    监听者抛出的异常会被记录，不影响其他监听者和分发线程。
    """

    def __init__(self, *, queue_size: int = 1000, history_size: int = 256) -> None:
        """创建总线，不启动线程。

        Args:
            queue_size: 待分发事件的队列上限。
            history_size: :meth:`recent` 保留的最近事件条数。
        """
        self._queue_size = queue_size
        self._cond = threading.Condition()
        self._queue: deque[VideoEvent] = deque()
        self._history: deque[VideoEvent] = deque(maxlen=history_size)
        self._listeners: list[EventSink] = []
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """因队列溢出而丢弃的事件数。"""
        with self._cond:
            return self._dropped

    def subscribe(self, listener: EventSink) -> None:
        """添加监听者，在分发线程中被调用。

        Args:
            listener: 事件处理函数。
        """
        with self._cond:
            if listener not in self._listeners:
                self._listeners = [*self._listeners, listener]

    def unsubscribe(self, listener: EventSink) -> None:
        """移除监听者，不存在时忽略。

        Args:
            listener: 事件处理函数。
        """
        with self._cond:
            self._listeners = [item for item in self._listeners if item != listener]

    def recent(self) -> list[VideoEvent]:
        """返回最近的事件（含已分发的），按发生顺序。

        Returns:
            事件列表的副本。
        """
        with self._cond:
            return list(self._history)

    def emit(self, event: VideoEvent) -> None:
        """发布事件。非阻塞，可在任意线程调用。

        Args:
            event: 事件。
        """
        logger.log(_LOG_LEVELS[event.level], "[%s] %s", event.code.value, event.message)
        with self._cond:
            self._history.append(event)
            if len(self._queue) >= self._queue_size:
                self._queue.popleft()
                self._dropped += 1
            self._queue.append(event)
            self._cond.notify()

    def start(self) -> None:
        """启动分发线程，已启动时无操作。"""
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._run, name="video-events", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> bool:
        """投递剩余事件后停止分发线程。

        Args:
            timeout_s: 等待线程退出的时间。

        Returns:
            线程是否已退出。
        """
        with self._cond:
            thread = self._thread
            self._stopping = True
            self._cond.notify_all()
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout_s)
        alive = thread.is_alive()
        if not alive:
            with self._cond:
                if self._thread is thread:
                    self._thread = None
        return not alive

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if not self._queue:
                    return
                event = self._queue.popleft()
                listeners = self._listeners
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    logger.exception("事件监听者抛出异常（已忽略）：%s", event.code.value)
