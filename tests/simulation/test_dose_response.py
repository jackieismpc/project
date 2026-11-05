"""默认场景的剂量关系：高浓度阳性、低浓度阴性、阴性对照阴性，边界有部分凝固，可复现。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gelinspect.domain.conclusions import evaluate_conclusion
from gelinspect.domain.entities import Sample, TubeAssignment, TubeResult
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    GelState,
    Interpretation,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.device import PlatePlan, PlateTube
from simulation.scenario import Scenario, dose_response_states
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource


def plan(labeled: float | None = 0.25) -> PlatePlan:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    return PlatePlan(
        "EXP",
        labeled,
        tuple(PlateTube(i.tube_no, i.role, i.spike_for(labeled)) for i in template.items),
    )


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("labeled", [0.5, 0.25, 0.03])
def test_dose_relation_holds_for_every_seed(seed: int, labeled: float) -> None:
    states = dose_response_states(plan(labeled), seed)
    assert all(states[no] is GelState.FULLY_GELLED for no in range(1, 5))
    assert all(states[no] is GelState.NOT_GELLED for no in range(13, 18))
    for offset in range(4):
        # 同一系列从高到低不会先未凝固再完全凝固。
        readings = [states[level * 4 + offset + 1] for level in range(4)]
        first_negative = readings.index(GelState.NOT_GELLED)
        assert GelState.FULLY_GELLED not in readings[first_negative:]


@pytest.mark.parametrize("seed", range(40))
def test_every_seed_gives_a_valid_lambda_c_after_review(seed: int) -> None:
    """部分凝固无论人工判阳还是判阴，默认场景都算得出有效的 λc。"""
    p = plan(0.25)
    states = dose_response_states(p, seed)
    assignments = [
        TubeAssignment(t.tube_no, Sample(str(t.tube_no), "样品"), t.role, None, t.spiked_endotoxin)
        for t in p.tubes
    ]
    for reviewed in (Interpretation.POSITIVE, Interpretation.NEGATIVE):
        results: dict[int, TubeResult] = {}
        for no, state in states.items():
            result = TubeResult.from_analysis(
                tube_no=no, status=TubeAnalysisStatus.OK, state=state, mapping=GelStateMapping()
            )
            results[no] = (
                result.with_review(
                    interpretation=reviewed,
                    reviewed_by="a",
                    reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
                if result.needs_review
                else result
            )
        conclusion = evaluate_conclusion(
            ExperimentType.SENSITIVITY_CONFIRMATION,
            assignments,
            results,
            0.25,
            layout_confirmed=True,
        )
        assert conclusion.verdict in (ConclusionVerdict.PASS, ConclusionVerdict.PENDING)
        assert conclusion.value is not None and 0.125 <= conclusion.value <= 0.5


def test_default_seed_has_tubes_for_review() -> None:
    states = dose_response_states(plan(0.25), 0)
    assert GelState.PARTIALLY_GELLED in states.values()


def test_some_seeds_differ_but_the_same_seed_repeats() -> None:
    assert dose_response_states(plan(), 3) == dose_response_states(plan(), 3)
    assert len({tuple(sorted(dose_response_states(plan(), s).items())) for s in range(20)}) > 1


def test_unconfirmed_tubes_keep_the_ratio_fallback() -> None:
    unconfirmed = PlatePlan(
        "E", 0.5, (PlateTube(1, TubeRole.UNCONFIRMED), PlateTube(2, TubeRole.TEST))
    )
    assert dose_response_states(unconfirmed, 0) == {}
    scenario = Scenario(seed=1, plate_plan=unconfirmed)
    assert scenario.state_for(1) is not None
    assert scenario.state_for(3) is None  # 不在孔板上


def test_missing_lambda_keeps_the_ratio_fallback() -> None:
    assert dose_response_states(plan(None), 0) == {}


def test_explicit_states_win_over_the_plan() -> None:
    scenario = Scenario(tube_states={1: GelState.NOT_GELLED}, plate_plan=plan())
    assert scenario.state_for(1) is GelState.NOT_GELLED


def test_negative_control_is_never_positive_even_with_a_spike() -> None:
    p = PlatePlan("E", 0.5, (PlateTube(1, TubeRole.NEGATIVE_CONTROL, 1.0),))
    assert dose_response_states(p)[1] is GelState.NOT_GELLED


def test_video_source_follows_the_device_plan() -> None:
    device = SimDevice()
    source = SimVideoSource(scene=device)
    device.load_plate_plan(plan())
    source._sync_plate_plan()  # noqa: SLF001
    assert source.scenario.plate_plan is device.plate_plan
    assert source.scenario.state_for(1) is GelState.FULLY_GELLED
    assert source.scenario.state_for(50) is None
