"""抓帧：用带已知时间戳和编号的假视频源验证抓到的帧确实在翻转前后，settle 生效，不足 n 帧抛异常。"""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from gelinspect.interfaces.analysis import CapturedFrame
from gelinspect.services.video.config import SnapshotConfig
from gelinspect.services.video.errors import (
    InsufficientFramesError,
    InvalidIdentifierError,
    SnapshotError,
)
from gelinspect.services.video.pipeline import VideoPipeline
from tests.services.video.helpers import T0, FakeSource, frame_number, make_config, wait_until

# 合成时间：第 k 帧在 T0 + 0.1k 秒。翻转 [5.0, 7.0] 秒，稳定 1.0 秒 → 翻转后帧从 8.0 秒（第 80 帧）起。
FLIP_START = T0 + timedelta(seconds=5.0)
FLIP_END = T0 + timedelta(seconds=7.0)


@pytest.fixture
def running(tmp_path: Path, no_thread_leak: None) -> VideoPipeline:  # type: ignore[misc]
    source = FakeSource(interval_s=0.001)
    pipeline = VideoPipeline(
        source, make_config(tmp_path, snapshot=SnapshotConfig(wait_timeout_s=3.0, settle_s=1.0))
    )
    pipeline.start()
    yield pipeline  # type: ignore[misc]
    pipeline.stop()


def wait_for_sequence(pipeline: VideoPipeline, sequence: int) -> None:
    def reached() -> bool:
        latest = pipeline.buffer.latest()
        return latest is not None and latest.sequence >= sequence

    assert wait_until(reached, 10.0)


def numbers(frames: list[CapturedFrame]) -> list[int]:
    return [frame_number(f.image) for f in frames]


def test_frames_are_before_flip_and_after_settle(running: VideoPipeline) -> None:
    wait_for_sequence(running, 49)
    running.mark_flip_start(FLIP_START, 1)
    result = running.grab(1, 3, experiment_id="EXP-1", flip_completed_at=FLIP_END)

    # 翻转前：时间严格早于翻转开始（5.0 秒 = 第 50 帧）的最近 3 帧 → 47、48、49
    assert numbers(result.frames_before) == [47, 48, 49]
    assert all(f.captured_at < FLIP_START for f in result.frames_before)
    # 翻转后：翻转结束 7.0 秒 + 稳定 1.0 秒 = 8.0 秒（第 80 帧）起的最早 3 帧；
    # 翻转刚结束时（70–79 帧）的画面还在流动，不应被选中
    assert numbers(result.frames_after) == [80, 81, 82]
    assert all(f.captured_at >= FLIP_END + timedelta(seconds=1.0) for f in result.frames_after)
    assert result.settle_s == 1.0


def test_settle_parameter_shifts_after_frames(running: VideoPipeline) -> None:
    wait_for_sequence(running, 49)
    running.mark_flip_start(FLIP_START, 1)
    result = running.grab(1, 3, 2.5, experiment_id="EXP-1", flip_completed_at=FLIP_END)
    assert numbers(result.frames_after) == [95, 96, 97]  # 7.0 + 2.5 = 9.5 秒
    result0 = running.grab(
        2,
        2,
        0.0,
        experiment_id="EXP-1",
        flip_completed_at=FLIP_END,
        flip_started_at=FLIP_START,
    )
    assert numbers(result0.frames_after) == [70, 71]


def test_waits_for_frames_that_do_not_exist_yet(tmp_path: Path, no_thread_leak: None) -> None:
    """翻转后的帧要等：调用 grab 时视频才播到第 20 帧，却要 8 秒之后的帧。"""
    source = FakeSource(interval_s=0.02)
    pipeline = VideoPipeline(source, make_config(tmp_path))
    pipeline.start()
    try:
        wait_for_sequence(pipeline, 5)
        start = pipeline.buffer.latest()
        assert start is not None
        flip_start = start.captured_at
        pipeline.mark_flip_start(flip_start, 1)
        result = pipeline.grab(
            1,
            3,
            0.1,
            experiment_id="EXP-W",
            flip_completed_at=flip_start + timedelta(seconds=0.5),
            timeout_s=5.0,
        )
        assert result.frames_after[0].captured_at >= flip_start + timedelta(seconds=0.6)
        assert len(result.frames_after) == 3
    finally:
        pipeline.stop()


def test_insufficient_after_frames_raises_after_timeout(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = FakeSource(interval_s=0.001, max_frames=82)  # 只产出 0–81 号帧
    pipeline = VideoPipeline(source, make_config(tmp_path))
    pipeline.start()
    try:
        wait_for_sequence(pipeline, 81)
        pipeline.mark_flip_start(FLIP_START, 1)
        with pytest.raises(InsufficientFramesError) as info:
            pipeline.grab(
                1, 3, experiment_id="EXP-X", flip_completed_at=FLIP_END, timeout_s=0.3
            )  # 8.0 秒之后只有 80、81 两帧
        assert (info.value.phase, info.value.required, info.value.available) == ("after", 3, 2)
        assert "翻转后" in str(info.value) and "超时" in str(info.value)
        # 不会留下半成品文件
        assert not (tmp_path / "images" / "EXP-X").exists() or not any(
            (tmp_path / "images" / "EXP-X").rglob("*.png")
        )
    finally:
        pipeline.stop()


def test_insufficient_before_frames_raises_immediately(
    tmp_path: Path, no_thread_leak: None
) -> None:
    source = FakeSource(interval_s=0.001, max_frames=3)
    pipeline = VideoPipeline(source, make_config(tmp_path))
    pipeline.start()
    try:
        wait_for_sequence(pipeline, 2)
        with pytest.raises(InsufficientFramesError) as info:
            pipeline.grab(
                1,
                5,
                experiment_id="EXP-B",
                flip_completed_at=T0 + timedelta(seconds=1),
                flip_started_at=T0 + timedelta(seconds=1),
            )
        assert info.value.phase == "before" and info.value.available == 3
    finally:
        pipeline.stop()


def test_missing_flip_start_is_an_error(running: VideoPipeline) -> None:
    wait_for_sequence(running, 10)
    with pytest.raises(SnapshotError, match="翻转开始"):
        running.grab(1, 3, experiment_id="EXP-1", flip_completed_at=FLIP_END)


def test_mark_pins_pre_roll_frames_against_eviction(tmp_path: Path, no_thread_leak: None) -> None:
    """翻转期间产生大量帧，常规淘汰会冲掉翻转前的帧；pin 保证它们还在。"""
    image_bytes = 64 * 48 * 3
    config = make_config(
        tmp_path, buffer={"max_bytes": image_bytes * 10, "pin_hard_limit_bytes": image_bytes * 40}
    )
    source = FakeSource(interval_s=0.001)
    pipeline = VideoPipeline(source, config)
    pipeline.start()
    try:
        wait_for_sequence(pipeline, 49)
        pipeline.mark_flip_start(FLIP_START, 1)  # 保护 47、48、49
        wait_for_sequence(pipeline, 200)  # 缓冲只能放 10 帧，常规淘汰早已把 47–49 挤出去
        result = pipeline.grab(
            1, 3, 0.0, experiment_id="EXP-P", flip_completed_at=T0 + timedelta(seconds=19.5)
        )
        assert numbers(result.frames_before) == [47, 48, 49]
        assert pipeline.buffer.stats().pins == 0  # 抓完后保护已解除
    finally:
        pipeline.stop()


def test_without_mark_pre_roll_frames_are_lost(tmp_path: Path, no_thread_leak: None) -> None:
    """对照组：不调用 mark_flip_start，同样的条件下翻转前的帧已被淘汰，必须明确报错。"""
    image_bytes = 64 * 48 * 3
    config = make_config(tmp_path, buffer={"max_bytes": image_bytes * 10})
    pipeline = VideoPipeline(FakeSource(interval_s=0.001), config)
    pipeline.start()
    try:
        wait_for_sequence(pipeline, 200)
        with pytest.raises(InsufficientFramesError) as info:
            pipeline.grab(
                1,
                3,
                0.0,
                experiment_id="EXP-P",
                flip_completed_at=T0 + timedelta(seconds=19.5),
                flip_started_at=FLIP_START,
            )
        assert info.value.phase == "before"
    finally:
        pipeline.stop()


def test_png_files_are_lossless_hashed_and_assembled(
    running: VideoPipeline, tmp_path: Path
) -> None:
    wait_for_sequence(running, 49)
    running.mark_flip_start(FLIP_START, 2)
    result = running.grab(2, 3, experiment_id="EXP-IMG", flip_completed_at=FLIP_END)

    assert result.directory == (tmp_path / "images" / "EXP-IMG" / "row_02").resolve()
    assert len(result.files) == 6
    assert [f.phase for f in result.files] == ["before"] * 3 + ["after"] * 3
    for saved, frame in zip(result.files, result.frames_before + result.frames_after, strict=True):
        assert saved.path.suffix == ".png"
        assert frame.source_path == saved.path
        data = saved.path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == saved.sha256
        assert saved.size_bytes == len(data)
        decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert np.array_equal(decoded, frame.image)  # 无损：逐像素一致
        assert frame_number(decoded) == saved.sequence
    assert not list(result.directory.glob("*.part"))
    # 帧本身只读，可以直接交给算法而不复制
    assert all(not f.image.flags.writeable for f in result.frames_before + result.frames_after)


def test_jpg_format_and_no_overwrite(running: VideoPipeline) -> None:
    wait_for_sequence(running, 49)
    running.mark_flip_start(FLIP_START, 1)
    first = running.grab(
        1, 3, experiment_id="EXP-J", flip_completed_at=FLIP_END, image_format="jpg"
    )
    assert all(f.path.suffix == ".jpg" for f in first.files)
    running.mark_flip_start(FLIP_START, 1)
    second = running.grab(1, 3, experiment_id="EXP-J", flip_completed_at=FLIP_END)
    assert second.directory != first.directory  # 重拍不覆盖
    assert second.directory.name == "row_01_02"
    assert all(f.path.exists() for f in first.files + second.files)


def test_rejects_bad_identifiers(running: VideoPipeline, tmp_path: Path) -> None:
    wait_for_sequence(running, 10)
    for bad in ("../evil", "a/b", "..", "", "a\\b", "/abs", ".hidden", "x" * 65):
        with pytest.raises(InvalidIdentifierError):
            running.grab(
                1, 3, experiment_id=bad, flip_completed_at=FLIP_END, flip_started_at=FLIP_START
            )
    with pytest.raises(InvalidIdentifierError):
        running.grab(
            11, 3, experiment_id="ok", flip_completed_at=FLIP_END, flip_started_at=FLIP_START
        )
    assert not (tmp_path.parent / "evil").exists()


def test_grab_async_returns_future_and_does_not_block_caller(running: VideoPipeline) -> None:
    wait_for_sequence(running, 49)
    running.mark_flip_start(FLIP_START, 3)
    future = running.grab_async(3, 3, experiment_id="EXP-A", flip_completed_at=FLIP_END)
    result = future.result(10)
    assert numbers(result.frames_after) == [80, 81, 82]
    # 提交后可立即对下一排打标记，互不干扰
    running.mark_flip_start(FLIP_START, 4)
    running.mark_flip_start(FLIP_START, 5)
    running.snapshots.cancel_mark(4)
    running.snapshots.cancel_mark(5)
    assert running.buffer.stats().pins == 0


def test_stop_cancels_waiting_grab(tmp_path: Path, no_thread_leak: None) -> None:
    pipeline = VideoPipeline(FakeSource(interval_s=0.001, max_frames=20), make_config(tmp_path))
    pipeline.start()
    wait_for_sequence(pipeline, 19)
    pipeline.mark_flip_start(T0 + timedelta(seconds=1.0), 1)
    future = pipeline.grab_async(
        1,
        3,
        experiment_id="EXP-C",
        flip_completed_at=T0 + timedelta(seconds=50),
        timeout_s=30.0,
    )
    threading.Timer(0.2, pipeline.stop).start()
    with pytest.raises(InsufficientFramesError, match="管线已停止"):
        future.result(5)
