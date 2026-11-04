"""FileVideoSource、ImageSequenceSource、load_row_capture、factory、中文路径。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from gelinspect.infrastructure.video.factory import (
    VideoSourceConfigError,
    create_video_source,
)
from gelinspect.infrastructure.video.file_source import FileVideoSource
from gelinspect.infrastructure.video.image_io import (
    ImageLoadError,
    list_images,
    read_image,
    write_image,
)
from gelinspect.infrastructure.video.image_sequence import ImageSequenceSource, load_row_capture
from gelinspect.interfaces.analysis import RowAnalysisRequest, TubeSlot
from gelinspect.interfaces.video import (
    VideoConfig,
    VideoNotOpenError,
    VideoOpenError,
    VideoSource,
)

NO_SLEEP = {"sleep": lambda s: None}


def numbered(k: int, width: int = 64, height: int = 48) -> np.ndarray:  # type: ignore[type-arg]
    image = np.full((height, width, 3), 50, np.uint8)
    image[:, :, 0] = k * 10
    return image


def make_mp4(
    path: Path, count: int = 12, fps: float = 20.0, size: tuple[int, int] = (64, 48)
) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, size)
    assert writer.isOpened()
    for k in range(count):
        writer.write(numbered(k, *size))
    writer.release()
    return path


# ------------------------------------------------------------------ FileVideoSource


def test_file_source_plays_all_frames_then_eof(tmp_path: Path) -> None:
    path = make_mp4(tmp_path / "a.mp4", 12, 20.0)
    source = FileVideoSource(realtime=False)
    assert isinstance(source, VideoSource)
    info = source.open(VideoConfig(device=str(path)))
    assert (info.width, info.height, info.is_live, info.frame_count) == (64, 48, False, 12)
    assert info.fps == pytest.approx(20.0)
    frames = []
    while (frame := source.read()) is not None:
        frames.append(frame)
    assert [f.sequence for f in frames] == list(range(12))
    assert all(f.image.shape == (48, 64, 3) for f in frames)
    assert source.read() is None  # 读完后保持 None（文件结束）
    source.close()
    source.close()
    with pytest.raises(VideoNotOpenError):
        source.read()


def test_file_source_loop_and_pacing(tmp_path: Path) -> None:
    path = make_mp4(tmp_path / "a.mp4", 5, 20.0)
    waits: list[float] = []
    source = FileVideoSource(loop=True, sleep=waits.append)
    source.open(VideoConfig(device=str(path)))
    frames = [source.read() for _ in range(12)]
    assert all(f is not None for f in frames)
    assert [f.sequence for f in frames if f] == list(range(12))  # 循环时序号继续递增
    # 注入的 sleep 不真实等待，所以相邻两次的「应等待时间」差一个帧间隔（1/20 秒）
    steps = [b - a for a, b in zip(waits, waits[1:], strict=False)]
    assert steps and all(s == pytest.approx(0.05, abs=0.01) for s in steps)
    source.close()


def test_file_source_errors(tmp_path: Path) -> None:
    with pytest.raises(VideoOpenError, match="不存在"):
        FileVideoSource().open(VideoConfig(device=str(tmp_path / "nope.mp4")))
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    with pytest.raises(VideoOpenError):
        FileVideoSource().open(VideoConfig(device=str(broken)))
    with pytest.raises(ValueError):
        FileVideoSource(speed=0)


def test_file_source_accepts_chinese_path(tmp_path: Path) -> None:
    folder = tmp_path / "视频 样片"
    folder.mkdir()
    path = make_mp4(folder / "翻转.mp4", 4)
    source = FileVideoSource(realtime=False)
    source.open(VideoConfig(device=str(path)))
    assert source.read() is not None
    source.close()


# ------------------------------------------------------------------ 中文路径读写


def test_image_io_roundtrip_with_chinese_path(tmp_path: Path) -> None:
    folder = tmp_path / "试验 一排"
    folder.mkdir()
    for name in ("翻转前_1.png", "翻转前_2.jpg"):
        image = numbered(3)
        write_image(folder / name, image)
        loaded = read_image(folder / name)
        assert loaded.shape == image.shape and loaded.dtype == np.uint8
    assert np.array_equal(read_image(folder / "翻转前_1.png"), numbered(3))  # PNG 无损


def test_image_io_errors_and_natural_sort(tmp_path: Path) -> None:
    with pytest.raises(ImageLoadError, match="无法读取"):
        read_image(tmp_path / "missing.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG garbage")
    with pytest.raises(ImageLoadError, match="损坏"):
        read_image(bad)
    empty = tmp_path / "empty.png"
    empty.touch()
    with pytest.raises(ImageLoadError):
        read_image(empty)
    folder = tmp_path / "sorted"
    folder.mkdir()
    for n in (10, 2, 1):
        write_image(folder / f"IMG_{n}.png", numbered(1))
    (folder / "note.txt").write_text("x")
    assert [p.name for p in list_images(folder)] == ["IMG_1.png", "IMG_2.png", "IMG_10.png"]
    with pytest.raises(ImageLoadError, match="不存在"):
        list_images(tmp_path / "nope")


# ------------------------------------------------------------------ ImageSequenceSource


def make_folder(path: Path, count: int, **kwargs: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for k in range(count):
        write_image(path / f"p{k + 1}.png", numbered(k, **kwargs))
    return path


def test_sequence_plays_in_natural_order_and_ends(tmp_path: Path) -> None:
    folder = make_folder(tmp_path / "照片", 12)  # p1…p12：自然排序下 p2 在 p10 之前
    source = ImageSequenceSource(**NO_SLEEP)  # type: ignore[arg-type]
    assert isinstance(source, VideoSource)
    info = source.open(VideoConfig(device=str(folder), fps=5.0))
    assert (info.width, info.height, info.fps, info.is_live, info.frame_count) == (
        64,
        48,
        5.0,
        False,
        12,
    )
    frames = []
    while (frame := source.read()) is not None:
        frames.append(frame)
    assert [int(f.image[0, 0, 0]) for f in frames] == [k * 10 for k in range(12)]
    assert [f.sequence for f in frames] == list(range(12))
    source.close()
    with pytest.raises(VideoNotOpenError):
        source.read()


def test_sequence_loop(tmp_path: Path) -> None:
    folder = make_folder(tmp_path / "s", 3)
    source = ImageSequenceSource(loop=True, realtime=False)
    source.open(VideoConfig(device=str(folder)))
    got = [source.read() for _ in range(7)]
    assert [int(f.image[0, 0, 0]) for f in got if f] == [0, 10, 20, 0, 10, 20, 0]
    assert [f.sequence for f in got if f] == list(range(7))


def test_sequence_paces_by_fps(tmp_path: Path) -> None:
    folder = make_folder(tmp_path / "s", 4)
    waits: list[float] = []
    source = ImageSequenceSource(sleep=waits.append)
    source.open(VideoConfig(device=str(folder), fps=10.0))
    while source.read() is not None:
        pass
    steps = [b - a for a, b in zip(waits, waits[1:], strict=False)]
    assert steps and all(s == pytest.approx(0.1, abs=0.01) for s in steps)


def test_sequence_open_errors(tmp_path: Path) -> None:
    with pytest.raises(VideoOpenError, match="不存在"):
        ImageSequenceSource().open(VideoConfig(device=str(tmp_path / "nope")))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(VideoOpenError, match="没有"):
        ImageSequenceSource().open(VideoConfig(device=str(empty)))

    mixed = make_folder(tmp_path / "mixed", 3)
    write_image(mixed / "p4.png", numbered(0, width=80, height=48))
    with pytest.raises(VideoOpenError, match=r"p4\.png.*80×48.*64×48"):
        ImageSequenceSource().open(VideoConfig(device=str(mixed)))

    corrupt = make_folder(tmp_path / "corrupt", 3)
    (corrupt / "p2.png").write_bytes(b"garbage")
    with pytest.raises(VideoOpenError, match=r"p2\.png"):
        ImageSequenceSource().open(VideoConfig(device=str(corrupt)))


def test_sequence_file_removed_after_open_is_disconnect(tmp_path: Path) -> None:
    from gelinspect.interfaces.video import VideoDisconnectedError

    folder = make_folder(tmp_path / "s", 3)
    source = ImageSequenceSource(realtime=False)
    source.open(VideoConfig(device=str(folder)))
    (folder / "p2.png").unlink()
    assert source.read() is not None
    with pytest.raises(VideoDisconnectedError):
        source.read()


# ------------------------------------------------------------------ load_row_capture


def make_row_dir(root: Path, before: int = 3, after: int = 3) -> Path:
    make_folder(root / "before", before)
    make_folder(root / "after", after)
    return root


def test_load_row_capture_builds_valid_request(tmp_path: Path) -> None:
    row = make_row_dir(tmp_path / "样片" / "第一排", before=4, after=3)
    capture = load_row_capture(row)
    assert len(capture.frames_before) == 4 and len(capture.frames_after) == 3
    assert [int(f.image[0, 0, 0]) for f in capture.frames_before] == [0, 10, 20, 30]
    assert capture.frames_before[0].source_path == row / "before" / "p1.png"
    assert all(not f.image.flags.writeable for f in capture.frames_before)
    times = [f.captured_at for f in capture.frames_before + capture.frames_after]
    assert times == sorted(times) and all(t.tzinfo for t in times)
    request = RowAnalysisRequest(
        experiment_id="EXP-IMG",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(1)],
        frames_before=capture.frames_before,
        frames_after=capture.frames_after,
    )
    assert request.image_size == (48, 64)


def test_load_row_capture_requires_three_frames_per_side(tmp_path: Path) -> None:
    with pytest.raises(ImageLoadError, match=r"至少需要 3 张.*实际只有 2 张"):
        load_row_capture(make_row_dir(tmp_path / "a", before=2, after=3))
    with pytest.raises(ImageLoadError, match="after"):
        load_row_capture(make_row_dir(tmp_path / "b", before=3, after=1))
    with pytest.raises(ImageLoadError, match="不存在"):
        load_row_capture(tmp_path / "missing")
    only_before = tmp_path / "c"
    make_folder(only_before / "before", 3)
    with pytest.raises(ImageLoadError, match="不存在"):
        load_row_capture(only_before)


def test_load_row_capture_rejects_mixed_resolution_and_corrupt(tmp_path: Path) -> None:
    row = make_row_dir(tmp_path / "m")
    write_image(row / "after" / "p9.png", numbered(0, width=70, height=48))
    with pytest.raises(ImageLoadError, match="分辨率不一致"):
        load_row_capture(row)
    row2 = make_row_dir(tmp_path / "n")
    (row2 / "before" / "p2.png").write_bytes(b"junk")
    with pytest.raises(ImageLoadError, match="损坏"):
        load_row_capture(row2)


# ------------------------------------------------------------------ factory


def test_factory_aliases_and_dotted_paths() -> None:
    assert isinstance(create_video_source("file"), FileVideoSource)
    assert isinstance(create_video_source("images", {"loop": True}), ImageSequenceSource)
    dotted = "gelinspect.infrastructure.video.file_source.FileVideoSource"
    assert isinstance(create_video_source(dotted), FileVideoSource)
    colon = "gelinspect.infrastructure.video.file_source:FileVideoSource"
    assert isinstance(create_video_source(colon, {"realtime": False}), FileVideoSource)
    sim = create_video_source("simulation.sim_video:SimVideoSource")  # 通过配置字符串使用模拟源
    assert isinstance(sim, VideoSource)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("opencv", "格式"),  # 没有相机选项
        ("nomodule.Klass", "无法导入"),
        ("gelinspect.infrastructure.video.file_source:Nope", "没有名为"),
        ("pathlib:Path", "没有实现 VideoSource"),
        ("", "格式"),
    ],
)
def test_factory_errors(spec: str, message: str) -> None:
    with pytest.raises(VideoSourceConfigError, match=message):
        create_video_source(spec)


def test_factory_bad_options() -> None:
    with pytest.raises(VideoSourceConfigError, match="创建视频源"):
        create_video_source("file", {"bogus": 1})
