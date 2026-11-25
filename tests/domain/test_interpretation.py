import pytest

from gelinspect.domain.enums import GelState, Interpretation, TubeAnalysisStatus
from gelinspect.domain.interpretation import GelStateMapping

P = Interpretation.POSITIVE
N = Interpretation.NEGATIVE


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (GelState.FULLY_GELLED, P),
        (GelState.NOT_GELLED, N),
        (GelState.PARTIALLY_GELLED, N),
    ],
)
def test_default_mapping(state: GelState, expected: Interpretation) -> None:
    assert GelStateMapping().interpret(TubeAnalysisStatus.OK, state) is expected


@pytest.mark.parametrize("status", list(TubeAnalysisStatus))
def test_undetermined_is_always_none(status: TubeAnalysisStatus) -> None:
    assert GelStateMapping().interpret(status, None) is None


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
    assert GelStateMapping().interpret(status, state) is None


def test_gel_missing_uses_state_from_algorithm() -> None:
    mapping = GelStateMapping()
    assert mapping.interpret(TubeAnalysisStatus.GEL_MISSING, GelState.NOT_GELLED) is N


def test_mapping_is_configurable() -> None:
    mapping = GelStateMapping.from_config({"partially_gelled": "positive"})
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.PARTIALLY_GELLED) is P
    assert mapping.interpret(TubeAnalysisStatus.OK, GelState.NOT_GELLED) is N


def test_configured_mapping_still_never_judges_failures() -> None:
    mapping = GelStateMapping(partially_gelled=N, fully_gelled=N, not_gelled=N)
    assert mapping.interpret(TubeAnalysisStatus.FAILED, None) is None


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
