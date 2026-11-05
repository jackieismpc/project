"""视频管线门面：把采集、缓冲、预览、录制、抓帧和质量检查装配在一起。

线程（全部为守护线程，只用 ``threading``，不依赖 Qt）::

    采集线程 video-capture ──写──→ FrameRingBuffer
        │  offer（非阻塞）
        ├→ PreviewWorker   video-preview   latest-wins 单槽，≤15 fps，缩放后回调
        ├→ SegmentRecorder video-writer-*  有界队列 + 单写盘线程（每个录制段一个）
        └→ QualityMonitor  video-quality   每 N 帧分析 + 看门狗
    事件总线 video-events：异步投递 VideoEvent
    抓帧线程池 video-grab / video-imgio：仅在抓帧时按需创建

``start``/``stop`` 幂等，可反复启停，``stop`` 对每个线程设有 join 超时。
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from gelinspect.interfaces.video import VideoInfo, VideoSource
from gelinspect.services.video.capture import CaptureState, CaptureWorker
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import VideoPipelineConfig
from gelinspect.services.video.errors import PipelineStateError
from gelinspect.services.video.events import (
    EventBus,
    EventSink,
    VideoEvent,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)
from gelinspect.services.video.preview import PreviewFrame, PreviewWorker
from gelinspect.services.video.quality import QualityMonitor, QualityStats
from gelinspect.services.video.recorder import RecorderStats, SegmentRecord, SegmentRecorder
from gelinspect.services.video.ring_buffer import BufferStats, FrameRingBuffer
from gelinspect.services.video.snapshot import SnapshotGrabber, SnapshotResult

logger = logging.getLogger("gelinspect.services.video")

_DEFAULT_RECORD_FPS_FLOOR = 1.0
_DEFAULT_RECORD_FPS_CEIL = 120.0


class PipelineState(enum.Enum):
    """管线状态。"""

    STOPPED = "stopped"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    """文件回放已到末尾（管线仍处于已启动状态，缓冲和抓帧仍可用）。"""
    FAILED = "failed"
    """断流且不再重连。"""


@dataclass(frozen=True, slots=True)
class PipelineStats:
    """管线统计快照。

    Attributes:
        state: 状态。
        frames_captured: 累计采集帧数。
        reconnects: 累计重连成功次数。
        consumer_errors: 帧消费者抛出异常的次数。
        buffer: 环形缓冲统计。
        quality: 质量统计。
        recorder: 录制统计。
        preview_offered: 预览收到的帧数。
        preview_skipped: 因限帧率跳过的帧数。
        preview_dropped: 慢消费者造成的预览丢帧数。
        preview_delivered: 成功交给回调的预览帧数。
        events_dropped: 事件队列溢出丢弃的事件数。
    """

    state: PipelineState
    frames_captured: int
    reconnects: int
    consumer_errors: int
    buffer: BufferStats
    quality: QualityStats
    recorder: RecorderStats
    preview_offered: int
    preview_skipped: int
    preview_dropped: int
    preview_delivered: int
    events_dropped: int


class VideoPipeline:
    """视频管线。

    典型用法::

        pipeline = VideoPipeline(source, config, on_preview=show)
        info = pipeline.start()
        pipeline.start_segment("EXP-001", 1)
        pipeline.mark_flip_start(flip.started_at, 1)   # 翻转开始时
        ...                                             # 设备翻转
        snap = pipeline.grab(1, 3, experiment_id="EXP-001", flip_completed_at=flip.completed_at)
        record = pipeline.stop_segment()
        pipeline.stop()
    """

    def __init__(
        self,
        source: VideoSource,
        config: VideoPipelineConfig,
        *,
        clock: Clock | None = None,
        on_preview: Callable[[PreviewFrame], None] | None = None,
        on_event: EventSink | None = None,
    ) -> None:
        """创建管线，不启动线程、不打开视频源。

        Args:
            source: 视频源（已按配置创建）。管线启动后其方法只在采集线程上被调用。
            config: 管线配置。
            clock: 时钟，默认系统时钟。
            on_preview: 预览回调，在预览线程调用。
            on_event: 事件监听者，在事件分发线程调用。
        """
        self._source = source
        self._config = config
        self._clock: Clock = clock or SystemClock()
        self._lock = threading.RLock()
        self._running = False
        self._info: VideoInfo | None = None
        self._capture: CaptureWorker | None = None

        self._bus = EventBus()
        if on_event is not None:
            self._bus.subscribe(on_event)
        emit = self._bus.emit
        self._buffer = FrameRingBuffer(
            config.buffer.max_bytes,
            hard_limit_bytes=config.buffer.hard_limit_bytes,
            on_event=emit,
            clock=self._clock,
        )
        self._preview = PreviewWorker(config.preview, on_preview, clock=self._clock, emit=emit)
        self._recorder = SegmentRecorder(
            config.data_dir, config.recorder, clock=self._clock, emit=emit
        )
        self._quality = QualityMonitor(config.quality, clock=self._clock, emit=emit)
        self._snapshots = SnapshotGrabber(self._buffer, config.data_dir, config.snapshot)

    # ------------------------------------------------------------------ 属性

    @property
    def buffer(self) -> FrameRingBuffer:
        """环形缓冲（只读使用）。"""
        return self._buffer

    @property
    def recorder(self) -> SegmentRecorder:
        """分段录制器。"""
        return self._recorder

    @property
    def snapshots(self) -> SnapshotGrabber:
        """抓帧器。"""
        return self._snapshots

    @property
    def quality(self) -> QualityMonitor:
        """质量监控。"""
        return self._quality

    @property
    def info(self) -> VideoInfo | None:
        """最近一次打开得到的视频源参数，未启动时为 None。"""
        return self._info

    @property
    def is_running(self) -> bool:
        """管线是否已启动（含重连中、文件已播完）。"""
        return self._running

    @property
    def state(self) -> PipelineState:
        """当前状态。"""
        capture = self._capture
        if not self._running or capture is None:
            return PipelineState.STOPPED
        mapping = {
            CaptureState.RECONNECTING: PipelineState.RECONNECTING,
            CaptureState.ENDED: PipelineState.ENDED,
            CaptureState.FAILED: PipelineState.FAILED,
        }
        return mapping.get(capture.state, PipelineState.RUNNING)

    def set_preview_callback(self, callback: Callable[[PreviewFrame], None] | None) -> None:
        """更换预览回调。

        Args:
            callback: 新回调，None 表示不再投递。
        """
        self._preview.set_callback(callback)

    def add_event_listener(self, listener: EventSink) -> None:
        """添加事件监听者（在事件分发线程调用）。

        Args:
            listener: 事件处理函数。
        """
        self._bus.subscribe(listener)

    def remove_event_listener(self, listener: EventSink) -> None:
        """移除事件监听者。

        Args:
            listener: 事件处理函数。
        """
        self._bus.unsubscribe(listener)

    def recent_events(self) -> list[VideoEvent]:
        """返回最近的事件（含已投递的）。

        Returns:
            事件列表。
        """
        return self._bus.recent()

    def stats(self) -> PipelineStats:
        """返回统计快照。

        Returns:
            :class:`PipelineStats`。
        """
        capture = self._capture
        return PipelineStats(
            state=self.state,
            frames_captured=capture.frames_captured if capture else 0,
            reconnects=capture.reconnects if capture else 0,
            consumer_errors=capture.consumer_errors if capture else 0,
            buffer=self._buffer.stats(),
            quality=self._quality.stats(),
            recorder=self._recorder.stats(),
            preview_offered=self._preview.offered,
            preview_skipped=self._preview.skipped,
            preview_dropped=self._preview.dropped,
            preview_delivered=self._preview.delivered,
            events_dropped=self._bus.dropped,
        )

    # ------------------------------------------------------------------ 生命周期

    def reconfigure(self, source: VideoSource, config: VideoPipelineConfig) -> None:
        """更换视频源和抓帧参数，下一次 :meth:`start` 起生效（仪器设置保存后调用）。

        只有视频源（含 ``config.source``）和抓帧配置会更换；缓冲、预览、录制和质量检查的参数
        在创建时已经定下，这里不改。旧视频源由调用方释放：管线停止时已经关闭了它。

        Args:
            source: 新的视频源（已按配置创建，尚未打开）。
            config: 新的管线配置。

        Raises:
            PipelineStateError: 管线正在运行，运行中不能换视频源。
        """
        with self._lock:
            if self._running or self._capture is not None:
                raise PipelineStateError("视频管线正在运行，运行中不能更换视频源")
            self._snapshots.close()
            self._source = source
            self._config = config
            self._snapshots = SnapshotGrabber(self._buffer, config.data_dir, config.snapshot)

    def start(self) -> VideoInfo:
        """启动管线：打开视频源并启动全部工作线程。已启动时直接返回当前参数。

        Returns:
            视频源的实际参数（以相机返回为准）。

        Raises:
            VideoOpenError: 视频源打开失败，此时所有线程都已回收，可以再次 ``start``。
        """
        with self._lock:
            if self._running and self._info is not None:
                return self._info
            self._buffer.clear()
            self._bus.start()
            self._quality.start()
            self._preview.start()
            capture = CaptureWorker(
                self._source,
                self._config.source,
                self._buffer,
                [self._preview, self._recorder, self._quality],
                reconnect=self._config.reconnect,
                clock=self._clock,
                emit=self._bus.emit,
                on_disconnect=self._handle_disconnect,
                on_reconnected=self._handle_reconnected,
            )
            try:
                info = capture.start(self._config.open_timeout_s)
            except BaseException:
                capture.stop(self._config.stop_timeout_s)
                self._stop_helpers()
                raise
            self._capture = capture
            self._info = info
            self._apply_info(info)
            self._running = True
            return info

    def stop(self) -> None:
        """停止管线，幂等。

        顺序：取消抓帧等待 → 停采集线程（并关闭视频源）→ 封存进行中的录制段 → 停其余线程。
        任何线程超过 ``stop_timeout_s`` 没有退出时发 ``STOP_TIMEOUT`` 事件并继续停止其余部分。
        """
        with self._lock:
            if not self._running and self._capture is None:
                return
            timeout = self._config.stop_timeout_s
            self._buffer.cancel_waits()
            capture = self._capture
            if capture is not None and not capture.stop(timeout):
                self._warn_timeout("采集线程")
            self._running = False
            self._capture = None
            self._stop_helpers()

    def _stop_helpers(self) -> None:
        timeout = self._config.stop_timeout_s
        if not self._recorder.close(max(timeout, self._config.recorder.finish_timeout_s)):
            self._warn_timeout("录制写盘线程")
        self._snapshots.close()
        if not self._preview.stop(timeout):
            self._warn_timeout("预览线程")
        if not self._quality.stop(timeout):
            self._warn_timeout("质量监控线程")
        if not self._bus.stop(timeout):
            logger.warning("事件分发线程未在 %s 秒内退出", timeout)

    def _warn_timeout(self, what: str) -> None:
        self._bus.emit(
            make_event(
                self._clock,
                VideoEventCode.STOP_TIMEOUT,
                VideoEventLevel.ERROR,
                f"{what}未在限时内退出",
            )
        )

    def _apply_info(self, info: VideoInfo) -> None:
        self._recorder.set_source_size(info.width, info.height)
        self._quality.configure(info.fps if info.is_live else None)

    # ------------------------------------------------------------------ 采集线程回调

    def _handle_disconnect(self) -> None:
        self._quality.pause_watchdog()
        self._recorder.abort_segment("disconnected")

    def _handle_reconnected(self, info: VideoInfo) -> None:
        self._info = info
        self._apply_info(info)
        self._quality.resume_watchdog()

    # ------------------------------------------------------------------ 录制

    def start_segment(self, experiment_id: str, row_index: int) -> Path:
        """开始录制一排。

        录制帧率取实测帧率（样本足够时），否则取视频源帧率，再否则取配置默认值。

        Args:
            experiment_id: 试验标识。
            row_index: 排号 1–10。

        Returns:
            录制完成后的最终文件路径。

        Raises:
            PipelineStateError: 管线未启动。
            RecorderError: 已有进行中的段或写入器打不开。
        """
        self._require_running()
        return self._recorder.start_segment(experiment_id, row_index, fps=self._record_fps())

    def stop_segment(self) -> SegmentRecord:
        """结束当前录制段并返回记录。

        Returns:
            录制记录。断流等原因被中断的段 ``interrupted=True``。

        Raises:
            NoActiveSegmentError: 没有进行中的段。
            RecorderError: 段内没有帧或写盘失败。
        """
        return self._recorder.stop_segment()

    def _record_fps(self) -> float | None:
        measured = self._quality.measured_fps
        if (
            measured is not None
            and _DEFAULT_RECORD_FPS_FLOOR <= measured <= _DEFAULT_RECORD_FPS_CEIL
        ):
            return round(measured, 2)
        info = self._info
        if info is not None and info.is_live and info.fps:
            return info.fps
        return None

    # ------------------------------------------------------------------ 抓帧

    def mark_flip_start(
        self, started_at: datetime, row_index: int | None = None, *, n: int | None = None
    ) -> None:
        """标记翻转开始，保护翻转前最近的帧。参见 :meth:`SnapshotGrabber.mark_flip_start`。

        Args:
            started_at: 翻转开始时间（带时区）。
            row_index: 排号。
            n: 保护的帧数，默认配置的 ``frames_per_side``。

        Raises:
            PipelineStateError: 管线未启动。
        """
        self._require_running()
        self._snapshots.mark_flip_start(started_at, row_index, n=n)

    def grab(
        self,
        row_index: int,
        n: int | None = None,
        settle_s: float | None = None,
        *,
        experiment_id: str,
        flip_completed_at: datetime,
        flip_started_at: datetime | None = None,
        timeout_s: float | None = None,
        image_format: Literal["png", "jpg"] | None = None,
    ) -> SnapshotResult:
        """抓取翻转前后各 n 帧并落盘（阻塞）。参见 :meth:`SnapshotGrabber.grab`。

        Args:
            row_index: 排号。
            n: 每侧帧数，None 时用配置的 ``frames_per_side``。
            settle_s: 稳定时间。
            experiment_id: 试验标识。
            flip_completed_at: 翻转结束时间。
            flip_started_at: 翻转开始时间，默认使用 :meth:`mark_flip_start` 登记的值。
            timeout_s: 等待翻转后帧的最长时间。
            image_format: 图像格式。

        Returns:
            抓帧结果。

        Raises:
            PipelineStateError: 管线未启动。
            InsufficientFramesError: 帧数不足。
        """
        self._require_running()
        return self._snapshots.grab(
            row_index,
            n or self._config.snapshot.frames_per_side,
            settle_s,
            experiment_id=experiment_id,
            flip_completed_at=flip_completed_at,
            flip_started_at=flip_started_at,
            timeout_s=timeout_s,
            image_format=image_format,
        )

    def grab_async(
        self,
        row_index: int,
        n: int | None = None,
        settle_s: float | None = None,
        *,
        experiment_id: str,
        flip_completed_at: datetime,
        flip_started_at: datetime | None = None,
        timeout_s: float | None = None,
        image_format: Literal["png", "jpg"] | None = None,
    ) -> Future[SnapshotResult]:
        """同 :meth:`grab`，但在工作线程中执行，立即返回 ``Future``。

        Returns:
            抓帧结果的 ``Future``。
        """
        self._require_running()
        return self._snapshots.grab_async(
            row_index,
            n or self._config.snapshot.frames_per_side,
            settle_s,
            experiment_id=experiment_id,
            flip_completed_at=flip_completed_at,
            flip_started_at=flip_started_at,
            timeout_s=timeout_s,
            image_format=image_format,
        )

    def _require_running(self) -> None:
        if not self._running:
            raise PipelineStateError("视频管线未启动")
