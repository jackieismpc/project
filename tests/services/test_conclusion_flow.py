"""结论入库：与“已完成”同事务、复核后计算、规则变更不改写历史、低置信度、λ 计算。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import gelinspect.domain.conclusions as conclusions
from gelinspect.domain.entities import Experiment, TubeResult
from gelinspect.domain.enums import (
    AuditAction,
    ConclusionVerdict,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentKind,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest, StateConflictError
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
    service = ReviewService(stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=True)
    service.review_tube(stack.login("analyst"), "EXP-SAMPLE-0001", tube_no, value, note)


def test_no_conclusion_while_review_is_pending(stack: Stack) -> None:
    pending_sample(stack)
    experiment = stored(stack)
    assert experiment.conclusion is None
    assert experiment.measured_sensitivity is None
    assert experiment.completed_at is None


def test_last_review_records_lambda_c_with_the_completed_state(stack: Stack) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.POSITIVE)
    experiment = stored(stack)
    assert experiment.state is ExperimentState.COMPLETED
    assert experiment.completed_at == NOW
    conclusion = experiment.conclusion
    assert conclusion is not None
    assert conclusion.verdict is ConclusionVerdict.PASS
    assert conclusion.value == pytest.approx(0.5946035575)
    assert experiment.measured_sensitivity == pytest.approx(0.5946035575)
    assert conclusion.rule_version == conclusions.SENSITIVITY_RULE.version
    assert conclusion.labeled_sensitivity == 0.5
    assert {t.tube_no for t in conclusion.tubes} == set(range(1, 18))
    assert next(t for t in conclusion.tubes if t.tube_no == 7).reviewed
    assert any(
        "sensitivity-v1" in d and "0.5946" in d
        for d in stack.audit(AuditAction.CONCLUSION_RECORDED)
    )


def test_review_as_negative_changes_the_endpoint(stack: Stack) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.NEGATIVE)
    conclusion = stored(stack).conclusion
    assert conclusion is not None
    # 终点 0.5、0.5、1、1：antilg((2·lg0.5 + 2·lg1)/4) = sqrt(0.5)
    assert conclusion.value == pytest.approx(0.7071067812)


def test_conclusion_rolls_back_with_the_state_on_conflict(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending_sample(stack)
    calls: list[str] = []
    real_uow = stack.database.unit_of_work

    def conflicting_uow():  # type: ignore[no-untyped-def]
        context = real_uow()

        class Wrapper:
            def __enter__(self):  # type: ignore[no-untyped-def]
                uow = context.__enter__()
                # 事务开始后才发现别处改过状态：结论已经写了，也要一起回滚。
                real_save = uow.experiments.record_completion

                def fail_after(*args: object, **kwargs: object) -> None:
                    real_save(*args, **kwargs)  # type: ignore[arg-type]
                    calls.append("written")
                    raise StateConflictError("模拟冲突")

                uow.experiments.record_completion = fail_after  # type: ignore[method-assign]
                return uow

            def __exit__(self, *exc: object) -> object:
                return context.__exit__(*exc)  # type: ignore[arg-type]

        return Wrapper()

    monkeypatch.setattr(stack.database, "unit_of_work", conflicting_uow)
    with pytest.raises(ServiceError) as caught:
        review(stack, 7, Interpretation.POSITIVE)
    assert caught.value.code is ServiceErrorCode.REVIEW_CONFLICT
    assert calls == ["written"]
    monkeypatch.undo()
    experiment = stored(stack)
    assert experiment.state is ExperimentState.PENDING_REVIEW
    assert experiment.conclusion is None
    assert experiment.measured_sensitivity is None
    assert experiment.completed_at is None
    assert stack.audit(AuditAction.CONCLUSION_RECORDED) == []


def test_changing_rules_does_not_rewrite_recorded_conclusions(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.POSITIVE)
    before = stored(stack).conclusion

    # 规则和阈值都变了：判定范围收窄到不可能通过，版本号也换了。
    monkeypatch.setattr(conclusions, "LAMBDA_C_MIN_RATIO", 100.0)
    monkeypatch.setattr(conclusions, "LAMBDA_C_MAX_RATIO", 200.0)
    changed = conclusions.ConclusionRule(
        experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
        version="sensitivity-v2",
        source="新规则",
        verdict_enabled=True,
        tracked_roles=(TubeRole.TEST,),
        clauses=("新条文",),
    )
    monkeypatch.setattr(conclusions, "SENSITIVITY_RULE", changed)
    assert conclusions.judge_lambda_c(0.59, 0.5) is ConclusionVerdict.FAIL
    assert conclusions.conclude_experiment(stored(stack)).rule_version == "sensitivity-v2"

    after = stored(stack).conclusion
    assert after == before
    assert after is not None
    assert after.verdict is ConclusionVerdict.PASS
    assert after.rule_version == "sensitivity-v1"


def test_unconfirmed_template_finishes_with_a_pending_conclusion(stack: Stack) -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.LIMIT]
    draft = ExperimentDraft()
    draft.choose_template(template)
    draft.auto_select()
    draft.reagent_codes.update(GOOD_CODES)
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, draft.to_request())
    experiment = stored(stack, experiment_id)
    assert experiment.layout_confirmed is False
    for event in (
        ExperimentEvent.START,
        ExperimentEvent.ROW_DUE,
        ExperimentEvent.ROW_CAPTURED,
    ):
        experiment.apply(event)
    for assignment in experiment.assignments:
        experiment.record_result(
            TubeResult.from_analysis(
                tube_no=assignment.tube_no,
                status=TubeAnalysisStatus.OK,
                state=(
                    GelState.PARTIALLY_GELLED if assignment.tube_no == 1 else GelState.NOT_GELLED
                ),
                mapping=GelStateMapping(),
            )
        )
    experiment.apply(ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW)
    stack.database.experiments.save(experiment)
    service = ReviewService(stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=True)
    service.review_tube(
        stack.login("analyst"), experiment_id, 1, Interpretation.NEGATIVE, "人工判阴性"
    )
    done = stored(stack, experiment_id)
    assert done.state is ExperimentState.COMPLETED
    assert done.conclusion is not None
    assert done.conclusion.verdict is ConclusionVerdict.PENDING
    assert done.conclusion.value is None
    assert done.measured_sensitivity is None
    assert done.completed_at == NOW


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
    assert spikes[17] == 0.0
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
    """旧的自定义模板存的是绝对值，照原样进入试验；与 λ 不符时结论是待定，不会算错。"""
    draft_items = [
        TemplateItemInput(f"样品{i}", TubeRole.TEST, None, value)
        for i, value in enumerate([1.0, 0.5, 0.25, 0.125] * 4)
    ]
    draft_items.append(TemplateItemInput("对照", TubeRole.NEGATIVE_CONTROL, None, 0.0))
    template = stack.templates.create_template(
        stack.login("admin"),
        TemplateDraft("旧自定义", ExperimentType.SENSITIVITY_CONFIRMATION, draft_items),
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
    assert conclusions.conclude_experiment(experiment).verdict is ConclusionVerdict.PENDING


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


def test_saving_an_experiment_keeps_its_conclusion(stack: Stack) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.POSITIVE)
    experiment = stored(stack)
    stack.database.experiments.save(experiment)
    again = stored(stack)
    assert again.conclusion == experiment.conclusion
    assert again.completed_at == experiment.completed_at
    assert again.layout_confirmed is True


def test_only_a_finalized_conclusion_can_be_stored(stack: Stack) -> None:
    experiment = sensitivity_experiment()
    stack.database.experiments.save(experiment)
    unfinished = conclusions.conclude_experiment(experiment)
    with pytest.raises(ValueError):
        stack.database.experiments.record_completion(
            experiment.experiment_id,
            completed_at=NOW,
            measured_sensitivity=None,
            conclusion=unfinished,
        )


def test_summaries_carry_the_completion_time(stack: Stack) -> None:
    pending_sample(stack)
    review(stack, 7, Interpretation.POSITIVE)
    page = stack.database.experiments.query(ExperimentQuery(), PageRequest(limit=5))
    assert page.items[0].completed_at == NOW
