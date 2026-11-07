"""分段录制：真写 MP4 后读回校验，哈希、临时文件、错误路径、路径穿越。"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
import pytest

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.config import RecorderConfig
from gelinspect.services.video.errors import (
    InvalidIdentifierError,
    NoActiveSegmentError,
    RecorderError,
)
from gelinspect.services.video.events import VideoEvent, VideoEventCode
from gelinspect.services.video.paths import make_ascii_scratch_dir
from gelinspect.services.video.recorder import SegmentRecorder
from tests.services.video.helpers import T0, stamped_image

RECORDER_MODULE = "gelinspect.services.video.recorder"


def frame(k: int, width: int = 320, height: int = 240) -> VideoFrame:
    rng = np.random.default_rng(k)
    base = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    return VideoFrame(
        image=stamped_image(base, k),
        sequence=k,
        captured_at=T0 + timedelta(seconds=k * 0.1),
        monotonic_s=k * 0.1,
    )


def make_recorder(
    tmp_path: Path,
    config: RecorderConfig | None = None,
    events: list[VideoEvent] | None = None,
    size: tuple[int, int] = (320, 240),
) -> SegmentRecorder:
    recorder = SegmentRecorder(
        tmp_path, config or RecorderConfig(), emit=events.append if events is not None else None
    )
    recorder.set_source_size(*size)
    return recorder


def read_back(path: Path) -> tuple[int, int, int, float]:
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened(), f"cv2 无法打开 {path}"
    count = 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        count += 1
    cap.release()
    return count, width, height, fps


def test_records_mp4_that_reads_back_with_matching_frames(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    final = recorder.start_segment("EXP-REC", 3, fps=10.0)
    assert final == (tmp_path / "videos" / "EXP-REC" / "row_03.mp4").resolve()
    assert recorder.active
    for k in range(25):
        recorder.offer(frame(k))
    record = recorder.stop_segment()

    assert record.path == final and record.path.exists()
    assert (record.experiment_id, record.row_index) == ("EXP-REC", 3)
    assert record.frame_count == 25 and record.dropped_frames == 0
    assert (record.width, record.height) == (320, 240)
    assert record.duration_s == pytest.approx(2.4)
    assert not record.interrupted
    count, width, height, fps = read_back(record.path)
    assert (count, width, height) == (25, 320, 240)
    assert fps == pytest.approx(10.0)
    assert hashlib.sha256(record.path.read_bytes()).hexdigest() == record.sha256
    assert record.size_bytes == record.path.stat().st_size > 0
    assert list(record.path.parent.iterdir()) == [record.path]  # .part 无残留
    assert not recorder.active


def test_downscales_to_long_side_with_even_dimensions(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path, RecorderConfig(long_side=200), size=(401, 301))
    assert recorder.target_size() == (200, 150)
    recorder.start_segment("EXP-S", 1, fps=5.0)
    for k in range(6):
        recorder.offer(frame(k, 401, 301))
    record = recorder.stop_segment()
    count, width, height, _ = read_back(record.path)
    assert (count, width, height) == (6, 200, 150)

    odd = make_recorder(tmp_path, RecorderConfig(long_side=151), size=(401, 301))
    assert odd.target_size()[0] % 2 == 0 and odd.target_size()[1] % 2 == 0


def test_does_not_upscale_small_sources(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path, size=(320, 240))
    assert recorder.target_size() == (320, 240)


def test_never_overwrites_existing_segment(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    paths = []
    for _ in range(3):
        paths.append(recorder.start_segment("EXP-R", 1, fps=10.0))
        for k in range(3):
            recorder.offer(frame(k))
        recorder.stop_segment()
    assert [p.name for p in paths] == ["row_01.mp4", "row_01_02.mp4", "row_01_03.mp4"]
    assert all(p.exists() for p in paths)


def test_writer_that_cannot_open_is_an_explicit_error(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    with mock.patch("cv2.VideoWriter") as writer_cls:
        writer_cls.fourcc = cv2.VideoWriter.fourcc
        writer_cls.return_value.isOpened.return_value = False
        with pytest.raises(RecorderError, match="无法创建视频写入器"):
            recorder.start_segment("EXP-E", 1, fps=10.0)
    assert not recorder.active
    assert not list((tmp_path / "videos" / "EXP-E").glob("*"))  # 没有空文件

    bad = make_recorder(tmp_path, RecorderConfig(fourcc="ZZZZ"))
    with pytest.raises(RecorderError):
        bad.start_segment("EXP-E", 2, fps=10.0)
    assert not list((tmp_path / "videos" / "EXP-E").glob("*"))


def test_segment_without_frames_is_an_error_not_an_empty_file(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    recorder.start_segment("EXP-N", 1, fps=10.0)
    with pytest.raises(RecorderError, match="没有任何帧"):
        recorder.stop_segment()
    assert list((tmp_path / "videos" / "EXP-N").iterdir()) == []
    with pytest.raises(NoActiveSegmentError):
        recorder.stop_segment()


def test_write_failure_is_reported_and_cleaned_up(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    recorder.start_segment("EXP-W", 1, fps=10.0)
    segment = recorder._segment  # noqa: SLF001
    assert segment is not None
    segment.writer = mock.Mock(wraps=segment.writer)
    segment.writer.write.side_effect = OSError("磁盘已满")
    for k in range(5):
        recorder.offer(frame(k))
    with pytest.raises(RecorderError, match="磁盘已满"):
        recorder.stop_segment()
    assert list((tmp_path / "videos" / "EXP-W").iterdir()) == []


def test_start_twice_and_stop_without_start(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    with pytest.raises(NoActiveSegmentError):
        recorder.stop_segment()
    recorder.start_segment("EXP-T", 1, fps=10.0)
    with pytest.raises(RecorderError, match="已有进行中"):
        recorder.start_segment("EXP-T", 2, fps=10.0)
    recorder.offer(frame(0))
    recorder.stop_segment()


def test_start_requires_source_size(tmp_path: Path) -> None:
    with pytest.raises(RecorderError, match="分辨率"):
        SegmentRecorder(tmp_path, RecorderConfig()).start_segment("EXP-Z", 1)


@pytest.mark.parametrize(
    "bad",
    ["../x", "..", "a/b", "a\\b", "/etc/passwd", "", " ", "a b", "x" * 65, "..%2f", "a\x00b", ".x"],
)
def test_path_traversal_ids_are_rejected(tmp_path: Path, bad: str) -> None:
    recorder = make_recorder(tmp_path)
    with pytest.raises(InvalidIdentifierError):
        recorder.start_segment(bad, 1)
    assert not recorder.active
    assert not (tmp_path / "videos").exists() or list((tmp_path / "videos").iterdir()) == []
    assert not (tmp_path.parent / "x").exists()


@pytest.mark.parametrize("row", [0, 13, -1, True])
def test_bad_row_index_is_rejected(tmp_path: Path, row: int) -> None:
    with pytest.raises(InvalidIdentifierError):
        make_recorder(tmp_path).start_segment("EXP-OK", row)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    videos = tmp_path / "data" / "videos"
    videos.mkdir(parents=True)
    (videos / "EXP-LINK").symlink_to(outside, target_is_directory=True)
    recorder = make_recorder(tmp_path / "data")
    with pytest.raises(InvalidIdentifierError, match="越出"):
        recorder.start_segment("EXP-LINK", 1)
    assert list(outside.iterdir()) == []


def test_slow_writer_drops_frames_without_blocking_offer(tmp_path: Path) -> None:
    events: list[VideoEvent] = []
    recorder = make_recorder(tmp_path, RecorderConfig(queue_max_frames=3), events)
    recorder.start_segment("EXP-D", 1, fps=10.0)
    segment = recorder._segment  # noqa: SLF001
    assert segment is not None
    real_write = segment.writer.write
    gate = threading.Event()

    def slow_write(image: np.ndarray) -> None:
        gate.wait(60)
        real_write(image)

    segment.writer = mock.Mock(wraps=segment.writer)
    segment.writer.write.side_effect = slow_write

    frames = [frame(k) for k in range(40)]  # 造帧的耗时不算进 offer
    started = time.monotonic()
    for item in frames:
        recorder.offer(item)
    elapsed = time.monotonic() - started
    assert elapsed < 30.0  # 写盘卡 60 秒，offer 要是在等它，这里就超了
    gate.set()
    record = recorder.stop_segment()
    assert record.dropped_frames > 0
    assert record.frame_count + record.dropped_frames == 40
    assert read_back(record.path)[0] == record.frame_count
    assert any(e.code is VideoEventCode.RECORDING_DROPPED for e in events)
    assert recorder.stats().total_dropped_frames == record.dropped_frames


def test_queue_byte_limit_also_drops(tmp_path: Path) -> None:
    one = 320 * 240 * 3
    recorder = make_recorder(
        tmp_path, RecorderConfig(queue_max_bytes=one * 2, queue_max_frames=100)
    )
    recorder.start_segment("EXP-B", 1, fps=10.0)
    segment = recorder._segment  # noqa: SLF001
    assert segment is not None
    gate = threading.Event()
    real_write = segment.writer.write
    segment.writer = mock.Mock(wraps=segment.writer)
    segment.writer.write.side_effect = lambda image: (gate.wait(5), real_write(image))
    for k in range(10):
        recorder.offer(frame(k))
    gate.set()
    record = recorder.stop_segment()
    assert record.dropped_frames >= 5 and record.frame_count + record.dropped_frames == 10


def test_source_gap_is_counted(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    recorder.start_segment("EXP-G", 1, fps=10.0)
    for k in (0, 1, 2, 6, 7):  # 3–5 号帧在驱动层丢失
        recorder.offer(frame(k))
    record = recorder.stop_segment()
    assert record.source_gap_frames == 3 and record.frame_count == 5


def test_abort_seals_segment_as_interrupted(tmp_path: Path) -> None:
    events: list[VideoEvent] = []
    recorder = make_recorder(tmp_path, events=events)
    recorder.start_segment("EXP-I", 4, fps=10.0)
    for k in range(8):
        recorder.offer(frame(k))
    recorder.abort_segment("disconnected")
    recorder.offer(frame(8))  # 中断后的帧被忽略
    assert not recorder.active
    record = recorder.stop_segment()  # 取回已封存的记录
    assert record.interrupted and record.interrupt_reason == "disconnected"
    assert record.frame_count == 8
    assert read_back(record.path)[0] == 8  # 文件可播放
    assert hashlib.sha256(record.path.read_bytes()).hexdigest() == record.sha256
    assert any(e.code is VideoEventCode.SEGMENT_INTERRUPTED for e in events)
    with pytest.raises(NoActiveSegmentError):
        recorder.stop_segment()
    # 可以接着录下一段
    recorder.start_segment("EXP-I", 5, fps=10.0)
    recorder.offer(frame(0))
    assert not recorder.stop_segment().interrupted


def test_close_finalizes_active_segment_and_joins_writer(tmp_path: Path) -> None:
    before = threading.active_count()
    recorder = make_recorder(tmp_path)
    recorder.start_segment("EXP-X", 1, fps=10.0)
    for k in range(4):
        recorder.offer(frame(k))
    assert recorder.close(5.0)
    assert threading.active_count() == before
    record = recorder.stop_segment()
    assert record.interrupted and record.frame_count == 4


def test_part_file_exists_while_recording_and_is_renamed_on_stop(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    final = recorder.start_segment("EXP-P", 1, fps=10.0)
    part = final.with_name("row_01.part.mp4")
    for k in range(4):
        recorder.offer(frame(k))
    assert part.exists() and not final.exists()  # 录制中只有 .part
    recorder.stop_segment()
    assert final.exists() and not part.exists()


def test_non_ascii_path_writes_through_ascii_scratch_then_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟 Windows：编码器写纯 ASCII 临时目录，收尾时移到中文目录，临时目录被清理。"""
    made: list[Path] = []
    real_make = make_ascii_scratch_dir

    def make() -> Path:
        made.append(real_make())
        return made[-1]

    monkeypatch.setattr(RECORDER_MODULE + ".needs_ascii_path", lambda p: True)
    monkeypatch.setattr(RECORDER_MODULE + ".make_ascii_scratch_dir", make)

    recorder = make_recorder(tmp_path / "数据 目录")
    final = recorder.start_segment("EXP-CN2", 2, fps=10.0)
    assert len(made) == 1 and str(made[0]).isascii()
    assert (made[0] / "segment.mp4").exists()  # 编码器写的是 ASCII 临时文件
    for k in range(6):
        recorder.offer(frame(k))
    record = recorder.stop_segment()

    assert record.path == final and read_back(final)[0] == 6
    assert hashlib.sha256(final.read_bytes()).hexdigest() == record.sha256
    assert list(final.parent.iterdir()) == [final]  # .part 与临时文件都无残留
    assert not made[0].exists()


def test_no_ascii_scratch_dir_is_an_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: "/临时")
    for name in ("PUBLIC", "PROGRAMDATA", "SYSTEMDRIVE"):
        monkeypatch.setenv(name, "/不存在的目录")
    with pytest.raises(RecorderError, match="纯英文路径"):
        make_ascii_scratch_dir()


def test_chinese_data_dir_for_video_and_images(tmp_path: Path) -> None:
    """数据目录含中文：直接写（macOS/Linux）；Windows 走 ASCII 临时目录，见上一个测试。"""
    data = tmp_path / "数据 目录"
    recorder = make_recorder(data)
    recorder.start_segment("EXP-CN", 1, fps=10.0)
    for k in range(5):
        recorder.offer(frame(k))
    record = recorder.stop_segment()
    assert "数据 目录" in str(record.path) and read_back(record.path)[0] == 5


def test_interrupted_event_is_published_before_the_segment_ends(tmp_path: Path) -> None:
    """``stop_segment`` 返回后，中断原因一定已经发出。"""
    done_at_event: list[bool] = []
    recorder = make_recorder(tmp_path)

    def on_event(event: VideoEvent) -> None:
        if event.code is VideoEventCode.SEGMENT_INTERRUPTED:
            assert segment is not None
            done_at_event.append(segment.done.is_set())

    recorder._emit = on_event  # noqa: SLF001
    recorder.start_segment("EXP-ORDER", 1, fps=10.0)
    segment = recorder._segment  # noqa: SLF001
    recorder.offer(frame(0))
    recorder.abort_segment("disconnected")
    assert recorder.stop_segment().interrupted
    assert done_at_event == [False]
