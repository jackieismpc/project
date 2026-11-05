"""质量监控：黑屏、过曝、卡帧、实测帧率、丢帧、看门狗；每种状况只在出现和恢复时各发一次。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import numpy as np

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.config import QualityConfig
from gelinspect.services.video.events import VideoEvent, VideoEventCode, VideoEventLevel
from gelinspect.services.video.quality import QualityMonitor
from tests.services.video.helpers import T0, wait_until


class StepClock:
    """线程安全、手动推进的时钟。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t = 1000.0

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._t += seconds

    def monotonic(self) -> float:
        with self._lock:
            return self._t

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.monotonic())


def flat(value: int, k: int, size: int = 64) -> VideoFrame:
    return VideoFrame(
        image=np.full((size, size, 3), value, np.uint8),
        sequence=k,
        captured_at=T0 + timedelta(seconds=k * 0.1),
        monotonic_s=k * 0.1,
    )


def noisy(k: int, seed: int | None = None) -> VideoFrame:
    rng = np.random.default_rng(k if seed is None else seed)
    return VideoFrame(
        image=rng.integers(80, 160, (64, 64, 3), dtype=np.uint8),
        sequence=k,
        captured_at=T0 + timedelta(seconds=k * 0.1),
        monotonic_s=k * 0.1,
    )


def monitor(
    events: list[VideoEvent], clock: StepClock | None = None, **overrides: object
) -> QualityMonitor:
    config = QualityConfig(check_every_n_frames=1, tick_interval_s=0.01, **overrides)  # type: ignore[arg-type]
    return QualityMonitor(config, clock=clock, emit=events.append)


def codes(events: list[VideoEvent], active: bool = True) -> list[VideoEventCode]:
    return [e.code for e in events if e.active is active]


def test_black_frame_raises_once_and_recovers() -> None:
    events: list[VideoEvent] = []
    mon = monitor(events)
    mon.start()
    try:
        for k in range(5):
            mon.offer(flat(0, k))
            time.sleep(0.01)
        assert wait_until(lambda: VideoEventCode.BLACK_FRAME in codes(events))
        time.sleep(0.05)
        assert codes(events).count(VideoEventCode.BLACK_FRAME) == 1  # 持续黑屏不刷屏
        black = next(e for e in events if e.code is VideoEventCode.BLACK_FRAME)
        assert (
            black.level is VideoEventLevel.ERROR and black.value == 0.0 and black.threshold == 8.0
        )
        mon.offer(noisy(10))
        assert wait_until(lambda: VideoEventCode.BLACK_FRAME in codes(events, active=False))
    finally:
        mon.stop(2.0)


def test_overexposed_vs_normal_bright_backlight() -> None:
    events: list[VideoEvent] = []
    mon = monitor(events)
    mon.start()
    try:
        mon.offer(flat(235, 0))  # 正常背光（亮但未饱和）不告警
        time.sleep(0.1)
        assert VideoEventCode.OVEREXPOSED not in codes(events)
        mon.offer(flat(255, 1))
        assert wait_until(lambda: VideoEventCode.OVEREXPOSED in codes(events))
    finally:
        mon.stop(2.0)


def test_frozen_frame_detection() -> None:
    events: list[VideoEvent] = []
    mon = monitor(events, frozen_samples=3)
    mon.start()
    try:
        for k in range(3):  # 画面有变化，不告警
            mon.offer(noisy(k))
            time.sleep(0.01)
        time.sleep(0.05)
        assert VideoEventCode.FROZEN_FRAME not in codes(events)
        for k in range(3, 9):  # 连续相同的画面
            mon.offer(noisy(k, seed=99))
            time.sleep(0.01)
        assert wait_until(lambda: VideoEventCode.FROZEN_FRAME in codes(events))
        mon.offer(noisy(50))
        assert wait_until(lambda: VideoEventCode.FROZEN_FRAME in codes(events, active=False))
    finally:
        mon.stop(2.0)


def test_measured_fps_and_low_fps_event() -> None:
    clock = StepClock()
    events: list[VideoEvent] = []
    mon = monitor(events, clock, fps_window_s=10.0, low_fps_ratio=0.5)
    mon.configure(10.0)
    mon.start()
    try:
        for k in range(20):  # 10 fps
            mon.offer(noisy(k))
            clock.advance(0.1)
        fps = mon.measured_fps
        assert fps is not None and abs(fps - 10.0) < 0.2
        time.sleep(0.1)
        assert VideoEventCode.LOW_FPS not in codes(events)
        for k in range(20, 40):  # 降到 2 fps
            mon.offer(noisy(k))
            clock.advance(0.5)
        assert wait_until(lambda: VideoEventCode.LOW_FPS in codes(events))
        low = next(e for e in events if e.code is VideoEventCode.LOW_FPS)
        assert low.threshold == 5.0 and low.value is not None and low.value < 5.0
    finally:
        mon.stop(2.0)


def test_driver_dropped_frames_from_sequence_gaps() -> None:
    events: list[VideoEvent] = []
    mon = monitor(events)
    mon.start()
    try:
        for k in (0, 1, 2, 7, 8, 12):  # 缺 3–6 和 9–11，共 7 帧
            mon.offer(noisy(k))
        assert wait_until(lambda: VideoEventCode.FRAMES_DROPPED in codes(events))
        assert mon.stats().driver_dropped_frames == 7
        dropped = [e for e in events if e.code is VideoEventCode.FRAMES_DROPPED]
        assert sum(e.value or 0 for e in dropped) == 7
    finally:
        mon.stop(2.0)


def test_watchdog_stall_and_recovery() -> None:
    clock = StepClock()
    events: list[VideoEvent] = []
    mon = monitor(events, clock, watchdog_timeout_s=3.0)
    mon.start()
    try:
        mon.offer(noisy(0))
        clock.advance(1.0)
        time.sleep(0.05)
        assert VideoEventCode.STREAM_STALLED not in codes(events)
        clock.advance(5.0)  # 6 秒没有新帧
        assert wait_until(lambda: VideoEventCode.STREAM_STALLED in codes(events))
        stalled = next(e for e in events if e.code is VideoEventCode.STREAM_STALLED)
        assert stalled.level is VideoEventLevel.ERROR
        assert stalled.value is not None and stalled.value > 3.0
        mon.offer(noisy(1))
        assert wait_until(lambda: VideoEventCode.STREAM_STALLED in codes(events, active=False))
        assert codes(events).count(VideoEventCode.STREAM_STALLED) == 1
    finally:
        mon.stop(2.0)


def test_watchdog_paused_during_reconnect() -> None:
    clock = StepClock()
    events: list[VideoEvent] = []
    mon = monitor(events, clock, watchdog_timeout_s=1.0)
    mon.start()
    try:
        mon.offer(noisy(0))
        mon.pause_watchdog()
        clock.advance(10.0)
        time.sleep(0.1)
        assert VideoEventCode.STREAM_STALLED not in codes(events)
        mon.resume_watchdog()
        time.sleep(0.05)
        assert VideoEventCode.STREAM_STALLED not in codes(events)  # 恢复后重新计时
    finally:
        mon.stop(2.0)


def test_offer_is_cheap_and_analysis_is_decimated() -> None:
    events: list[VideoEvent] = []
    config = QualityConfig(check_every_n_frames=10, tick_interval_s=0.05)
    mon = QualityMonitor(config, emit=events.append)
    mon.start()
    try:
        frames = [noisy(k) for k in range(100)]  # 造帧的耗时不算进 offer
        started = time.monotonic()
        for item in frames:
            mon.offer(item)
        assert time.monotonic() - started < 0.5
        assert wait_until(lambda: mon.stats().analyzed >= 1)
        time.sleep(0.1)
        assert mon.stats().analyzed <= 10
        assert mon.stats().frames_received == 100
    finally:
        mon.stop(2.0)
