"""预览：限帧率、缩放、latest-wins、慢回调只丢预览帧。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from functools import partial

import numpy as np

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.config import PreviewConfig
from gelinspect.services.video.events import VideoEvent, VideoEventCode
from gelinspect.services.video.preview import PreviewFrame, PreviewWorker, scaled_size
from tests.services.video.helpers import T0, stamped_image, wait_until


class StepClock:
    """手动推进的时钟。"""

    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.t)


def frame(k: int, width: int = 200, height: int = 100) -> VideoFrame:
    return VideoFrame(
        image=stamped_image(np.full((height, width, 3), 90, np.uint8), k),
        sequence=k,
        captured_at=T0 + timedelta(seconds=k * 0.05),
        monotonic_s=k * 0.05,
    )


def test_scaled_size() -> None:
    assert scaled_size(4000, 3000, 960) == (960, 720)
    assert scaled_size(3000, 4000, 960) == (720, 960)
    assert scaled_size(640, 480, 960) == (640, 480)  # 不放大
    assert scaled_size(10000, 1, 960) == (960, 1)


def test_rate_limited_and_resized() -> None:
    clock = StepClock()
    received: list[PreviewFrame] = []
    worker = PreviewWorker(PreviewConfig(max_fps=15, long_side=100), received.append, clock=clock)
    worker.start()
    try:
        for k in range(30):  # 30 帧在 1.5 秒内到达（20 fps），上限 15 fps
            worker.offer(frame(k))
            time.sleep(0.003)
            clock.t += 0.05
        assert wait_until(lambda: worker.delivered >= 1)
        time.sleep(0.05)
    finally:
        worker.stop(2.0)
    assert worker.offered == 30
    assert worker.skipped > 0
    assert worker.skipped + worker.delivered + worker.dropped == 30
    # 20 fps 输入、15 fps 上限：间隔不足 1/15 秒的帧被跳过，实际约隔帧取一帧；绝不超过 15 fps
    assert 10 <= worker.delivered + worker.dropped <= 24
    first = received[0]
    assert first.image.shape == (50, 100, 3)
    assert (first.source_width, first.source_height) == (200, 100)
    assert not first.image.flags.writeable


def test_latest_wins_with_slow_callback() -> None:
    gate = threading.Event()
    received: list[int] = []

    def slow(preview: PreviewFrame) -> None:
        received.append(preview.sequence)
        gate.wait(5)

    worker = PreviewWorker(PreviewConfig(max_fps=100000), slow)
    worker.start()
    try:
        worker.offer(frame(0))
        assert wait_until(lambda: received == [0])  # 回调卡在第 0 帧
        started = time.monotonic()
        for k in range(1, 200):
            worker.offer(frame(k))
            time.sleep(0.0005)
        assert time.monotonic() - started < 2.0  # offer 不被卡住的回调阻塞
        gate.set()
        assert wait_until(lambda: received[-1] == 199)
    finally:
        gate.set()
        worker.stop(2.0)
    assert worker.dropped > 150  # 中间的预览帧被覆盖丢弃
    assert received == sorted(received)  # 顺序不乱，最新的一定送达


def test_callback_exception_does_not_kill_worker() -> None:
    events: list[VideoEvent] = []
    calls: list[int] = []

    def bad(preview: PreviewFrame) -> None:
        calls.append(preview.sequence)
        raise RuntimeError("界面回调炸了")

    worker = PreviewWorker(PreviewConfig(max_fps=1000), bad, emit=events.append)
    worker.start()
    try:
        for k in range(5):
            worker.offer(frame(k))
            assert wait_until(partial(calls.__contains__, k))
        assert worker.errors == 5
    finally:
        worker.stop(2.0)
    codes = [e.code for e in events]
    assert codes.count(VideoEventCode.CALLBACK_ERROR) == 1  # 限频，不刷屏


def test_stop_is_idempotent_and_restartable() -> None:
    got: list[int] = []
    worker = PreviewWorker(PreviewConfig(max_fps=1000), lambda p: got.append(p.sequence))
    for _ in range(3):
        worker.start()
        worker.start()
        worker.offer(frame(len(got)))
        assert wait_until(lambda: len(got) >= 1)
        assert worker.stop(2.0) and worker.stop(2.0)
        got.clear()
