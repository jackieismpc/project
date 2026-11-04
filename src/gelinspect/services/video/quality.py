"""图像与流质量监控：黑屏、过曝、卡帧、实测帧率、丢帧和断流看门狗。

``offer`` 由采集线程调用，只做计数（O(1)）；每 N 帧把一帧放入单槽，由监控线程缩小后分析。
看门狗和帧率检查在同一线程里周期性执行。每种状况只在「出现」和「恢复」时各发一次事件，
不会刷屏。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.config import QualityConfig
from gelinspect.services.video.events import (
    EventSink,
    VideoEvent,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)
from gelinspect.services.video.latest_slot import LatestSlotWorker
from gelinspect.services.video.preview import resize_to_long_side

logger = logging.getLogger("gelinspect.services.video.quality")

_MIN_FPS_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class QualityStats:
    """质量统计快照。

    Attributes:
        frames_received: 收到的帧数。
        driver_dropped_frames: 帧序号跳号推算出的驱动层掉帧数。
        measured_fps: 滑动窗口内的实测帧率，样本不足时为 None。
        analyzed: 已做图像分析的帧数。
        active_conditions: 当前处于「出现」状态的事件码。
    """

    frames_received: int
    driver_dropped_frames: int
    measured_fps: float | None
    analyzed: int
    active_conditions: frozenset[VideoEventCode]


class QualityMonitor:
    """质量监控，同时是一个帧消费者（实现 ``offer``）。"""

    def __init__(
        self,
        config: QualityConfig,
        *,
        clock: Clock | None = None,
        emit: EventSink | None = None,
    ) -> None:
        """创建监控，不启动线程。

        Args:
            config: 阈值配置。
            clock: 时钟，用于帧率窗口和看门狗。
            emit: 事件接收函数。
        """
        self._config = config
        self._clock: Clock = clock or SystemClock()
        self._emit_sink = emit
        self._lock = threading.Lock()
        self._times: deque[float] = deque()
        self._frames = 0
        self._dropped = 0
        self._gap_pending = 0
        self._last_sequence: int | None = None
        self._last_frame_t = self._clock.monotonic()
        self._watchdog_paused = False
        self._expected_fps: float | None = config.expected_fps
        self._active: set[VideoEventCode] = set()
        self._previous: NDArray[np.uint8] | None = None
        self._frozen_count = 0
        self._analyzed = 0
        self._worker: LatestSlotWorker[VideoFrame] = LatestSlotWorker(
            "video-quality",
            self._analyze,
            self._on_error,
            tick=self._tick,
            tick_interval_s=config.tick_interval_s,
        )

    # ------------------------------------------------------------------ 控制

    def configure(self, source_fps: float | None) -> None:
        """设置视频源报告的帧率，作为未配置 ``expected_fps`` 时的期望帧率。

        Args:
            source_fps: 视频源帧率，未知时为 None。
        """
        with self._lock:
            self._expected_fps = self._config.expected_fps or source_fps

    def start(self) -> None:
        """启动监控线程，并重置计数和看门狗。"""
        with self._lock:
            self._reset_locked()
        self._worker.start()

    def stop(self, timeout_s: float) -> bool:
        """停止监控线程。

        Args:
            timeout_s: 等待退出的最长时间。

        Returns:
            线程是否已退出。
        """
        return self._worker.stop(timeout_s)

    def pause_watchdog(self) -> None:
        """暂停看门狗（重连期间调用，避免与断流事件重复告警）。"""
        with self._lock:
            self._watchdog_paused = True
            self._times.clear()

    def resume_watchdog(self) -> None:
        """恢复看门狗并重置序号跟踪和帧率窗口（重连成功后调用）。"""
        with self._lock:
            self._watchdog_paused = False
            self._last_frame_t = self._clock.monotonic()
            self._last_sequence = None
            self._times.clear()
            self._previous = None
            self._frozen_count = 0

    def _reset_locked(self) -> None:
        self._times.clear()
        self._frames = 0
        self._dropped = 0
        self._gap_pending = 0
        self._last_sequence = None
        self._last_frame_t = self._clock.monotonic()
        self._watchdog_paused = False
        self._active.clear()
        self._previous = None
        self._frozen_count = 0
        self._analyzed = 0

    # ------------------------------------------------------------------ 查询

    @property
    def measured_fps(self) -> float | None:
        """滑动窗口内的实测帧率，样本不足时为 None。"""
        with self._lock:
            return self._fps_locked(self._clock.monotonic())

    def stats(self) -> QualityStats:
        """返回统计快照。

        Returns:
            :class:`QualityStats`。
        """
        with self._lock:
            return QualityStats(
                frames_received=self._frames,
                driver_dropped_frames=self._dropped,
                measured_fps=self._fps_locked(self._clock.monotonic()),
                analyzed=self._analyzed,
                active_conditions=frozenset(self._active),
            )

    def _fps_locked(self, now: float) -> float | None:
        window = self._config.fps_window_s
        while self._times and now - self._times[0] > window:
            self._times.popleft()
        if len(self._times) < _MIN_FPS_SAMPLES:
            return None
        span = self._times[-1] - self._times[0]
        if span <= 0:
            return None
        return (len(self._times) - 1) / span

    # ------------------------------------------------------------------ 帧入口

    def offer(self, frame: VideoFrame) -> None:
        """登记一帧，由采集线程调用，O(1) 且非阻塞。

        Args:
            frame: 视频帧。
        """
        now = self._clock.monotonic()
        recovered = False
        with self._lock:
            self._frames += 1
            self._times.append(now)
            if self._last_sequence is not None and frame.sequence > self._last_sequence + 1:
                missed = frame.sequence - self._last_sequence - 1
                self._dropped += missed
                self._gap_pending += missed
            self._last_sequence = frame.sequence
            self._last_frame_t = now
            if VideoEventCode.STREAM_STALLED in self._active:
                recovered = True
            analyze = self._config.enabled and self._frames % self._config.check_every_n_frames == 0
        if recovered:
            self._clear(VideoEventCode.STREAM_STALLED, "视频流已恢复")
        if analyze:
            self._worker.submit(frame)

    # ------------------------------------------------------------------ 分析线程

    def _analyze(self, frame: VideoFrame) -> None:
        small = resize_to_long_side(frame.image, self._config.analysis_long_side)
        gray = cast("NDArray[np.uint8]", cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        mean = float(gray.mean())
        saturated = float(np.count_nonzero(gray >= self._config.overexposed_level)) / gray.size
        self._analyzed += 1

        self._condition(
            VideoEventCode.BLACK_FRAME,
            mean < self._config.black_mean_max,
            VideoEventLevel.ERROR,
            f"画面过暗（平均灰度 {mean:.1f}）",
            "黑屏已恢复",
            value=mean,
            threshold=self._config.black_mean_max,
        )
        self._condition(
            VideoEventCode.OVEREXPOSED,
            saturated > self._config.overexposed_fraction,
            VideoEventLevel.WARNING,
            f"画面过曝（饱和像素占 {saturated:.0%}）",
            "过曝已恢复",
            value=saturated,
            threshold=self._config.overexposed_fraction,
        )

        previous = self._previous
        self._previous = gray
        if previous is not None and previous.shape == gray.shape:
            diff = float(np.abs(gray.astype(np.int16) - previous.astype(np.int16)).mean())
            self._frozen_count = (
                self._frozen_count + 1 if diff <= self._config.frozen_diff_max else 0
            )
            self._condition(
                VideoEventCode.FROZEN_FRAME,
                self._frozen_count >= self._config.frozen_samples,
                VideoEventLevel.ERROR,
                f"画面连续 {self._frozen_count} 次采样无变化（平均差 {diff:.3f}）",
                "卡帧已恢复",
                value=diff,
                threshold=self._config.frozen_diff_max,
            )

    def _tick(self) -> None:
        """周期任务：掉帧汇总、帧率检查、看门狗。"""
        now = self._clock.monotonic()
        with self._lock:
            gap = self._gap_pending
            self._gap_pending = 0
            fps = self._fps_locked(now)
            expected = self._expected_fps
            idle = now - self._last_frame_t
            paused = self._watchdog_paused
        if gap:
            self._emit(
                VideoEventCode.FRAMES_DROPPED,
                VideoEventLevel.WARNING,
                f"视频源掉帧 {gap} 帧（帧序号跳号）",
                value=float(gap),
            )
        if fps is not None and expected is not None:
            floor = expected * self._config.low_fps_ratio
            self._condition(
                VideoEventCode.LOW_FPS,
                fps < floor,
                VideoEventLevel.WARNING,
                f"实测帧率 {fps:.1f} fps，期望 {expected:.1f} fps",
                "帧率已恢复",
                value=fps,
                threshold=floor,
            )
        if not paused:
            self._condition(
                VideoEventCode.STREAM_STALLED,
                idle > self._config.watchdog_timeout_s,
                VideoEventLevel.ERROR,
                f"已 {idle:.1f} 秒没有收到新帧",
                "视频流已恢复",
                value=idle,
                threshold=self._config.watchdog_timeout_s,
            )

    # ------------------------------------------------------------------ 事件

    def _emit(
        self,
        code: VideoEventCode,
        level: VideoEventLevel,
        message: str,
        *,
        value: float | None = None,
        threshold: float | None = None,
        active: bool = True,
    ) -> None:
        if self._emit_sink is None:
            return
        event: VideoEvent = make_event(
            self._clock, code, level, message, value=value, threshold=threshold, active=active
        )
        self._emit_sink(event)

    def _condition(
        self,
        code: VideoEventCode,
        is_bad: bool,
        level: VideoEventLevel,
        message: str,
        recovered_message: str,
        *,
        value: float,
        threshold: float,
    ) -> None:
        """状况出现时发一次事件，恢复时发一次 ``active=False`` 事件。"""
        with self._lock:
            was_active = code in self._active
            if is_bad and not was_active:
                self._active.add(code)
            elif not is_bad and was_active:
                self._active.discard(code)
            else:
                return
        if is_bad:
            self._emit(code, level, message, value=value, threshold=threshold)
        else:
            self._emit(
                code,
                VideoEventLevel.INFO,
                recovered_message,
                value=value,
                threshold=threshold,
                active=False,
            )

    def _clear(self, code: VideoEventCode, message: str) -> None:
        with self._lock:
            if code not in self._active:
                return
            self._active.discard(code)
        self._emit(code, VideoEventLevel.INFO, message, active=False)

    def _on_error(self, exc: Exception) -> None:
        logger.error("质量检查抛出异常（线程继续运行）：%r", exc, exc_info=exc)
