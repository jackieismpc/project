"""掩码存档：引擎把算法掩码存成 PNG，哈希入库，与结果同一个工作单元。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.interfaces.analysis import (
    AnalyzerInfo,
    CapturedFrame,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysis,
    TubeMasks,
    TubeSlot,
)
from gelinspect.interfaces.storage import MediaKind
from gelinspect.services import mask_archive
from gelinspect.services.mask_archive import (
    MASK_PREFIX,
    MaskArchiveError,
    archive_masks,
    latest_masks,
    mask_file_name,
    parse_mask_name,
    read_mask,
)
from gelinspect.services.video.paths import sha256_file
from tests.services.engine_helpers import (
    EXP_ID,
    OPERATOR,
    FlakyStorage,
    Harness,
    make_harness,
    tubes_of_rows,
)

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


def test_file_names_round_trip() -> None:
    name = mask_file_name(7, "gel", "before")
    assert name == "mask_t07_gel_before.png" and name.startswith(MASK_PREFIX)
    assert parse_mask_name(name) == (7, "gel", "before")
    assert parse_mask_name("mask_t80_tube_after_03.png") == (80, "tube", "after")
    assert parse_mask_name("before_01.png") is None


def test_engine_archives_every_mask_with_the_result(harness: HarnessFactory) -> None:
    h = harness(tubes_of_rows(1, 2))
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.finished()
    stored = h.stored()
    assert stored.state is S.COMPLETED

    files = h.mask_files()
    assert len(files) == 16 * 4  # 每支管：翻转前后的 tube 和 gel
    h.assert_media_intact()  # 文件存在，哈希一致
    latest = latest_masks(files)
    assert len(latest) == len(files)
    raw_dir = {Path(m.path).parent for m in files}
    raw_images = [
        m
        for m in h.database.media.list_for_experiment(EXP_ID, kind=MediaKind.IMAGE)
        if not Path(m.path).name.startswith(MASK_PREFIX)
    ]
    # 掩码和原图在同一排目录，行号一致，且与原图同尺寸
    assert raw_dir <= {Path(m.path).parent for m in raw_images}
    gel = read_mask(Path(latest[(3, "gel", "after")].path))
    tube = read_mask(Path(latest[(3, "tube", "after")].path))
    frame = h.analyzer.requests[0].frames_after[-1].image
    assert gel.shape == tube.shape == frame.shape[:2]
    assert set(np.unique(gel)) <= {0, 255} and gel.any() and tube.any()
    assert {m.row_index for m in files} == {1, 2}
    # 掩码的哈希就是文件内容的哈希
    for item in files[:5]:
        assert sha256_file(Path(item.path)) == item.sha256
    h.assert_clean()


def test_masks_are_stored_in_the_same_transaction_as_the_results(
    harness: HarnessFactory,
) -> None:
    """结果事务提交失败：掩码记录也不入库，重试后才与结果一起入库。"""
    h = harness(
        tubes_of_rows(1),
        storage_wrapper=FlakyStorage,
    )
    flaky = h.storage
    assert isinstance(flaky, FlakyStorage)
    flaky.result_failures_left = 1
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.mask_files() == []  # 结果没入库，掩码记录也没有
    assert set(h.stored().results) == set()

    assert h.engine.resume(OPERATOR)  # 只有存储原因时用恢复
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert len(h.mask_files()) == 8 * 4
    h.assert_media_intact()
    h.assert_clean()


def test_a_failed_archive_turns_the_row_into_a_retryable_analysis_failure(
    harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = mask_archive.archive_masks
    calls: list[int] = []

    def flaky(request: RowAnalysisRequest, result: RowAnalysisResult, at: datetime) -> list:  # type: ignore[type-arg]
        calls.append(request.row_index)
        if len(calls) == 1:
            raise MaskArchiveError("磁盘已满")
        return real(request, result, at)

    monkeypatch.setattr("gelinspect.services.engine_run.archive_masks", flaky)
    h = harness(tubes_of_rows(1))
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.stored().results == {}  # 绝不在没有存档的情况下默认出结果
    assert h.mask_files() == []
    assert h.engine.retry_analysis(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert len(h.mask_files()) == 32
    assert h.device.flipped_rows() == [1]  # 重试没有再翻转
    assert any("磁盘已满" in d for d in h.audit_details(AuditAction.ENGINE_ALERT))
    h.assert_clean()


def test_the_flip_angle_is_kept_with_every_tube_result(harness: HarnessFactory) -> None:
    h = harness(tubes_of_rows(1))
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.finished()
    angles = {r.features.get("flip_angle_deg") for r in h.stored().results.values()}
    assert len(angles) == 1 and next(iter(angles)) is not None


def _request(directory: Path | None) -> RowAnalysisRequest:
    image = np.zeros((20, 40, 3), np.uint8)
    path = None if directory is None else directory / "after_03.png"
    stamp = datetime(2026, 11, 5, tzinfo=UTC)
    frame = CapturedFrame(image=image, captured_at=stamp, source_path=path)
    return RowAnalysisRequest(
        experiment_id="E1",
        row_index=1,
        tubes=[TubeSlot.from_tube_no(1)],
        frames_before=[frame] * 3,
        frames_after=[frame] * 3,
    )


def _result(*, gel: bool = True) -> RowAnalysisResult:
    tube = np.zeros((20, 40), np.uint8)
    tube[2:18, 5:30] = 255
    masks = TubeMasks(
        tube_before=tube,
        tube_after=tube,
        gel_before=tube if gel else None,
        gel_after=tube if gel else None,
    )
    return RowAnalysisResult(
        analyzer_info=AnalyzerInfo(model_version="m", config_version="c"),
        elapsed_ms=1.0,
        tubes=[
            TubeAnalysis(tube_no=1, status="ok", state="fully_gelled", masks=masks, score=0.9)  # type: ignore[arg-type]
        ],
    )


def test_gel_masks_are_optional_and_nothing_is_overwritten(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    first = archive_masks(_request(tmp_path), _result(gel=False), now)
    assert sorted(Path(m.path).name for m in first) == [
        "mask_t01_tube_after.png",
        "mask_t01_tube_before.png",
    ]
    second = archive_masks(_request(tmp_path), _result(gel=False), now)
    assert {Path(m.path).name for m in second} == {
        "mask_t01_tube_after_02.png",
        "mask_t01_tube_before_02.png",
    }
    assert latest_masks([*first, *second])[(1, "tube", "after")].path.endswith("_02.png")


def test_archive_cleans_up_and_reports_when_the_directory_is_unknown_or_unwritable(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    with pytest.raises(MaskArchiveError, match="目录"):
        archive_masks(_request(None), _result(), now)
    missing = tmp_path / "gone"
    with pytest.raises(MaskArchiveError, match="写盘失败"):
        archive_masks(_request(missing), _result(), now)
    assert not missing.exists()
