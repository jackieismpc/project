import pytest

from gelinspect.domain.entities import TubeResult
from gelinspect.domain.enums import GelState, Interpretation, ReviewStatus, TubeAnalysisStatus
from gelinspect.domain.interpretation import GelStateMapping

P = Interpretation.POSITIVE
N = Interpretation.NEGATIVE
R = Interpretation.PENDING_REVIEW


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (GelState.FULLY_GELLED, P),
        (GelState.NOT_GELLED, N),
        (GelState.PARTIALLY_GELLED, R),
    ],
)
def test_default_mapping(state: GelState, expected: Interpretation) -> None:
    assert GelStateMapping().interpret(TubeAnalysisStatus.OK, state) is expected


@pytest.mark.parametrize("status", list(TubeAnalysisStatus))
def test_undetermined_is_always_pending_review(status: TubeAnalysisStatus) -> None:
    assert GelStateMapping().interpret(status, None) is R


@pytest.mark.parametrize(
    "status",
    [
        TubeAnalysisStatus.LOW_QUALITY,
        TubeAnalysisStatus.TUBE_NOT_FOUND,
        TubeAnalysisStatus.FAILED,
    ],
)
@pytest.mark.parametrize("state", list(GelState))
def test_untrusted_status_ignores_state(status: TubeAnalysisStatus, state: GelState) -> None:
    assert GelStateMapping().interpret(status, state) is R


def test_gel_missing_uses_state_from_algorithm() -> None:
    mapping = GelStateMapping()
    assert mapping.interpret(TubeAnalysisStatus.GEL_MISSING, GelState.NOT_GELLED) is N


def test_mapping_is_configurable() -> None:
    mapping = GelStateMapping.from_config({"partially_gelled": "positive"})
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.PARTIALLY_GELLED) is P
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.NOT_GELLED) is N


def test_configured_mapping_still_never_judges_failures() -> None:
    mapping = GelStateMapping(partially_gelled=N, fully_gelled=N, not_gelled=N)
    assert mapping.interpret(TubeAnalysisStatus.FAILED, None) is R


@pytest.mark.parametrize(
    "config",
    [{"unknown": "positive"}, {"not_gelled": "maybe"}],
)
def test_invalid_config_is_rejected(config: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        GelStateMapping.from_config(config)


def test_config_round_trip() -> None:
    mapping = GelStateMapping(partially_gelled=P)
    assert GelStateMapping.from_config(mapping.to_config()) == mapping


def test_low_confidence_threshold_is_off_by_default() -> None:
    mapping = GelStateMapping()
    assert mapping.low_confidence_threshold is None
    assert not mapping.is_low_confidence(None)
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.FULLY_GELLED, 0.01) is (
        Interpretation.POSITIVE
    )
    assert "low_confidence_threshold" not in mapping.to_config()


def test_low_confidence_goes_to_review_when_enabled() -> None:
    mapping = GelStateMapping.from_config({}, low_confidence_threshold=0.7)
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.NOT_GELLED, 0.69) is (
        Interpretation.PENDING_REVIEW
    )
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.NOT_GELLED, 0.7) is (
        Interpretation.NEGATIVE
    )
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.FULLY_GELLED, None) is (
        Interpretation.PENDING_REVIEW
    )
    assert mapping.to_config()["low_confidence_threshold"] == "0.7"


def test_low_confidence_threshold_must_be_a_probability() -> None:
    with pytest.raises(ValueError):
        GelStateMapping(low_confidence_threshold=1.5)


def test_low_confidence_result_explains_itself() -> None:
    result = TubeResult.from_analysis(
        tube_no=1,
        status=TubeAnalysisStatus.OK,
        state=GelState.FULLY_GELLED,
        mapping=GelStateMapping(low_confidence_threshold=0.7),
        confidence=0.5,
    )
    assert result.needs_review
    assert result.review_status is ReviewStatus.PENDING
    assert result.gel_state is GelState.FULLY_GELLED  # 三态原样保留
    assert result.message is not None and "0.50" in result.message and "0.7" in result.message
