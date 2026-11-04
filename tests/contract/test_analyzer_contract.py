"""``GelAnalyzer`` 契约测试，可校验任意后端，指定方式见 ``conftest.py``。"""

import warnings
from pathlib import Path

import numpy as np
import pytest

from gelinspect.interfaces.analysis import (
    AnalyzerError,
    AnalyzerInfo,
    GelAnalyzer,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysisStatus,
    check_row_result,
)
from tests.contract.conftest import AnalyzerFactory, RequestFactory
from tests.contract.fake_analyzer import FakeGelAnalyzer

ROW_BUDGET_MS = 2000


def assert_gel_missing_fields_are_none(result: RowAnalysisResult) -> None:
    for tube in result.tubes:
        features = tube.features
        if features.gel_missing_before:
            assert tube.status is TubeAnalysisStatus.GEL_MISSING
            assert features.area_before is None
            assert features.centroid_before is None
            assert features.axial_before is None
            assert tube.masks is None or tube.masks.gel_before is None
        if features.gel_missing_after:
            assert tube.status is TubeAnalysisStatus.GEL_MISSING
            assert features.area_after is None
            assert features.centroid_after is None
            assert features.axial_after is None
            assert tube.masks is None or tube.masks.gel_after is None
        if features.gel_missing_before or features.gel_missing_after:
            assert features.delta_area_pct is None
            assert features.delta_s is None


def assert_contract(request: RowAnalysisRequest, result: RowAnalysisResult) -> None:
    assert check_row_result(request, result) == []
    assert_gel_missing_fields_are_none(result)


def test_backend_implements_protocol(analyzer_factory: AnalyzerFactory) -> None:
    assert isinstance(analyzer_factory(), GelAnalyzer)


def test_load_returns_versions(
    analyzer_factory: AnalyzerFactory, analyzer_config: Path | None
) -> None:
    instance = analyzer_factory()
    info = instance.load(analyzer_config)
    instance.close()
    assert isinstance(info, AnalyzerInfo)


def test_analyze_before_load_raises_analyzer_error(
    analyzer_factory: AnalyzerFactory, make_request: RequestFactory
) -> None:
    with pytest.raises(AnalyzerError):
        analyzer_factory().analyze_row(make_request())


def test_close_is_idempotent_and_blocks_analysis(
    analyzer_factory: AnalyzerFactory,
    analyzer_config: Path | None,
    make_request: RequestFactory,
) -> None:
    instance = analyzer_factory()
    instance.load(analyzer_config)
    instance.close()
    instance.close()
    with pytest.raises(AnalyzerError):
        instance.analyze_row(make_request())


def test_can_load_again_after_close(
    analyzer_factory: AnalyzerFactory,
    analyzer_config: Path | None,
    make_request: RequestFactory,
) -> None:
    instance = analyzer_factory()
    instance.load(analyzer_config)
    instance.close()
    instance.load(analyzer_config)
    request = make_request()
    assert_contract(request, instance.analyze_row(request))
    instance.close()


@pytest.mark.parametrize(
    ("row_index", "tube_nos"),
    [
        (1, None),
        (10, None),
        (1, [1]),
        (2, [16, 9, 12]),
    ],
    ids=["row-1-full", "row-10-full", "single-tube", "partial-unordered"],
)
def test_tubes_masks_and_gel_missing_satisfy_contract(
    analyzer: GelAnalyzer,
    make_request: RequestFactory,
    row_index: int,
    tube_nos: list[int] | None,
) -> None:
    request = make_request(row_index=row_index, tube_nos=tube_nos)
    assert_contract(request, analyzer.analyze_row(request))


def test_masks_have_image_shape_and_uint8_dtype(
    analyzer: GelAnalyzer, make_request: RequestFactory
) -> None:
    request = make_request()
    for tube in analyzer.analyze_row(request).tubes:
        if tube.masks is None:
            continue
        for mask in (
            tube.masks.tube_before,
            tube.masks.tube_after,
            tube.masks.gel_before,
            tube.masks.gel_after,
        ):
            if mask is not None:
                assert mask.shape == request.image_size
                assert mask.dtype == np.uint8


def test_result_reports_loaded_versions(
    analyzer_factory: AnalyzerFactory,
    analyzer_config: Path | None,
    make_request: RequestFactory,
) -> None:
    instance = analyzer_factory()
    info = instance.load(analyzer_config)
    assert instance.analyze_row(make_request()).analyzer_info == info
    instance.close()


@pytest.mark.parametrize("kind", ["black", "white", "noise"])
def test_unusable_images_use_status_not_exception(
    analyzer: GelAnalyzer, make_request: RequestFactory, kind: str
) -> None:
    shape = (1080, 1920, 3)
    if kind == "noise":
        image = np.random.default_rng(0).integers(0, 256, shape, dtype=np.uint8)
    else:
        image = np.full(shape, 0 if kind == "black" else 255, dtype=np.uint8)
    request = make_request(image=image)
    assert_contract(request, analyzer.analyze_row(request))


def test_input_frames_are_not_modified(analyzer: GelAnalyzer, make_request: RequestFactory) -> None:
    request = make_request()
    original = request.frames_before[0].image.copy()
    analyzer.analyze_row(request)
    assert np.array_equal(request.frames_before[0].image, original)


def test_full_row_time_budget(analyzer: GelAnalyzer, make_request: RequestFactory) -> None:
    # 超时在运行时只告警不中断，这里同样只告警：耗时取决于运行机器，需要在工控机上验收。
    result = analyzer.analyze_row(make_request())
    assert result.elapsed_ms >= 0
    if result.elapsed_ms > ROW_BUDGET_MS:
        warnings.warn(f"8 支耗时 {result.elapsed_ms:.0f} ms，超过 {ROW_BUDGET_MS} ms", stacklevel=1)


def test_fake_covers_every_status(make_request: RequestFactory) -> None:
    # 确认假实现确实走到了 gel_missing 等分支，否则上面的条件断言可能形同虚设。
    fake = FakeGelAnalyzer()
    fake.load(None)
    result = fake.analyze_row(make_request())
    assert {tube.status for tube in result.tubes} == set(TubeAnalysisStatus)
    missing = [t for t in result.tubes if t.status is TubeAnalysisStatus.GEL_MISSING]
    assert any(t.features.gel_missing_before for t in missing)
    assert any(t.features.gel_missing_after for t in missing)
