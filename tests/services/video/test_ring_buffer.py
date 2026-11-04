"""环形缓冲：字节上限、淘汰顺序、pin 保护、pin 硬上限、并发读写。"""

from __future__ import annotations

import threading
from datetime import timedelta

import numpy as np
import pytest

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.events import VideoEvent, VideoEventCode
from gelinspect.services.video.ring_buffer import FrameRingBuffer
from tests.services.video.helpers import T0, frame_number, stamped_image

FRAME_BYTES = 10 * 10 * 3


def make_frame(k: int, *, dt: float = 0.1) -> VideoFrame:
    base = np.zeros((10, 10, 3), np.uint8)
    return VideoFrame(
        image=stamped_image(base, k),
        sequence=k,
        captured_at=T0 + timedelta(seconds=k * dt),
        monotonic_s=k * dt,
    )


def ts(k: int, dt: float = 0.1) -> float:
    return (T0 + timedelta(seconds=k * dt)).timestamp()


def numbers(frames: list[VideoFrame]) -> list[int]:
    return [frame_number(f.image) for f in frames]


def test_byte_limit_evicts_oldest_first() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 5)
    for k in range(12):
        buffer.put(make_frame(k))
    stats = buffer.stats()
    assert stats.frames == 5
    assert stats.bytes_used == FRAME_BYTES * 5
    assert stats.evicted == 7
    assert numbers(buffer.frames_from(0.0)) == [7, 8, 9, 10, 11]
    latest = buffer.latest()
    assert latest is not None and latest.sequence == 11


def test_limit_counts_bytes_not_frames() -> None:
    """大帧和小帧混合时，按字节而不是按帧数淘汰。"""
    buffer = FrameRingBuffer(1000)
    small = np.zeros((5, 5, 3), np.uint8)  # 75 字节
    big = np.zeros((20, 20, 3), np.uint8)  # 1200 字节，超过上限
    buffer.put(VideoFrame(image=small, sequence=0, captured_at=T0, monotonic_s=0))
    buffer.put(
        VideoFrame(image=big, sequence=1, captured_at=T0 + timedelta(seconds=1), monotonic_s=1)
    )
    # 单帧超限时仍保留最新一帧，旧帧被淘汰
    assert buffer.stats().frames == 1
    assert buffer.latest() is not None and buffer.latest().sequence == 1  # type: ignore[union-attr]


def test_queries_by_timestamp() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 100)
    for k in range(20):
        buffer.put(make_frame(k))
    assert numbers(buffer.frames_before(ts(10), 3)) == [7, 8, 9]  # 严格早于
    assert numbers(buffer.frames_before(ts(10) + 1e-6, 2)) == [9, 10]
    assert numbers(buffer.frames_from(ts(15))) == [15, 16, 17, 18, 19]
    assert numbers(buffer.frames_from(ts(15), limit=2)) == [15, 16]
    assert buffer.frames_before(ts(0), 3) == []
    assert numbers(buffer.frames_before(ts(2), 5)) == [0, 1]  # 不足 n 帧时返回已有的


def test_pin_protects_frames_beyond_normal_limit() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 5, hard_limit_bytes=FRAME_BYTES * 50)
    for k in range(5):
        buffer.put(make_frame(k))
    pin = buffer.pin(ts(2))  # 保护 2 号及之后
    for k in range(5, 20):
        buffer.put(make_frame(k))
    # 0、1 号未受保护，被淘汰；2 号起全部保留，超过常规上限
    assert numbers(buffer.frames_from(0.0))[0] == 2
    assert buffer.stats().frames == 18
    assert buffer.stats().forced_evictions == 0
    buffer.unpin(pin)
    assert buffer.stats().frames == 5  # 解除保护后立即回收
    assert numbers(buffer.frames_from(0.0)) == [15, 16, 17, 18, 19]
    buffer.unpin(pin)  # 重复解除无害


def test_bounded_pin_only_protects_its_window() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 4, hard_limit_bytes=FRAME_BYTES * 20)
    for k in range(10):
        buffer.put(make_frame(k))
    pin_id = buffer.pin_last_before(ts(10), 3)  # 保护 7、8、9
    assert pin_id is not None
    for k in range(10, 30):
        buffer.put(make_frame(k))
    # 受保护的 7、8、9 加最新一帧恰好占满常规上限；中间的未受保护帧都被淘汰了
    assert numbers(buffer.frames_from(0.0)) == [7, 8, 9, 29]
    assert buffer.stats().forced_evictions == 0
    buffer.unpin(pin_id)
    buffer.put(make_frame(30))
    assert numbers(buffer.frames_from(0.0)) == [8, 9, 29, 30]


def test_pin_hard_limit_alerts_and_evicts_protected_frames() -> None:
    events: list[VideoEvent] = []
    buffer = FrameRingBuffer(
        FRAME_BYTES * 4, hard_limit_bytes=FRAME_BYTES * 8, on_event=events.append
    )
    buffer.pin(ts(0))  # 保护一切
    for k in range(30):
        buffer.put(make_frame(k))
    stats = buffer.stats()
    assert stats.bytes_used <= FRAME_BYTES * 8  # 绝不无限增长
    assert stats.peak_bytes <= FRAME_BYTES * 8 + FRAME_BYTES
    assert stats.forced_evictions > 0
    alerts = [e for e in events if e.code is VideoEventCode.PIN_OVERFLOW]
    assert len(alerts) == 1  # 一个溢出过程只告警一次
    assert alerts[0].threshold == FRAME_BYTES * 8
    # 被强制淘汰的是最旧的受保护帧，最新的帧还在
    latest = buffer.latest()
    assert latest is not None and latest.sequence == 29


def test_pin_overflow_alert_rearms_after_recovery() -> None:
    events: list[VideoEvent] = []
    buffer = FrameRingBuffer(
        FRAME_BYTES * 2, hard_limit_bytes=FRAME_BYTES * 4, on_event=events.append
    )
    pin = buffer.pin(ts(0))
    for k in range(10):
        buffer.put(make_frame(k))
    buffer.unpin(pin)
    buffer.pin(ts(10))
    for k in range(10, 20):
        buffer.put(make_frame(k))
    assert len([e for e in events if e.code is VideoEventCode.PIN_OVERFLOW]) == 2


def test_wait_for_frames_and_cancel() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 100)
    result: list[tuple[list[VideoFrame], bool]] = []
    waiter = threading.Thread(
        target=lambda: result.append(buffer.wait_for_frames_from(ts(5), 3, 3.0))
    )
    waiter.start()
    for k in range(8):
        buffer.put(make_frame(k))
    waiter.join(3)
    frames, cancelled = result[0]
    assert numbers(frames) == [5, 6, 7] and not cancelled

    # 超时：返回已有的帧（不足 n），不抛异常
    frames, cancelled = buffer.wait_for_frames_from(ts(100), 3, 0.05)
    assert frames == [] and not cancelled

    # 取消：立即返回
    out: list[tuple[list[VideoFrame], bool]] = []
    waiter = threading.Thread(
        target=lambda: out.append(buffer.wait_for_frames_from(ts(100), 3, 30.0))
    )
    waiter.start()
    buffer.cancel_waits()
    waiter.join(2)
    assert not waiter.is_alive() and out[0][1] is True


def test_clock_regression_keeps_index_sorted() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 100)
    buffer.put(make_frame(5))
    buffer.put(make_frame(2))  # 墙钟被回拨
    assert buffer.stats().clock_regressions == 1
    assert len(buffer.frames_from(0.0)) == 2


def test_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        FrameRingBuffer(0)
    with pytest.raises(ValueError):
        FrameRingBuffer(100, hard_limit_bytes=50)


def test_concurrent_producer_and_readers() -> None:
    buffer = FrameRingBuffer(FRAME_BYTES * 30, hard_limit_bytes=FRAME_BYTES * 60)
    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                frames = buffer.frames_before(ts(10**6), 10)
                seq = numbers(frames)
                assert seq == sorted(seq)
                assert len(set(seq)) == len(seq)
                buffer.stats()
                pin = buffer.pin_last_before(ts(10**6), 3)
                if pin is not None:
                    buffer.unpin(pin)
        except BaseException as exc:
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    for k in range(3000):
        buffer.put(make_frame(k, dt=0.001))
    stop.set()
    for thread in readers:
        thread.join(5)
    assert errors == []
    stats = buffer.stats()
    assert stats.total_put == 3000
    assert stats.bytes_used <= FRAME_BYTES * 30
    assert stats.frames == stats.bytes_used // FRAME_BYTES
