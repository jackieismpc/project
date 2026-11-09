"""MockAnalyzer：契约、测量结果、配置和故障注入。"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from gelinspect.domain.enums import GelState, TubeAnalysisStatus
from gelinspect.interfaces.analysis import (
    AnalyzerError,
    GelAnalyzer,
    RowAnalysisRequest,
    check_row_result,
)
from simulation.image_synth import SynthFrame, TubeSpec, render_pair, render_row
from simulation.mock_analyzer import MockAnalyzer
from simulation.scenario import Scenario
from tests.simulation.conftest import SMALL, S, request_from

ROOT = Path(__file__).parents[2]
Mixed = tuple[RowAnalysisRequest, SynthFrame, SynthFrame]


def loaded(**kwargs: object) -> MockAnalyzer:
    analyzer = MockAnalyzer(**kwargs)  # type: ignore[arg-type]
    analyzer.load(None)
    return analyzer


@pytest.mark.parametrize("extra", [[], ["--analyzer-config=simulation/analyzer.example.yaml"]])
def test_passes_existing_contract_tests(extra: list[str]) -> None:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/contract",
        "--analyzer-backend=simulation.mock_analyzer:MockAnalyzer",
        "-q",
        "-p",
        "no:cacheprovider",
        *extra,
    ]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "passed" in done.stdout


def test_is_protocol_and_constructs_without_arguments() -> None:
    assert isinstance(MockAnalyzer(), GelAnalyzer)


def test_measures_states_from_pixels(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    result = loaded().analyze_row(request)
    assert check_row_result(request, result) == []
    states = [S.FULLY_GELLED] * 3 + [S.PARTIALLY_GELLED] * 3 + [S.NOT_GELLED] * 2
    assert [t.state for t in result.tubes] == states
    assert all(t.status is TubeAnalysisStatus.OK for t in result.tubes)


def test_delta_a_ordering_and_features(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    tubes = loaded().analyze_row(request).tubes
    delta = [t.features.delta_area_pct for t in tubes]
    shift = [t.features.delta_s for t in tubes]
    scores = [t.score for t in tubes]
    assert all(v is not None for v in (*delta, *shift, *scores))
    delta_f = [v for v in delta if v is not None]
    shift_f = [v for v in shift if v is not None]
    score_f = [v for v in scores if v is not None]
    assert max(delta_f[0:3]) < min(delta_f[3:6]) < max(delta_f[3:6]) < min(delta_f[6:8])
    assert max(shift_f[0:3]) < min(shift_f[3:6]) < max(shift_f[3:6]) < min(shift_f[6:8])
    assert min(score_f[0:3]) > max(score_f[3:6]) > min(score_f[3:6]) > max(score_f[6:8])
    for tube in tubes:
        assert tube.features.sigma_area is not None
        assert tube.features.sigma_axial is not None
        assert tube.features.axial_before is not None and 0 < tube.features.axial_before < 1


def test_masks_match_ground_truth(mixed_request: Mixed) -> None:
    request, before, after = mixed_request
    for tube in loaded().analyze_row(request).tubes:
        position = (tube.tube_no - 1) % 8 + 1
        assert tube.masks is not None
        pairs = [
            (tube.masks.tube_before, before.tube_mask(position)),
            (tube.masks.tube_after, after.tube_mask(position)),
            (tube.masks.gel_before, before.gel_mask(position, largest_only=True)),
            (tube.masks.gel_after, after.gel_mask(position, largest_only=True)),
        ]
        for mask, truth in pairs:
            assert mask is not None and mask.dtype == np.uint8 and mask.shape == truth.shape
            inter = np.count_nonzero((mask > 0) & (truth > 0))
            union = np.count_nonzero((mask > 0) | (truth > 0))
            assert inter / union > 0.85


def test_not_gelled_without_visible_liquid_is_gel_missing() -> None:
    spec = TubeSpec(state=S.NOT_GELLED, pool_fraction=0.0)
    before, after = render_pair([spec], SMALL)
    request = request_from(before, after, positions=[1])
    (tube,) = loaded().analyze_row(request).tubes
    assert tube.status is TubeAnalysisStatus.GEL_MISSING
    assert tube.state is S.NOT_GELLED
    assert tube.features.gel_missing_after and not tube.features.gel_missing_before
    assert tube.features.delta_area_pct is None
    assert tube.masks is not None and tube.masks.gel_after is None


def test_empty_slot_is_tube_not_found() -> None:
    specs: list[TubeSpec | None] = [TubeSpec(state=S.FULLY_GELLED), None]
    before, after = render_pair(specs, SMALL)
    request = request_from(before, after, positions=[1, 2])
    tubes = loaded().analyze_row(request).tubes
    assert tubes[0].status is TubeAnalysisStatus.OK
    assert tubes[1].status is TubeAnalysisStatus.TUBE_NOT_FOUND
    assert tubes[1].masks is None and tubes[1].message


@pytest.mark.parametrize(("value", "message"), [(5, "过暗"), (255, "过曝")])
def test_unusable_exposure_is_low_quality(value: int, message: str) -> None:
    image = np.full((540, 960, 3), value, dtype=np.uint8)
    frame = SynthFrame(image=image)
    request = request_from(frame, frame)
    for tube in loaded().analyze_row(request).tubes:
        assert tube.status is TubeAnalysisStatus.LOW_QUALITY
        assert message in (tube.message or "")


def test_does_not_modify_input(mixed_request: Mixed) -> None:
    request, before, _ = mixed_request
    original = before.image.copy()
    loaded().analyze_row(request)
    assert np.array_equal(before.image, original)


def test_tube_fault_injection(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    faults = {
        1: TubeAnalysisStatus.LOW_QUALITY,
        2: TubeAnalysisStatus.FAILED,
        3: TubeAnalysisStatus.TUBE_NOT_FOUND,
    }
    result = loaded(tube_faults=faults).analyze_row(request)
    assert check_row_result(request, result) == []
    by_no = {t.tube_no: t for t in result.tubes}
    for no, status in faults.items():
        assert by_no[no].status is status
        assert by_no[no].message
    assert by_no[4].status is TubeAnalysisStatus.OK


def test_invalid_fault_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="故障状态"):
        MockAnalyzer(tube_faults={1: TubeAnalysisStatus.OK})


def test_whole_row_failures_raise_analyzer_error(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    with pytest.raises(AnalyzerError):
        loaded(fail_all=True).analyze_row(request)
    with pytest.raises(AnalyzerError):
        MockAnalyzer(fail_load=True).load(None)


def test_simulated_delay_is_reported_without_real_sleep(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    waited: list[float] = []
    result = loaded(delay_ms=1500, sleep=waited.append).analyze_row(request)
    assert waited == [1.5]
    assert result.elapsed_ms >= 1500


def test_scripted_mode_follows_scenario_and_state_ratios(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    scenario = Scenario(tube_states={1: S.NOT_GELLED, 2: S.PARTIALLY_GELLED, 3: None})
    result = loaded(scripted=True, scenario=scenario).analyze_row(request)
    assert check_row_result(request, result) == []
    by_no = {t.tube_no: t for t in result.tubes}
    assert by_no[1].state is S.NOT_GELLED and by_no[2].state is S.PARTIALLY_GELLED
    assert by_no[3].status is TubeAnalysisStatus.TUBE_NOT_FOUND
    again = loaded(scripted=True, scenario=scenario).analyze_row(request)
    assert [t.state for t in again.tubes] == [t.state for t in result.tubes]
    only_gelled = Scenario(state_ratios=(1, 0, 0))
    states = {
        t.state for t in loaded(scripted=True, scenario=only_gelled).analyze_row(request).tubes
    }
    assert states == {S.FULLY_GELLED}


def test_yaml_config(tmp_path: Path, mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    config = tmp_path / "mock.yaml"
    config.write_text(
        "scripted: true\ntube_states: {1: not_gelled}\ntube_faults: {2: failed}\ndelay_ms: 0\n",
        encoding="utf-8",
    )
    analyzer = MockAnalyzer()
    info = analyzer.load(config)
    assert info.config_version == "mock-mock"
    by_no = {t.tube_no: t for t in analyzer.analyze_row(request).tubes}
    assert by_no[1].state is GelState.NOT_GELLED
    assert by_no[2].status is TubeAnalysisStatus.FAILED
    config.write_text("tube_faults: {1: ok}\n", encoding="utf-8")
    with pytest.raises(AnalyzerError):
        analyzer.load(config)


def test_works_on_unusual_image_size_and_flip_angle() -> None:
    specs: list[TubeSpec | None] = [TubeSpec(state=S.FULLY_GELLED)]
    from simulation.image_synth import SynthConfig

    config = SynthConfig(width=1280, height=720)
    before = render_row(specs, config)
    after = render_row(specs, config, flip_phase=1.0, settle=1.0)
    request = request_from(before, after, positions=[1]).model_copy(update={"flip_angle_deg": None})
    (tube,) = loaded().analyze_row(request).tubes
    assert tube.state is S.FULLY_GELLED


def test_returns_score_terms_and_thresholds(mixed_request: Mixed) -> None:
    request, _, _ = mixed_request
    analyzer = loaded()
    result = analyzer.analyze_row(request)
    assert result.analyzer_info.thresholds is not None
    assert result.analyzer_info.thresholds.fully_gelled_min == 0.85
    for tube in result.tubes:
        assert tube.score_terms is not None and tube.score is not None
        assert list(tube.score_terms) == ["delta_area_pct", "delta_s", "sigma_area", "sigma_axial"]
        assert [t.weight for t in tube.score_terms.values()] == [0.5, 0.3, 0.1, 0.1]
        total = sum(t.weight * t.normalized for t in tube.score_terms.values())
        assert total == pytest.approx(tube.score)


def test_weights_and_thresholds_come_from_config(mixed_request: Mixed, tmp_path: Path) -> None:
    config = tmp_path / "calibrated.yaml"
    config.write_text(
        "weights: [1, 0, 0, 0]\nthresholds: {not_gelled_max: 0.1, fully_gelled_min: 0.2}\n",
        encoding="utf-8",
    )
    analyzer = MockAnalyzer()
    info = analyzer.load(config)
    assert info.config_version == "mock-calibrated"
    assert info.thresholds is not None and info.thresholds.not_gelled_max == 0.1
    request, _, _ = mixed_request
    for tube in analyzer.analyze_row(request).tubes:
        assert tube.score_terms is not None and tube.score is not None
        assert tube.score == pytest.approx(tube.score_terms["delta_area_pct"].normalized)
        expected = (
            S.FULLY_GELLED
            if tube.score >= 0.2
            else S.NOT_GELLED
            if tube.score <= 0.1
            else S.PARTIALLY_GELLED
        )
        assert tube.state is expected


@pytest.mark.parametrize(
    "text",
    [
        "weights: [0.5, 0.5, 0.5, 0.5]\n",
        "weights: [1, 0]\n",
        "limits: [1, 1, 0, 1]\n",
        "thresholds: {not_gelled_max: 0.9, fully_gelled_min: 0.1}\n",
    ],
)
def test_rejects_bad_scoring_config(text: str, tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(AnalyzerError):
        MockAnalyzer().load(config)
