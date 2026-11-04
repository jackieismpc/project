"""SimVideoSource：帧流、翻转状态、过渡帧和断流注入。"""

import numpy as np
import pytest
from numpy.typing import NDArray

from gelinspect.interfaces.device import TrayPosition
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoDisconnectedError,
    VideoNotOpenError,
    VideoSource,
)
from simulation.clock import ManualClock
from simulation.scenario import Scenario
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource
from tests.simulation.conftest import S

SMALL_CONFIG = VideoConfig(device="sim", width=480, height=270, fps=10.0)


def source(clock: ManualClock, **kwargs: object) -> SimVideoSource:
    scenario = Scenario(tube_states={n: S.FULLY_GELLED for n in range(1, 9)})
    return SimVideoSource(clock=clock, scenario=scenario, **kwargs)  # type: ignore[arg-type]


def mean_dark_rows(image: NDArray[np.uint8]) -> float:
    """图像中暗像素（凝胶）的平均行号，没有时返回 -1。"""
    ys = np.nonzero(image.mean(axis=2) < 150)[0]
    return float(ys.mean()) if ys.size else -1.0


def test_protocol_and_open_info() -> None:
    video = SimVideoSource()
    assert isinstance(video, VideoSource)
    info = video.open(VideoConfig(device="sim"))
    assert (info.width, info.height, info.fps, info.is_live) == (1920, 1080, 15.0, True)
    video.close()


def test_read_requires_open() -> None:
    video = SimVideoSource()
    with pytest.raises(VideoNotOpenError):
        video.read()
    video.open(SMALL_CONFIG)
    video.close()
    video.close()
    with pytest.raises(VideoNotOpenError):
        video.read()


def test_frames_follow_configured_resolution_and_rate() -> None:
    clock = ManualClock()
    video = source(clock)
    info = video.open(SMALL_CONFIG)
    frames = [video.read() for _ in range(11)]
    assert all(f is not None for f in frames)
    first, last = frames[0], frames[-1]
    assert first is not None and last is not None
    assert first.image.shape == (info.height, info.width, 3) and first.image.dtype == np.uint8
    assert [f.sequence for f in frames if f] == list(range(11))
    assert last.monotonic_s - first.monotonic_s == pytest.approx(1.0)
    assert (last.captured_at - first.captured_at).total_seconds() == pytest.approx(1.0)
    assert not np.array_equal(first.image, last.image)  # 噪声逐帧不同


def test_frames_are_reproducible_with_seed() -> None:
    def grab() -> NDArray[np.uint8]:
        video = source(ManualClock(), seed=4)
        video.open(SMALL_CONFIG)
        frame = video.read()
        assert frame is not None
        return frame.image

    assert np.array_equal(grab(), grab())


def test_static_scene_can_be_set_by_interface() -> None:
    clock = ManualClock()
    video = source(clock)
    video.open(SMALL_CONFIG)
    video.static_scene.set(1, flipped=False)
    upright = video.read()
    video.static_scene.set(1, flipped=True)
    inverted = video.read()
    assert upright is not None and inverted is not None
    half = upright.image.shape[0] / 2
    assert mean_dark_rows(upright.image) > half > mean_dark_rows(inverted.image) > 0


def test_follows_sim_device_through_a_flip() -> None:
    clock = ManualClock()
    device = SimDevice(clock=clock, time_scale=1.0, flip_duration_s=1.0, settle_duration_s=1.0)
    device.connect()
    device.move_tray(TrayPosition.IN)
    video = source(clock, scene=device)
    video.open(SMALL_CONFIG)
    first = video.read()
    assert first is not None
    device.begin_flip(1)
    clock.advance(0.5)
    middle = video.read()
    assert middle is not None
    assert not np.array_equal(first.image, middle.image)
    clock.advance(2.0)
    final = video.read()
    assert final is not None
    half = final.image.shape[0] / 2
    assert mean_dark_rows(first.image) > half > mean_dark_rows(final.image)
    dark_first = (first.image.mean(axis=2) < 150).sum()
    dark_middle = (middle.image.mean(axis=2) < 150).sum()
    assert dark_middle != dark_first  # 翻转过程中的过渡帧


def test_defaults_to_latest_device() -> None:
    clock = ManualClock()
    device = SimDevice(clock=clock)
    device.connect()
    device.move_tray(TrayPosition.IN)
    device.flip_row(1)
    clock.advance(100)
    video = SimVideoSource(clock=clock, scenario=Scenario(tube_states={1: S.FULLY_GELLED}))
    video.open(SMALL_CONFIG)
    frame = video.read()
    assert frame is not None
    assert mean_dark_rows(frame.image) < frame.image.shape[0] / 2


def test_disconnect_and_timeout_injection() -> None:
    clock = ManualClock()
    video = source(clock)
    video.open(SMALL_CONFIG)
    video.inject_timeouts(2)
    assert video.read() is None
    assert video.read() is None
    assert video.read() is not None
    video.inject_disconnect()
    with pytest.raises(VideoDisconnectedError):
        video.read()
    with pytest.raises(VideoDisconnectedError):
        video.read()
    video.close()
    video.open(SMALL_CONFIG)
    assert video.read() is not None


def test_read_returns_none_when_next_frame_is_not_due_within_timeout() -> None:
    clock = ManualClock()
    video = source(clock)
    video.open(VideoConfig(device="sim", width=480, height=270, fps=0.5, read_timeout_ms=100))
    assert video.read() is not None
    assert video.read() is None  # 下一帧在 2 秒后，超时只等 100 ms
    assert clock.monotonic() == pytest.approx(0.1)
