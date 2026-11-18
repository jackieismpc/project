"""鲎试剂 λ 计算、用户输入覆盖和浓度固化。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gelinspect.domain.entities import Experiment, TubeResult
from gelinspect.domain.enums import (
    ExperimentType,
    Interpretation,
    ReagentKind,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentEvent
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest
from gelinspect.services.config import DEFAULT_CONFIG, ConfigError, parse_config
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import ExperimentDraft
from gelinspect.services.review_service import ReviewService
from gelinspect.services.template_service import TemplateDraft, TemplateItemInput
from simulation.reagents import WHITELIST, SimReagentRegistry
from simulation.sample_data import SENSITIVITY_STATES, sensitivity_experiment

from .conftest import GOOD_CODES, NOW, Stack, build_stack


def pending_sample(stack: Stack, experiment: Experiment | None = None) -> Experiment:
    """灵敏度示例：1–6 号阳性，7 号待复核，其余阴性，λ=0.5。"""
    target = experiment or sensitivity_experiment()
    for event in (
        ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK,
        ExperimentEvent.REAGENT_CHECK_PASSED,
        ExperimentEvent.START,
        ExperimentEvent.ROW_DUE,
        ExperimentEvent.ROW_CAPTURED,
    ):
        target.apply(event)
    for no, state in SENSITIVITY_STATES.items():
        target.record_result(
            TubeResult.from_analysis(
                tube_no=no, status=TubeAnalysisStatus.OK, state=state, mapping=GelStateMapping()
            )
        )
    target.apply(ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW)
    stack.database.experiments.save(target)
    return target


def stored(stack: Stack, experiment_id: str = "EXP-SAMPLE-0001") -> Experiment:
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None
    return experiment


def review(stack: Stack, tube_no: int, value: Interpretation, note: str = "复核") -> None:
    service = ReviewService(stack.auth, stack.database, clock=lambda: NOW)
    service.review_tube(stack.login("analyst"), "EXP-SAMPLE-0001", tube_no, value, note)


# ---------------------------------------------------------------- λ 计算


def registry_with_lambda(labeled: float | None) -> SimReagentRegistry:
    return SimReagentRegistry(
        tuple(
            replace(entry, labeled_sensitivity=labeled)
            if entry.kind is ReagentKind.LAL_REAGENT
            else entry
            for entry in WHITELIST
        )
    )


def sensitivity_request(stack: Stack, codes: dict[ReagentKind, str] | None = None) -> str:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    draft = ExperimentDraft()
    draft.choose_template(template)
    draft.auto_select()
    draft.reagent_codes.update(GOOD_CODES if codes is None else codes)
    return stack.experiments.create_experiment(stack.login("experimenter"), draft.to_request())


def test_create_experiment_freezes_spikes_from_lambda(stack: Stack) -> None:
    experiment = stored(stack, sensitivity_request(stack))
    spikes = {a.tube_no: a.spiked_endotoxin for a in experiment.assignments}
    assert spikes[1] == 0.5  # 模拟白名单的 LAL2606A，λ=0.25
    assert spikes[5] == 0.25
    assert spikes[9] == 0.125
    assert spikes[13] == 0.0625
    assert spikes[17] == 0.0 and spikes[18] == 0.0
    assert experiment.layout_confirmed is True


@pytest.mark.parametrize("labeled", [0.5, 0.25, 0.03, 0.06, 0.125])
def test_service_recomputes_spikes_for_any_lambda(tmp_path: Path, labeled: float) -> None:
    stack = build_stack(tmp_path, registry=registry_with_lambda(labeled))
    try:
        experiment = stored(stack, sensitivity_request(stack))
    finally:
        stack.database.close()
    by_tube = {a.tube_no: a.spiked_endotoxin for a in experiment.assignments}
    assert [by_tube[n] for n in (1, 5, 9, 13)] == [
        float(f"{labeled * m:.10g}") for m in (2, 1, 0.5, 0.25)
    ]


def test_service_ignores_a_spike_sent_by_the_ui_for_multiple_rows(stack: Stack) -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    draft = ExperimentDraft()
    draft.choose_template(template)
    draft.auto_select()
    draft.reagent_codes.update(GOOD_CODES)
    draft.rows[0].spiked_endotoxin = 99.0
    experiment_id = stack.experiments.create_experiment(
        stack.login("experimenter"), draft.to_request()
    )
    assert stored(stack, experiment_id).assignments[0].spiked_endotoxin == 0.5


def test_missing_lambda_refuses_to_create(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, registry=registry_with_lambda(None))
    try:
        with pytest.raises(ServiceError) as caught:
            sensitivity_request(stack)
        assert caught.value.code is ServiceErrorCode.LAMBDA_REQUIRED
    finally:
        stack.database.close()


def test_absolute_custom_template_keeps_its_values(stack: Stack) -> None:
    """自定义模板中的绝对值原样进入试验。"""
    draft_items = [
        TemplateItemInput(f"样品{i}", TubeRole.TEST, None, value)
        for i, value in enumerate([1.0, 0.5, 0.25, 0.125] * 4)
    ]
    draft_items.append(TemplateItemInput("对照", TubeRole.NEGATIVE_CONTROL, None, 0.0))
    template = stack.templates.create_template(
        stack.login("admin"),
        TemplateDraft("自定义绝对浓度", ExperimentType.SENSITIVITY_CONFIRMATION, draft_items),
    )
    draft = ExperimentDraft()
    draft.choose_template(template)
    draft.auto_select()
    draft.reagent_codes.update(GOOD_CODES)
    experiment_id = stack.experiments.create_experiment(
        stack.login("experimenter"), draft.to_request()
    )
    experiment = stored(stack, experiment_id)
    assert experiment.assignments[0].spiked_endotoxin == 1.0


def test_draft_tracks_lambda_changes_and_formats_without_float_tails() -> None:
    draft = ExperimentDraft()
    draft.choose_template(BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION])
    assert draft.lambda_missing
    assert draft.rows[0].spiked_endotoxin is None
    draft.set_labeled_sensitivity(0.03)
    assert not draft.lambda_missing
    assert [draft.rows[i].spiked_endotoxin for i in (0, 4, 8, 12, 16)] == [
        0.06,
        0.03,
        0.015,
        0.0075,
        0.0,
    ]
    assert not draft.has_sample_input()  # 按 λ 算出的值不算用户编辑
    draft.set_labeled_sensitivity(None)
    assert draft.lambda_missing and draft.rows[0].spiked_endotoxin is None


def test_config_threshold_reaches_the_mapping() -> None:
    enabled = parse_config({**DEFAULT_CONFIG, "low_confidence_threshold": 0.7})
    assert enabled.mapping().low_confidence_threshold == 0.7
    assert parse_config(dict(DEFAULT_CONFIG)).mapping().low_confidence_threshold is None
    with pytest.raises(ConfigError):
        parse_config({**DEFAULT_CONFIG, "low_confidence_threshold": 1.5})


def test_summaries_carry_the_completion_time(stack: Stack) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.POSITIVE)
    page = stack.database.experiments.query(ExperimentQuery(), PageRequest(limit=5))
    assert page.items[0].completed_at == NOW


def test_lambda_recalculation_keeps_the_users_edited_multiple() -> None:
    draft = ExperimentDraft()
    draft.choose_template(BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION])
    draft.set_labeled_sensitivity(0.25)
    assert not draft.has_sample_input()
    draft.rows[0].lambda_multiple = 3
    draft.set_labeled_sensitivity(0.5)
    assert draft.rows[0].spiked_endotoxin == 1.5
    assert draft.has_sample_input()
