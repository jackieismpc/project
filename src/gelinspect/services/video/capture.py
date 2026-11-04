"""采集线程：循环读取视频源，写入环形缓冲，再通知消费者。

铁律：**采集线程绝不被消费者阻塞**。消费者的 ``offer`` 必须是非阻塞的（预览用单槽覆盖，录制用有界
队列并丢帧，质量检查只计数）；事件通过异步总线发布；消费者抛出的异常被捕获并记录，不会杀死线程。

``VideoSource`` 的 ``open``、``read``、``close`` 都在本线程上调用（见 ``docs/architecture.md``）。
断流后在本线程内按退避策略重连。
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoFrame,
    VideoInfo,
    VideoOpenError,
    VideoSource,
)
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import ReconnectConfig
from gelinspect.services.video.events import (
    EventSink,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)
from gelinspect.services.video.ring_buffer import FrameRingBuffer

logger = logging.getLogger("gelinspect.services.video.capture")

_CONSUMER_ERROR_REPORT_INTERVAL_S = 5.0
_IDLE_SLEEP_S = 0.001


class FrameConsumer(Protocol):
    """帧消费者。``offer`` 在采集线程调用，必须非阻塞。"""

    def offer(self, frame: VideoFrame) -> None:
        """接收一帧。

        Args:
            frame: 只读的视频帧。
        """
        ...


class CaptureState(enum.Enum):
    """采集线程状态。"""

    IDLE = "idle"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    """文件回放到达末尾。"""
    FAILED = "failed"
    """断流且不再重连（重连关闭或达到最大次数）。"""
    STOPPED = "stopped"


class CaptureWorker:
    """采集线程。"""

    def __init__(
        self,
        source: VideoSource,
        source_config: VideoConfig,
        buffer: FrameRingBuffer,
        consumers: Sequence[FrameConsumer],
        *,
        reconnect: ReconnectConfig,
        clock: Clock | None = None,
        emit: EventSink | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_reconnected: Callable[[VideoInfo], None] | None = None,
    ) -> None:
        """创建采集线程对象，不启动。

        Args:
            source: 视频源，其方法只会在采集线程上被调用。
            source_config: 传给 ``VideoSource.open`` 的配置。
            buffer: 环形缓冲。
            consumers: 帧消费者，按顺序通知。
            reconnect: 重连策略。
            clock: 时钟。
            emit: 事件接收函数，必须非阻塞。
            on_disconnect: 断流时在采集线程调用，必须非阻塞。
            on_reconnected: 重连成功后在采集线程调用，参数为新的 ``VideoInfo``。
        """
        self._source = source
        self._config = source_config
        self._buffer = buffer
        self._consumers = tuple(consumers)
        self._reconnect = reconnect
        self._clock: Clock = clock or SystemClock()
        self._emit_sink = emit
        self._on_disconnect = on_disconnect
        self._on_reconnected = on_reconnected
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._thread: threading.Thread | None = None
        self._open_error: Exception | None = None
        self._info: VideoInfo | None = None
        self._state = CaptureState.IDLE
        self._frames = 0
        self._reconnects = 0
        self._consumer_errors = 0
        self._last_consumer_report = -_CONSUMER_ERROR_REPORT_INTERVAL_S

    # ------------------------------------------------------------------ 属性

    @property
    def state(self) -> CaptureState:
        """当前状态。"""
        return self._state

    @property
    def info(self) -> VideoInfo | None:
        """最近一次打开得到的视频源参数。"""
        return self._info

    @property
    def frames_captured(self) -> int:
        """累计采集的帧数。"""
        return self._frames

    @property
    def reconnects(self) -> int:
        """累计重连成功次数。"""
        return self._reconnects

    @property
    def consumer_errors(self) -> int:
        """消费者抛出异常的累计次数。"""
        return self._consumer_errors

    @property
    def is_alive(self) -> bool:
        """采集线程是否仍在运行。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------------ 生命周期

    def start(self, open_timeout_s: float) -> VideoInfo:
        """启动线程并等待视频源打开。

        Args:
            open_timeout_s: 等待打开的最长时间。

        Returns:
            视频源的实际参数。

        Raises:
            VideoOpenError: 视频源打开失败或超时。
        """
        self._stop.clear()
        self._opened.clear()
        self._open_error = None
        self._info = None
        self._state = CaptureState.IDLE
        self._thread = threading.Thread(target=self._run, name="video-capture", daemon=True)
        self._thread.start()
        if not self._opened.wait(open_timeout_s):
            self._stop.set()
            raise VideoOpenError(f"打开视频源超时（{open_timeout_s} 秒）")
        if self._open_error is not None:
            thread = self._thread
            if thread is not None:
                thread.join(2.0)
            error = self._open_error
            if isinstance(error, VideoOpenError):
                raise error
            raise VideoOpenError(f"打开视频源失败：{error!r}") from error
        assert self._info is not None
        return self._info

    def stop(self, timeout_s: float) -> bool:
        """请求停止并等待线程退出。线程会在退出前关闭视频源。

        Args:
            timeout_s: 等待退出的最长时间。

        Returns:
            线程是否已退出。超时返回 False（线程可能卡在驱动的 ``read`` 里）。
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout_s)
        alive = thread.is_alive()
        if not alive:
            self._thread = None
        return not alive

    # ------------------------------------------------------------------ 线程体

    def _run(self) -> None:
        try:
            try:
                info = self._source.open(self._config)
            except Exception as exc:
                self._open_error = exc
                self._state = CaptureState.FAILED
                self._opened.set()
                return
            self._info = info
            self._state = CaptureState.RUNNING
            self._opened.set()
            self._loop()
        except Exception:
            logger.exception("采集线程发生未预期的异常")
            self._state = CaptureState.FAILED
        finally:
            self._safe_close()
            if self._state in (CaptureState.RUNNING, CaptureState.RECONNECTING):
                self._state = CaptureState.STOPPED

    def _loop(self) -> None:
        last_frame_t = self._clock.monotonic()
        stall_limit = self._reconnect.stall_disconnect_s
        while not self._stop.is_set():
            try:
                frame = self._source.read()
            except Exception as exc:
                if not self._handle_disconnect(exc):
                    return
                last_frame_t = self._clock.monotonic()
                continue
            if frame is None:
                info = self._info
                if info is not None and not info.is_live:
                    self._state = CaptureState.ENDED
                    self._emit(
                        VideoEventCode.END_OF_STREAM,
                        VideoEventLevel.INFO,
                        f"文件回放结束，共 {self._frames} 帧",
                    )
                    return
                if stall_limit is not None and self._clock.monotonic() - last_frame_t > stall_limit:
                    timeout_error = TimeoutError(f"连续 {stall_limit} 秒读不到帧")
                    if not self._handle_disconnect(timeout_error):
                        return
                    last_frame_t = self._clock.monotonic()
                    continue
                self._stop.wait(_IDLE_SLEEP_S)
                continue
            last_frame_t = self._clock.monotonic()
            self._dispatch(frame)

    def _dispatch(self, frame: VideoFrame) -> None:
        try:
            frame.image.flags.writeable = False
        except ValueError:  # 极少数无法设置只读的数组，继续使用但不保证只读
            logger.warning("帧图像无法设为只读")
        self._frames += 1
        self._buffer.put(frame)
        for consumer in self._consumers:
            try:
                consumer.offer(frame)
            except Exception as exc:
                self._consumer_failed(consumer, exc)

    def _consumer_failed(self, consumer: FrameConsumer, exc: Exception) -> None:
        self._consumer_errors += 1
        logger.error(
            "帧消费者 %s 抛出异常（采集继续）：%r", type(consumer).__name__, exc, exc_info=exc
        )
        now = self._clock.monotonic()
        if now - self._last_consumer_report >= _CONSUMER_ERROR_REPORT_INTERVAL_S:
            self._last_consumer_report = now
            self._emit(
                VideoEventCode.CONSUMER_ERROR,
                VideoEventLevel.ERROR,
                f"帧消费者 {type(consumer).__name__} 抛出异常：{exc!r}",
            )

    # ------------------------------------------------------------------ 断流与重连

    def _handle_disconnect(self, exc: Exception) -> bool:
        """处理断流。返回 True 表示已重连成功，False 表示线程应退出。"""
        kind = "断流" if isinstance(exc, VideoDisconnectedError) else "读取失败"
        self._emit(
            VideoEventCode.DISCONNECTED,
            VideoEventLevel.ERROR,
            f"视频源{kind}：{exc!r}",
        )
        self._call_hook(self._on_disconnect)
        self._safe_close()
        policy = self._reconnect
        if not policy.enabled:
            self._state = CaptureState.FAILED
            self._emit(
                VideoEventCode.RECONNECT_FAILED,
                VideoEventLevel.ERROR,
                "自动重连已关闭，采集停止",
            )
            return False
        self._state = CaptureState.RECONNECTING
        delay = policy.initial_delay_s
        attempt = 0
        while True:
            if self._stop.wait(delay):
                return False
            attempt += 1
            self._emit(
                VideoEventCode.RECONNECTING,
                VideoEventLevel.WARNING,
                f"第 {attempt} 次尝试重连",
                value=float(attempt),
            )
            try:
                info = self._source.open(self._config)
            except Exception as open_exc:
                logger.warning("重连失败（第 %d 次）：%r", attempt, open_exc)
                self._safe_close()
                if policy.max_attempts is not None and attempt >= policy.max_attempts:
                    self._state = CaptureState.FAILED
                    self._emit(
                        VideoEventCode.RECONNECT_FAILED,
                        VideoEventLevel.ERROR,
                        f"重连 {attempt} 次均失败，放弃",
                        value=float(attempt),
                    )
                    return False
                delay = min(delay * policy.factor, policy.max_delay_s)
                continue
            self._info = info
            self._reconnects += 1
            self._state = CaptureState.RUNNING
            self._emit(
                VideoEventCode.RECONNECTED,
                VideoEventLevel.INFO,
                f"重连成功（第 {attempt} 次尝试）",
                value=float(attempt),
            )
            if self._on_reconnected is not None:
                try:
                    self._on_reconnected(info)
                except Exception:
                    logger.exception("重连回调抛出异常（已忽略）")
            return True

    def _call_hook(self, hook: Callable[[], None] | None) -> None:
        if hook is None:
            return
        try:
            hook()
        except Exception:
            logger.exception("采集线程回调抛出异常（已忽略）")

    def _safe_close(self) -> None:
        try:
            self._source.close()
        except Exception:
            logger.exception("关闭视频源时抛出异常（已忽略）")

    def _emit(
        self,
        code: VideoEventCode,
        level: VideoEventLevel,
        message: str,
        *,
        value: float | None = None,
    ) -> None:
        if self._emit_sink is not None:
            self._emit_sink(make_event(self._clock, code, level, message, value=value))
