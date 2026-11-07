"""数据契约的校验器和 ``check_row_result`` 自身的测试：确认违规确实会被发现。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.interfaces.analysis import (
    AnalyzerInfo,
    CapturedFrame,
    GelFeatures,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeAnalysis,
    TubeMasks,
    TubeSlot,
    check_row_result,
)
from gelinspect.interfaces.device import DeviceController, DeviceStatus, TrayPosition
from gelinspect.interfaces.traceability import ReagentCode
from gelinspect.interfaces.video import VideoConfig, VideoFrame
from tests.contract.conftest import RequestFactory

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
INFO = AnalyzerInfo(model_version="m", config_version="c")


def image(shape: tuple[int, ...] = (6, 8, 3), dtype: Any = np.uint8) -> Any:
    return np.zeros(shape, dtype=dtype)


def mask(shape: tuple[int, int] = (1080, 1920)) -> Any:
    return np.zeros(shape, dtype=np.uint8)


def masks() -> TubeMasks:
    return TubeMasks(tube_before=mask(), tube_after=mask(), gel_before=mask(), gel_after=mask())


def frames(count: int = 3, **overrides: Any) -> list[CapturedFrame]:
    fields: dict[str, Any] = {"image": image(), "captured_at": NOW} | overrides
    return [CapturedFrame(**fields) for _ in range(count)]


def request_fields(**overrides: Any) -> dict[str, Any]:
    return {
        "experiment_id": "e1",
        "row_index": 1,
        "tubes": [TubeSlot.from_tube_no(1)],
        "frames_before": frames(),
        "frames_after": frames(),
    } | overrides


@pytest.mark.parametrize(
    "bad",
    [
        image((6, 8)),
        image((6, 8, 4)),
        image((6, 8, 1)),
        image((6, 8, 3), np.float32),
        image((0, 8, 3)),
        "not-an-array",
    ],
)
def test_captured_frame_rejects_bad_images(bad: Any) -> None:
    with pytest.raises(ValidationError):
        CapturedFrame(image=bad, captured_at=NOW)


def test_captured_frame_keeps_array_without_copy() -> None:
    source = image()
    assert CapturedFrame(image=source, captured_at=NOW).image is source


def test_captured_frame_requires_aware_time() -> None:
    with pytest.raises(ValidationError):
        CapturedFrame(image=image(), captured_at=datetime(2026, 11, 4, 9, 0))


def test_contract_models_are_frozen_and_reject_extra_fields() -> None:
    slot = TubeSlot.from_tube_no(1)
    with pytest.raises(ValidationError):
        slot.tube_no = 2
    with pytest.raises(ValidationError):
        TubeSlot(tube_no=1, position_in_row=1, extra=1)  # type: ignore[call-arg]


@pytest.mark.parametrize(("tube_no", "position"), [(1, 2), (9, 2), (0, 1), (97, 1), (1, 9)])
def test_tube_slot_rejects_inconsistent_position(tube_no: int, position: int) -> None:
    with pytest.raises(ValidationError):
        TubeSlot(tube_no=tube_no, position_in_row=position)


@pytest.mark.parametrize(
    "overrides",
    [
        {"row_index": 0},
        {"row_index": 13},
        {"tubes": []},
        {"tubes": [TubeSlot.from_tube_no(9)]},
        {"tubes": [TubeSlot.from_tube_no(1), TubeSlot.from_tube_no(1)]},
        {"frames_before": frames(2)},
        {"frames_after": frames(2)},
        {"frames_after": frames(image=image((6, 9, 3)))},
    ],
    ids=[
        "row-low",
        "row-high",
        "no-tubes",
        "foreign-tube",
        "duplicate-tube",
        "few-before",
        "few-after",
        "size-mismatch",
    ],
)
def test_row_request_rejects_invalid_input(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RowAnalysisRequest(**request_fields(**overrides))


def test_row_request_rejects_before_frames_after_flip() -> None:
    later = NOW.replace(hour=10)
    with pytest.raises(ValidationError):
        RowAnalysisRequest(**request_fields(frames_before=frames(captured_at=later)))


def test_row_request_image_size() -> None:
    assert RowAnalysisRequest(**request_fields()).image_size == (6, 8)


@pytest.mark.parametrize(
    "fields",
    [
        {"gel_missing_before": True, "area_before": 1.0},
        {"gel_missing_before": True, "centroid_before": (1.0, 1.0)},
        {"gel_missing_before": True, "axial_before": 1.0},
        {"gel_missing_after": True, "area_after": 1.0},
        {"gel_missing_after": True, "centroid_after": (1.0, 1.0)},
        {"gel_missing_after": True, "axial_after": 1.0},
        {"gel_missing_after": True, "delta_area_pct": 1.0},
        {"gel_missing_before": True, "delta_s": 1.0},
    ],
)
def test_gel_features_reject_values_when_gel_missing(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GelFeatures(**fields)


def test_gel_features_keep_present_side_when_other_side_missing() -> None:
    features = GelFeatures(area_before=10.0, gel_missing_after=True, sigma_area=0.1)
    assert features.area_before == 10.0


def test_gel_features_are_json_serializable_for_storage() -> None:
    features = GelFeatures(centroid_before=(1.0, 2.0))
    assert GelFeatures.model_validate_json(features.model_dump_json()) == features


@pytest.mark.parametrize(
    "bad",
    [mask().astype(np.float32), np.zeros((1080, 1920, 3), dtype=np.uint8), "not-an-array"],
    ids=["float-dtype", "three-channels", "not-array"],
)
def test_tube_masks_reject_bad_arrays(bad: Any) -> None:
    with pytest.raises(ValidationError):
        TubeMasks(tube_before=bad, tube_after=mask())


def test_tube_masks_reject_mixed_shapes() -> None:
    with pytest.raises(ValidationError):
        TubeMasks(tube_before=mask(), tube_after=mask((10, 10)))


@pytest.mark.parametrize(
    "fields",
    [
        {"status": TubeAnalysisStatus.OK, "masks": masks()},
        {"status": TubeAnalysisStatus.OK, "state": GelState.NOT_GELLED},
        {
            "status": TubeAnalysisStatus.OK,
            "state": GelState.NOT_GELLED,
            "masks": masks(),
            "features": GelFeatures(gel_missing_after=True),
        },
        {"status": TubeAnalysisStatus.GEL_MISSING},
        {"status": TubeAnalysisStatus.FAILED},
        {"status": TubeAnalysisStatus.FAILED, "message": "x", "state": GelState.NOT_GELLED},
        {"status": TubeAnalysisStatus.TUBE_NOT_FOUND, "message": "x", "score": 0.5},
        {"status": TubeAnalysisStatus.TUBE_NOT_FOUND, "message": "x", "masks": masks()},
        {"status": TubeAnalysisStatus.LOW_QUALITY},
        {
            "status": TubeAnalysisStatus.GEL_MISSING,
            "features": GelFeatures(gel_missing_after=True),
            "masks": masks(),
        },
        {"status": TubeAnalysisStatus.FAILED, "message": "x", "confidence": 1.5},
    ],
    ids=[
        "ok-no-state",
        "ok-no-masks",
        "ok-with-missing-flag",
        "gel-missing-no-flag",
        "failed-no-message",
        "failed-with-state",
        "not-found-with-score",
        "not-found-with-masks",
        "low-quality-no-message",
        "gel-missing-with-gel-mask",
        "confidence-out-of-range",
    ],
)
def test_tube_analysis_rejects_inconsistent_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TubeAnalysis(tube_no=1, **fields)


def test_algorithm_contract_has_no_interpretation_field() -> None:
    forbidden = {"interpretation", "positive", "negative", "is_positive", "result"}
    assert not forbidden & set(TubeAnalysis.model_fields)
    assert not forbidden & set(RowAnalysisResult.model_fields)


def failed(tube_no: int) -> TubeAnalysis:
    return TubeAnalysis(tube_no=tube_no, status=TubeAnalysisStatus.FAILED, message="x")


def result_of(*tubes: TubeAnalysis) -> RowAnalysisResult:
    return RowAnalysisResult(analyzer_info=INFO, elapsed_ms=1.0, tubes=list(tubes))


def test_check_row_result_accepts_matching_tubes(make_request: RequestFactory) -> None:
    request = make_request(tube_nos=[1, 2])
    assert check_row_result(request, result_of(failed(2), failed(1))) == []


@pytest.mark.parametrize(
    ("tube_nos", "expected"),
    [
        ([1], "缺少管号"),
        ([1, 2, 3], "多余管号"),
        ([1, 2, 2], "重复管号"),
    ],
)
def test_check_row_result_reports_tube_mismatch(
    make_request: RequestFactory, tube_nos: list[int], expected: str
) -> None:
    request = make_request(tube_nos=[1, 2])
    violations = check_row_result(request, result_of(*(failed(no) for no in tube_nos)))
    assert any(expected in violation for violation in violations)


def ok_with(tube_masks: TubeMasks) -> TubeAnalysis:
    return TubeAnalysis(
        tube_no=1, status=TubeAnalysisStatus.OK, state=GelState.NOT_GELLED, masks=tube_masks
    )


def test_check_row_result_reports_wrong_mask_shape(make_request: RequestFactory) -> None:
    small = TubeMasks(tube_before=mask((10, 10)), tube_after=mask((10, 10)))
    violations = check_row_result(make_request(tube_nos=[1]), result_of(ok_with(small)))
    assert any("形状" in violation for violation in violations)


def test_check_row_result_reports_non_binary_values(make_request: RequestFactory) -> None:
    gray = mask()
    gray[0, 0] = 128
    violations = check_row_result(
        make_request(tube_nos=[1]),
        result_of(ok_with(TubeMasks(tube_before=gray, tube_after=mask()))),
    )
    assert any("0 和 255" in violation for violation in violations)


def test_video_contracts_validate_inputs() -> None:
    frame = VideoFrame(image=image(), sequence=0, captured_at=NOW, monotonic_s=1.0)
    assert frame.sequence == 0
    with pytest.raises(ValidationError):
        VideoFrame(image=image((6, 8)), sequence=0, captured_at=NOW, monotonic_s=1.0)
    with pytest.raises(ValidationError):
        VideoConfig(device="")


def test_device_status_defaults_to_unknown_tray() -> None:
    assert DeviceStatus(connected=False, read_at=NOW).tray_position is TrayPosition.UNKNOWN


def test_reagent_code_requires_batch() -> None:
    with pytest.raises(ValidationError):
        ReagentCode(raw="x", product_code="p", batch_no="")


def test_path_field_accepts_source_path() -> None:
    frame = CapturedFrame(image=image(), captured_at=NOW, source_path=Path("a.png"))
    assert frame.source_path == Path("a.png")


def test_device_controller_protocol_rejects_incomplete_object() -> None:
    assert not isinstance(object(), DeviceController)
