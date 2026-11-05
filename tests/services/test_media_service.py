"""媒体服务：单管裁剪与掩码轮廓、哈希校验、3 帧序列、录像回放。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.services.auth import AuthService, PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.media_service import ImageWarning, MediaService
from gelinspect.services.replay import ReplayError, VideoClip
from tests.services.conftest import Stack
from tests.services.engine_helpers import EXP_ID, Harness, make_harness, tubes_of_rows

HarnessFactory = Callable[..., Harness]


@pytest.fixture
def harness(tmp_path: Path, thread_baseline: None) -> Iterator[HarnessFactory]:
    built: list[Harness] = []

    def build(*args: object, **kwargs: object) -> Harness:
        item = make_harness(tmp_path, *args, **kwargs)  # type: ignore[arg-type]
        built.append(item)
        return item

    yield build
    for item in built:
        item.close()


class _Auth:
    """只放行有会话的调用，足够测试媒体读取本身。"""

    def authorize(self, session: object, permission: object) -> None:
        if session is None:
            raise PermissionDeniedError("请先登录")


def finished_run(harness: HarnessFactory, **kwargs: object) -> tuple[Harness, MediaService]:
    h = harness(tubes_of_rows(1), **kwargs)
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.finished()
    assert h.stored().state is S.COMPLETED
    return h, MediaService(_Auth(), h.database)  # type: ignore[arg-type]


SESSION = object()


def test_tube_images_crop_the_tube_and_carry_real_mask_outlines(harness: HarnessFactory) -> None:
    h, media = finished_run(harness)
    images = media.tube_images(SESSION, EXP_ID, 3)  # type: ignore[arg-type]
    assert images.tube_no == 3 and images.row_index == 1 and images.warnings == ()
    assert images.before is not None and images.after is not None
    frame = h.analyzer.requests[0].frames_before[-1].image
    for side in (images.before, images.after):
        height, width = side.image.shape[:2]
        assert side.image.dtype == np.uint8 and side.image.ndim == 3
        assert max(height, width) <= 520 and width < frame.shape[1] / 2  # 只裁出一支管
        assert side.tube and side.gel
        for polygon in (*side.tube, *side.gel):
            assert polygon.shape[1] == 2 and polygon.min() >= 0
            assert polygon[:, 0].max() < width and polygon[:, 1].max() < height
    # 凝胶轮廓在试管轮廓之内（外接框）
    tube_box = np.concatenate(images.before.tube)
    gel_box = np.concatenate(images.before.gel)
    assert gel_box.min(axis=0)[0] >= tube_box.min(axis=0)[0] - 2
    assert gel_box.max(axis=0)[0] <= tube_box.max(axis=0)[0] + 2
    assert len(images.sequence_before) == 3 and len(images.sequence_after) == 3
    assert images.before.source_name == "before_03.png"  # 代表帧是最后一张
    assert images.after.source_name == "after_03.png"


def test_row_thumbnails_and_listing(harness: HarnessFactory) -> None:
    h, media = finished_run(harness)
    assert media.rows_with_images(SESSION, EXP_ID) == [1]  # type: ignore[arg-type]
    thumbs = media.row_thumbnails(SESSION, EXP_ID, 1, long_side=300)  # type: ignore[arg-type]
    assert thumbs.before is not None and thumbs.after is not None and thumbs.warnings == ()
    assert max(thumbs.before.shape[:2]) <= 300
    assert media.row_thumbnails(SESSION, EXP_ID, 2).before is None  # type: ignore[arg-type]
    assert media.rows_with_images(SESSION, "NOPE") == []  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError):
        media.rows_with_images(None, EXP_ID)


def test_a_changed_file_is_reported_not_hidden(harness: HarnessFactory) -> None:
    h, media = finished_run(harness)
    mask = next(m for m in h.mask_files() if "t03_gel_after" in m.path)
    raw = next(
        m
        for m in h.database.media.list_for_experiment(EXP_ID)
        if Path(m.path).name == "before_01.png"
    )
    Path(mask.path).write_bytes(Path(mask.path).read_bytes() + b"0")  # 掩码被改动
    images = media.tube_images(SESSION, EXP_ID, 3)  # type: ignore[arg-type]
    assert ImageWarning.HASH_MISMATCH in images.warnings
    assert images.after is not None  # 仍然显示，让人看到问题
    Path(raw.path).unlink()
    assert ImageWarning.FILE_MISSING in media.tube_images(SESSION, EXP_ID, 3).warnings  # type: ignore[arg-type]


def test_a_tube_without_masks_is_cropped_by_position(harness: HarnessFactory) -> None:
    h = harness(tubes_of_rows(1))
    h.analyzer.drop_tubes = {4}  # 算法没返回 4 号管：转待复核，没有掩码
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.finished()
    media = MediaService(_Auth(), h.database)  # type: ignore[arg-type]
    images = media.tube_images(SESSION, EXP_ID, 4)  # type: ignore[arg-type]
    assert ImageWarning.NO_MASKS in images.warnings
    assert images.before is not None and images.before.tube == ()
    width = h.analyzer.requests[0].frames_before[-1].image.shape[1]
    assert images.before.image.shape[1] <= width / 8 + 2  # 整幅图的 1/8 列


def test_recordings_play_and_a_changed_recording_is_refused(harness: HarnessFactory) -> None:
    h, media = finished_run(harness)
    recordings = media.recordings(SESSION, EXP_ID)  # type: ignore[arg-type]
    assert set(recordings) == {1} and recordings[1].path.is_file()
    clip = media.open_clip(SESSION, EXP_ID, 1)  # type: ignore[arg-type]
    try:
        assert isinstance(clip, VideoClip) and clip.frame_count > 0 and clip.fps > 0
        first = clip.read_next()
        assert first is not None and first.dtype == np.uint8 and max(first.shape[:2]) <= 960
        clip.seek(clip.frame_count - 1)
        assert clip.read_next() is not None and clip.read_next() is None  # 末尾
        clip.seek(0)
        assert clip.position == 0 and clip.read_next() is not None
    finally:
        clip.close()
    clip.close()  # 可重复关闭
    with pytest.raises(ServiceError) as missing:
        media.open_clip(SESSION, EXP_ID, 5)  # type: ignore[arg-type]
    assert missing.value.code is ServiceErrorCode.MEDIA_MISSING
    recordings[1].path.write_bytes(recordings[1].path.read_bytes() + b"\0")
    with pytest.raises(ServiceError) as tampered:
        media.open_clip(SESSION, EXP_ID, 1)  # type: ignore[arg-type]
    assert tampered.value.code is ServiceErrorCode.MEDIA_TAMPERED
    with pytest.raises(ReplayError):
        VideoClip(Path("/nonexistent/clip.mp4"))


def test_unknown_session_is_refused_by_the_real_auth(stack: Stack) -> None:
    media = MediaService(stack.auth, stack.database)
    assert isinstance(stack.auth, AuthService)
    with pytest.raises(PermissionDeniedError):
        media.recordings(None, "E1")
    assert media.recordings(stack.login("analyst"), "E1") == {}
