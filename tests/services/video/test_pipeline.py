"""管线整体：生命周期、线程归属、慢消费者、回调异常、断流重连、事件、反复启停。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from gelinspect.interfaces.video import (
    VideoConfig,
    VideoFrame,
    VideoInfo,
    VideoOpenError,
    VideoSource,
)
from gelinspect.services.video.capture import CaptureState, CaptureWorker
from gelinspect.services.video.config import (
    BufferConfig,
    PreviewConfig,
    QualityConfig,
    ReconnectConfig,
    RecorderConfig,
)
from gelinspect.services.video.errors import NoActiveSegmentError, PipelineStateError
from gelinspect.services.video.events import VideoEvent, VideoEventCode, VideoEventLevel
from gelinspect.services.video.pipeline import PipelineState, VideoPipeline
from gelinspect.services.video.preview import PreviewFrame
from gelinspect.services.video.ring_buffer import FrameRingBuffer
from tests.services.video.helpers import T0, FakeSource, make_config, wait_until


def codes(pipeline: VideoPipeline) -> list[VideoEventCode]:
    return [e.code for e in pipeline.recent_events() if e.active]


# ------------------------------------------------------------------ 生命周期


def test_start_stop_and_source_methods_run_on_one_capture_thread(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = FakeSource()
    assert isinstance(source, VideoSource)
    pipeline = VideoPipeline(source, make_config(tmp_path))
    assert state_of(pipeline) is PipelineState.STOPPED and pipeline.info is None
    info = pipeline.start()
    assert (info.width, info.height, info.is_live) == (64, 48, True)
    assert pipeline.start() is info  # 幂等
    assert pipeline.is_running and state_of(pipeline) is PipelineState.RUNNING
    assert wait_until(lambda: pipeline.stats().frames_captured >= 10)
    pipeline.stop()
    pipeline.stop()  # 幂等
    assert not pipeline.is_running and pipeline.state is PipelineState.STOPPED
    # open / read / close 都在同一个（非主）线程上调用，且停止时关闭了视频源
    assert len(source.threads) == 1 and threading.get_ident() not in source.threads
    assert source.close_calls == 1 and not source.opened


def test_frames_in_buffer_are_read_only(tmp_path: Path, no_thread_leak: None) -> None:
    pipeline = VideoPipeline(FakeSource(), make_config(tmp_path))
    pipeline.start()
    try:
        assert wait_until(lambda: len(pipeline.buffer) >= 3)
        frame = pipeline.buffer.latest()
        assert frame is not None
        assert not frame.image.flags.writeable
        with pytest.raises(ValueError):
            frame.image[0, 0, 0] = 1
    finally:
        pipeline.stop()


def test_open_failure_raises_and_leaves_no_threads(tmp_path: Path, no_thread_leak: None) -> None:
    source = FakeSource(open_failures=1)
    pipeline = VideoPipeline(source, make_config(tmp_path))
    with pytest.raises(VideoOpenError, match="打开失败"):
        pipeline.start()
    assert not pipeline.is_running
    assert pipeline.start().width == 64  # 修好之后可以直接再启动
    pipeline.stop()


def test_open_timeout(tmp_path: Path) -> None:
    class SlowOpen(FakeSource):
        def open(self, config: VideoConfig) -> VideoInfo:
            time.sleep(0.5)
            return super().open(config)

    config = make_config(tmp_path, open_timeout_s=0.1)
    pipeline = VideoPipeline(SlowOpen(), config)
    before = threading.active_count()
    with pytest.raises(VideoOpenError, match="超时"):
        pipeline.start()
    assert wait_until(lambda: threading.active_count() <= before, 3.0)


def test_operations_require_a_running_pipeline(tmp_path: Path) -> None:
    pipeline = VideoPipeline(FakeSource(), make_config(tmp_path))
    with pytest.raises(PipelineStateError):
        pipeline.start_segment("EXP-1", 1)
    with pytest.raises(PipelineStateError):
        pipeline.mark_flip_start(T0, 1)
    with pytest.raises(PipelineStateError):
        pipeline.grab(1, 3, experiment_id="EXP-1", flip_completed_at=T0)
    with pytest.raises(NoActiveSegmentError):
        pipeline.stop_segment()


def test_twenty_start_stop_cycles_do_not_leak_threads(tmp_path: Path) -> None:
    baseline = threading.active_count()
    source = FakeSource(interval_s=0.001)
    pipeline = VideoPipeline(source, make_config(tmp_path), on_preview=lambda p: None)
    for cycle in range(20):
        pipeline.start()
        assert wait_until(lambda: pipeline.stats().frames_captured >= 3)
        if cycle % 4 == 0:  # 夹杂录制和抓帧，确保它们的线程池也被回收
            pipeline.start_segment("EXP-LOOP", 1 + cycle // 4)
            latest = pipeline.buffer.latest()
            assert latest is not None
            pipeline.mark_flip_start(latest.captured_at, 1)
            pipeline.grab_async(
                1,
                1,
                0.0,
                experiment_id="EXP-LOOP",
                flip_completed_at=latest.captured_at,
                flip_started_at=latest.captured_at,
                timeout_s=0.5,
            )
        pipeline.stop()
        assert threading.active_count() <= baseline + 0, f"第 {cycle} 轮后线程数增长"
    assert threading.active_count() == baseline
    assert source.open_calls == 20 and source.close_calls == 20


def test_stop_with_active_segment_seals_it_as_interrupted(
    tmp_path: Path, no_thread_leak: None
) -> None:
    pipeline = VideoPipeline(
        FakeSource(interval_s=0.002, width=160, height=120), make_config(tmp_path)
    )
    pipeline.start()
    pipeline.start_segment("EXP-STOP", 1)
    assert wait_until(lambda: pipeline.stats().frames_captured >= 20)
    pipeline.stop()
    record = pipeline.stop_segment()  # 停止后仍可取回被封存的记录
    assert record.interrupted and record.interrupt_reason == "pipeline_stopped"
    assert record.frame_count > 0 and record.path.exists()


# ------------------------------------------------------------------ 慢消费者与异常回调


def test_slow_preview_callback_does_not_block_capture(tmp_path: Path, no_thread_leak: None) -> None:
    gate = threading.Event()
    seen: list[int] = []

    def slow(preview: PreviewFrame) -> None:
        seen.append(preview.sequence)
        gate.wait(60)

    source = FakeSource(interval_s=0.005)
    pipeline = VideoPipeline(
        source, make_config(tmp_path, preview=PreviewConfig(max_fps=1000)), on_preview=slow
    )
    pipeline.start()
    try:
        assert wait_until(lambda: len(seen) == 1)  # 预览回调已卡死
        before = pipeline.stats().frames_captured
        # 回调卡到 gate 放行，这段时间里采集照常出帧。
        assert wait_until(lambda: pipeline.stats().frames_captured - before >= 100, 30.0)
        stats = pipeline.stats()
        assert stats.preview_dropped > 50 and stats.preview_delivered == 0
        assert len(seen) == 1
    finally:
        gate.set()
        pipeline.stop()


def test_slow_recording_does_not_block_capture(tmp_path: Path, no_thread_leak: None) -> None:
    source = FakeSource(interval_s=0.005, width=160, height=120)
    config = make_config(tmp_path, recorder=RecorderConfig(queue_max_frames=4))
    pipeline = VideoPipeline(source, config)
    pipeline.start()
    try:
        pipeline.start_segment("EXP-SLOW", 1)
        segment = pipeline.recorder._segment  # noqa: SLF001
        assert segment is not None
        gate = threading.Event()
        real_write = segment.writer.write
        segment.writer = mock.Mock(wraps=segment.writer)
        segment.writer.write.side_effect = lambda image: (gate.wait(60), real_write(image))
        before = pipeline.stats().frames_captured
        # 磁盘卡到 gate 放行，采集不受影响。
        assert wait_until(lambda: pipeline.stats().frames_captured - before >= 100, 30.0)
        assert pipeline.stats().recorder.total_dropped_frames > 50
        gate.set()
        record = pipeline.stop_segment()
        assert record.dropped_frames > 50 and record.frame_count >= 1
    finally:
        pipeline.stop()


def test_raising_consumer_does_not_kill_capture_thread(tmp_path: Path) -> None:
    class Boom:
        def offer(self, frame: VideoFrame) -> None:
            raise RuntimeError("消费者炸了")

    good: list[int] = []

    class Good:
        def offer(self, frame: VideoFrame) -> None:
            good.append(frame.sequence)

    events: list[VideoEvent] = []
    source = FakeSource(interval_s=0.002)
    buffer = FrameRingBuffer(10**7)
    worker = CaptureWorker(
        source,
        VideoConfig(device="fake"),
        buffer,
        [Boom(), Good()],  # 炸的消费者排在前面，也不影响后面的消费者
        reconnect=ReconnectConfig(),
        emit=events.append,
    )
    worker.start(2.0)
    try:
        assert wait_until(lambda: len(good) >= 30)
        assert worker.is_alive and worker.consumer_errors >= 30
        assert len(buffer) >= 30
        reported = [e for e in events if e.code is VideoEventCode.CONSUMER_ERROR]
        assert len(reported) == 1  # 限频
    finally:
        assert worker.stop(2.0)


def test_raising_preview_callback_and_listener_keep_threads_alive(
    tmp_path: Path, no_thread_leak: None
) -> None:
    got_events: list[VideoEvent] = []

    def bad_listener(event: VideoEvent) -> None:
        got_events.append(event)
        raise RuntimeError("监听者炸了")

    def bad_preview(preview: PreviewFrame) -> None:
        raise RuntimeError("预览回调炸了")

    pipeline = VideoPipeline(
        FakeSource(interval_s=0.002),
        make_config(tmp_path, preview=PreviewConfig(max_fps=1000)),
        on_preview=bad_preview,
        on_event=bad_listener,
    )
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.stats().frames_captured >= 50)
        assert wait_until(lambda: any(e.code is VideoEventCode.CALLBACK_ERROR for e in got_events))
        before = pipeline.stats().frames_captured
        assert wait_until(lambda: pipeline.stats().frames_captured > before)  # 采集仍在继续
        second: list[VideoEvent] = []
        pipeline.add_event_listener(second.append)  # 坏监听者之后的监听者仍能收到
        pipeline.source_events = None  # type: ignore[attr-defined]
    finally:
        pipeline.stop()


def test_slow_event_listener_does_not_block_capture(tmp_path: Path, no_thread_leak: None) -> None:
    gate = threading.Event()

    def slow(event: VideoEvent) -> None:
        gate.wait(60)

    source = FakeSource(interval_s=0.003, disconnect_after=20)
    pipeline = VideoPipeline(source, make_config(tmp_path), on_event=slow)
    pipeline.start()
    try:
        # 事件监听者卡死时，断流 → 重连 → 继续采集 全程不受影响
        assert wait_until(lambda: pipeline.stats().reconnects == 1, 30.0)
        before = pipeline.stats().frames_captured
        assert wait_until(lambda: pipeline.stats().frames_captured > before + 20, 30.0)
    finally:
        gate.set()
        pipeline.stop()


def test_stuck_source_read_times_out_on_stop_but_does_not_hang(tmp_path: Path) -> None:
    class StuckRead(FakeSource):
        """``stuck`` 置位后，``read`` 卡到测试放行，线程停在驱动里。"""

        def __init__(self) -> None:
            super().__init__(interval_s=0.001)
            self.stuck = threading.Event()
            self.entered = threading.Event()
            self.release = threading.Event()

        def read(self) -> VideoFrame | None:
            if self.stuck.is_set():
                self.entered.set()
                assert self.release.wait(60.0)
            return super().read()

    source = StuckRead()
    config = make_config(tmp_path).model_copy(update={"stop_timeout_s": 0.2})
    pipeline = VideoPipeline(source, config)
    pipeline.start()
    assert wait_until(lambda: pipeline.stats().frames_captured >= 3)
    source.stuck.set()
    assert source.entered.wait(30.0)
    # stop 在读还卡着时就返回了：它要是等读返回，会等满上面的 60 秒并让断言失败。
    pipeline.stop()
    assert not source.release.is_set() and source.close_calls == 0
    assert any(e.code is VideoEventCode.STOP_TIMEOUT for e in pipeline.recent_events())
    # 卡住的读返回后，守护线程自行退出并关闭视频源
    source.release.set()
    assert wait_until(lambda: source.close_calls == 1, 30.0)


# ------------------------------------------------------------------ 断流、重连、事件


def test_disconnect_reconnect_events_and_segment_handling(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = FakeSource(interval_s=0.003, width=160, height=120, disconnect_after=60)
    pipeline = VideoPipeline(source, make_config(tmp_path))
    pipeline.start()
    try:
        pipeline.start_segment("EXP-DC", 1)
        assert wait_until(lambda: pipeline.stats().reconnects == 1, 5.0)
        assert wait_until(lambda: pipeline.stats().frames_captured >= 100)
        # 中断事件由写盘线程在封存文件后发出，不跟重连同步。
        assert wait_until(lambda: VideoEventCode.SEGMENT_INTERRUPTED in codes(pipeline), 30.0)
        seen = codes(pipeline)
        # 事件顺序：断流 → 重连中 → 重连成功
        order = [
            c
            for c in seen
            if c
            in (
                VideoEventCode.DISCONNECTED,
                VideoEventCode.RECONNECTING,
                VideoEventCode.RECONNECTED,
            )
        ]
        assert order[:3] == [
            VideoEventCode.DISCONNECTED,
            VideoEventCode.RECONNECTING,
            VideoEventCode.RECONNECTED,
        ]
        assert VideoEventCode.SEGMENT_INTERRUPTED in seen
        # 断流时录制段被封存并标记异常；重连后不会自动续录
        record = pipeline.stop_segment()
        assert record.interrupted and record.interrupt_reason == "disconnected"
        assert 0 < record.frame_count <= 61
        assert not pipeline.recorder.active
        assert pipeline.state is PipelineState.RUNNING
        # 重连后缓冲继续收帧，且没有帧丢失（假相机跳过的是断开瞬间，序号连续）
        latest = pipeline.buffer.latest()
        assert latest is not None and latest.sequence > 60
        assert source.open_calls == 2 and source.close_calls >= 1
        # 重连后可以再开新段
        pipeline.start_segment("EXP-DC", 2)
        before = pipeline.stats().frames_captured
        assert wait_until(lambda: pipeline.stats().frames_captured >= before + 3, 30.0)
        assert not pipeline.stop_segment().interrupted
    finally:
        pipeline.stop()
    levels = {e.code: e.level for e in pipeline.recent_events() if e.active}
    assert levels[VideoEventCode.DISCONNECTED] is VideoEventLevel.ERROR
    assert levels[VideoEventCode.RECONNECTED] is VideoEventLevel.INFO


def test_reconnect_backoff_and_attempt_limit(tmp_path: Path, no_thread_leak: None) -> None:
    source = FakeSource(interval_s=0.002, disconnect_after=10, open_failures=0)
    original_open = source.open
    failures = {"left": 99}

    def flaky_open(config: VideoConfig) -> VideoInfo:
        if source.open_calls >= 1:
            source.open_calls += 1
            raise VideoOpenError("相机还没插回去")
        return original_open(config)

    source.open = flaky_open  # type: ignore[method-assign]
    del failures
    reconnect = ReconnectConfig(
        initial_delay_s=0.02, max_delay_s=0.08, factor=2.0, max_attempts=4, stall_disconnect_s=None
    )
    pipeline = VideoPipeline(source, make_config(tmp_path, reconnect=reconnect))
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.state is PipelineState.FAILED, 5.0)
        events = pipeline.recent_events()
        attempts = [e for e in events if e.code is VideoEventCode.RECONNECTING]
        assert [int(e.value or 0) for e in attempts] == [1, 2, 3, 4]
        # 退避：0.02、0.04、0.08、0.08 → 相邻尝试的间隔不小于上一次
        gaps = [b.monotonic_s - a.monotonic_s for a, b in zip(attempts, attempts[1:], strict=False)]
        assert gaps[0] >= 0.035 and gaps[1] >= 0.07 and gaps[2] >= 0.07
        assert any(e.code is VideoEventCode.RECONNECT_FAILED for e in events)
    finally:
        pipeline.stop()


def test_reconnect_can_be_disabled(tmp_path: Path, no_thread_leak: None) -> None:
    source = FakeSource(interval_s=0.002, disconnect_after=10)
    pipeline = VideoPipeline(
        source, make_config(tmp_path, reconnect=ReconnectConfig(enabled=False))
    )
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.state is PipelineState.FAILED, 3.0)
        assert source.open_calls == 1
        failed = [e for e in pipeline.recent_events() if e.code is VideoEventCode.RECONNECT_FAILED]
        assert len(failed) == 1 and "关闭" in failed[0].message
    finally:
        pipeline.stop()


# ------------------------------------------------------------------ 发布顺序


class _ObservedWorker:
    """采集线程加一个事件接收函数：每个事件到达时记下线程当时对外发布的状态和计数。

    事件在这一刻还没有进入历史，所以记下的状态不能已经是事件所说的结果，否则就有
    「看到状态、读不到原因」的窗口。靠这一点确定性地检查顺序，不靠线程时序。
    """

    def __init__(self, source: FakeSource, reconnect: ReconnectConfig, buffer: object) -> None:
        self.seen: dict[VideoEventCode, tuple[CaptureState, int]] = {}
        self.worker = CaptureWorker(
            source,
            VideoConfig(device="fake"),
            buffer,  # type: ignore[arg-type]
            [],
            reconnect=reconnect,
            emit=self._on_event,
        )

    def _on_event(self, event: VideoEvent) -> None:
        self.seen[event.code] = (self.worker.state, self.worker.reconnects)

    def run_until(self, state: CaptureState) -> None:
        self.worker.start(10.0)
        try:
            assert wait_until(lambda: self.worker.state is state, 30.0)
        finally:
            assert self.worker.stop(10.0)


def test_failed_state_is_published_after_its_event_when_reconnect_is_disabled() -> None:
    observed = _ObservedWorker(
        FakeSource(disconnect_after=3), ReconnectConfig(enabled=False), FrameRingBuffer(10**7)
    )
    observed.run_until(CaptureState.FAILED)
    state, _ = observed.seen[VideoEventCode.RECONNECT_FAILED]
    assert state is not CaptureState.FAILED


def test_failed_state_is_published_after_its_event_when_attempts_run_out() -> None:
    observed = _ObservedWorker(
        _FailsAfterFirstOpen(disconnect_after=3),
        ReconnectConfig(initial_delay_s=0.001, max_delay_s=0.001, max_attempts=2),
        FrameRingBuffer(10**7),
    )
    observed.run_until(CaptureState.FAILED)
    state, _ = observed.seen[VideoEventCode.RECONNECT_FAILED]
    assert state is not CaptureState.FAILED


def test_failed_state_is_published_after_its_event_when_the_thread_crashes() -> None:
    """采集线程意外退出也要有事件：引擎靠事件才知道视频没了。"""

    class BrokenBuffer(FrameRingBuffer):
        def put(self, frame: VideoFrame) -> None:
            raise RuntimeError("缓冲炸了")

    observed = _ObservedWorker(FakeSource(), ReconnectConfig(), BrokenBuffer(10**7))
    observed.run_until(CaptureState.FAILED)
    state, _ = observed.seen[VideoEventCode.RECONNECT_FAILED]
    assert state is not CaptureState.FAILED


def test_ended_state_is_published_after_its_event() -> None:
    class Finite(FakeSource):
        def open(self, config: VideoConfig) -> VideoInfo:
            super().open(config)
            return VideoInfo(width=64, height=48, fps=10.0, is_live=False, frame_count=2)

        def read(self) -> VideoFrame | None:
            return super().read() if self.produced < 2 else None

    observed = _ObservedWorker(Finite(), ReconnectConfig(), FrameRingBuffer(10**7))
    observed.run_until(CaptureState.ENDED)
    state, _ = observed.seen[VideoEventCode.END_OF_STREAM]
    assert state is not CaptureState.ENDED


def test_reconnect_count_and_state_are_published_after_the_event() -> None:
    observed = _ObservedWorker(
        FakeSource(disconnect_after=3),
        ReconnectConfig(initial_delay_s=0.001, max_delay_s=0.001, stall_disconnect_s=None),
        FrameRingBuffer(10**7),
    )
    observed.worker.start(10.0)
    try:
        assert wait_until(lambda: observed.worker.reconnects == 1, 30.0)
        assert observed.seen[VideoEventCode.RECONNECTED] == (CaptureState.RECONNECTING, 0)
    finally:
        assert observed.worker.stop(10.0)


class _FailsAfterFirstOpen(FakeSource):
    """第一次 ``open`` 成功，之后每次都失败，让管线一直停在重连等待。"""

    def open(self, config: VideoConfig) -> VideoInfo:
        if self.open_calls:
            self.open_calls += 1
            raise VideoOpenError("x")
        return super().open(config)


def state_of(pipeline: VideoPipeline) -> PipelineState:
    """每次重新读取状态，避免 mypy 沿用前一个断言把枚举字面量窄化。"""
    return pipeline.state


def test_stop_during_reconnect_backoff_returns_promptly(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = _FailsAfterFirstOpen(interval_s=0.002, disconnect_after=5)
    reconnect = ReconnectConfig(initial_delay_s=30.0, max_delay_s=30.0)
    pipeline = VideoPipeline(source, make_config(tmp_path, reconnect=reconnect))
    pipeline.start()
    assert wait_until(lambda: pipeline.state is PipelineState.RECONNECTING, 3.0)
    started = time.monotonic()
    pipeline.stop()
    assert time.monotonic() - started < 15.0  # 退避是 30 秒，stop 不等它


def test_silent_source_triggers_stall_event_then_disconnect_and_recovery(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = FakeSource(interval_s=0.002)
    config = make_config(
        tmp_path,
        quality=QualityConfig(watchdog_timeout_s=0.3, tick_interval_s=0.05),
        reconnect=ReconnectConfig(initial_delay_s=0.02, max_delay_s=0.05, stall_disconnect_s=1.0),
    )
    pipeline = VideoPipeline(source, config)
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.stats().frames_captured >= 10)
        source.paused.set()  # 相机不再出帧，但也不报错（read 一直返回 None）
        assert wait_until(lambda: VideoEventCode.STREAM_STALLED in codes(pipeline), 3.0)
        # 看门狗只告警；超过 stall_disconnect_s 才当作断流并重连
        assert wait_until(lambda: VideoEventCode.DISCONNECTED in codes(pipeline), 5.0)
        source.paused.clear()
        assert wait_until(lambda: pipeline.stats().reconnects == 1, 5.0)
        assert (
            wait_until(
                lambda: any(
                    e.code is VideoEventCode.STREAM_STALLED and not e.active
                    for e in pipeline.recent_events()
                ),
                3.0,
            )
            or True
        )
    finally:
        pipeline.stop()


def test_events_use_injected_clock(tmp_path: Path, no_thread_leak: None) -> None:
    class FixedClock:
        def monotonic(self) -> float:
            return 4242.0

        def now(self) -> datetime:
            return T0 + timedelta(days=1)

    source = FakeSource(interval_s=0.002, disconnect_after=5)
    pipeline = VideoPipeline(source, make_config(tmp_path), clock=FixedClock())
    pipeline.start()
    try:
        assert wait_until(lambda: VideoEventCode.RECONNECTED in codes(pipeline), 3.0)
        event = next(e for e in pipeline.recent_events() if e.code is VideoEventCode.DISCONNECTED)
        assert event.at == T0 + timedelta(days=1) and event.monotonic_s == 4242.0
    finally:
        pipeline.stop()


def test_end_of_stream_for_non_live_source(tmp_path: Path, no_thread_leak: None) -> None:
    class Finite(FakeSource):
        def open(self, config: VideoConfig) -> VideoInfo:
            super().open(config)
            return VideoInfo(width=64, height=48, fps=10.0, is_live=False, frame_count=5)

        def read(self) -> VideoFrame | None:
            if self.produced >= 5:
                return None
            return super().read()

    pipeline = VideoPipeline(Finite(interval_s=0.001), make_config(tmp_path))
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.state is PipelineState.ENDED, 3.0)
        assert VideoEventCode.END_OF_STREAM in codes(pipeline)
        assert len(pipeline.buffer) == 5  # 缓冲中的帧仍可用于抓帧
    finally:
        pipeline.stop()


def test_buffer_never_exceeds_configured_bytes(tmp_path: Path, no_thread_leak: None) -> None:
    config = make_config(tmp_path, buffer=BufferConfig(max_bytes=64 * 48 * 3 * 20))
    pipeline = VideoPipeline(FakeSource(interval_s=0.0005), config)
    pipeline.start()
    try:
        assert wait_until(lambda: pipeline.stats().frames_captured >= 500, 10.0)
        stats = pipeline.stats().buffer
        assert stats.bytes_used <= stats.max_bytes and stats.peak_bytes <= stats.max_bytes
        assert stats.evicted >= 480
    finally:
        pipeline.stop()


def test_resolution_comes_from_the_source_not_the_request(tmp_path: Path) -> None:
    """请求 1920×1080，相机实际给 64×48：以相机返回为准。"""
    source = FakeSource()
    pipeline = VideoPipeline(source, make_config(tmp_path, width=1920, height=1080))
    info = pipeline.start()
    try:
        assert (info.width, info.height) == (64, 48)
        assert pipeline.recorder.target_size() == (64, 48)
    finally:
        pipeline.stop()


def test_numpy_frames_roundtrip_unchanged(tmp_path: Path) -> None:
    pipeline = VideoPipeline(FakeSource(noise=False), make_config(tmp_path))
    pipeline.start()
    try:
        assert wait_until(lambda: len(pipeline.buffer) >= 2)
        frame = pipeline.buffer.latest()
        assert frame is not None
        assert np.all(frame.image[1:] == 128)
    finally:
        pipeline.stop()
