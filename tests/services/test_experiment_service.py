"""创建和启动试验：服务层再校验试剂、数量、权限，界面被绕过也不放行。"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

import pytest

from gelinspect.domain.entities import ReagentBatch
from gelinspect.domain.enums import AuditAction, ExperimentType, ReagentKind, TubeRole
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate
from gelinspect.interfaces.storage import AuditQuery, ExperimentQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.engine_types import EngineBusyError, EngineStateError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import (
    ExperimentDraft,
    NewExperimentRequest,
    SampleAssignmentInput,
)
from gelinspect.services.reagent_service import ReagentLockedError, ScanOutcome
from simulation.reagents import SAMPLE_CODES

from .conftest import GOOD_CODES, NOW, Stack

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
LIMIT = BUILTIN_TEMPLATES[ExperimentType.LIMIT]


def request_for(
    template: ExperimentTemplate = SENSITIVITY,
    tubes: Iterable[int] | None = None,
    codes: Mapping[ReagentKind, str] | None = None,
) -> NewExperimentRequest:
    draft = ExperimentDraft()
    draft.choose_template(template)
    draft.set_selected(tubes if tubes is not None else [i.tube_no for i in template.items])
    request = draft.to_request()
    return NewExperimentRequest(
        request.template_id, request.assignments, GOOD_CODES if codes is None else codes
    )


def experiments_in(stack: Stack) -> int:
    return stack.database.experiments.query(ExperimentQuery(), PageRequest()).total


# ---------------------------------------------------------------------------- 创建


def test_create_saves_a_ready_experiment_with_the_template_mapping(stack: Stack) -> None:
    session = stack.login("experimenter")
    request = request_for(tubes=range(10, 27))
    experiment_id = stack.experiments.create_experiment(session, request)
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None
    assert experiment.state is ExperimentState.READY
    assert experiment.experiment_type is ExperimentType.SENSITIVITY_CONFIRMATION
    assert experiment.template_id == SENSITIVITY.template_id
    assert experiment.operator == "experimenter" and experiment.instrument_no == "GI-001"
    assert experiment.created_at == NOW
    # 第 i 个样品行对应第 i 小的管号，用途和外加内毒素来自同一个模板管位项
    items = sorted(SENSITIVITY.items, key=lambda i: i.tube_no)
    assert [a.tube_no for a in experiment.assignments] == list(range(10, 27))
    for assignment, item in zip(experiment.assignments, items, strict=True):
        assert assignment.role is item.role
        assert assignment.spiked_endotoxin == item.spike_for(0.25)  # 白名单的 λ
    assert experiment.assignments[-1].role is TubeRole.NEGATIVE_CONTROL
    assert {b.kind for b in experiment.reagents} == set(ReagentKind)
    assert all(b.registered and b.expiry_date is not None for b in experiment.reagents)
    assert stack.engine.started == []  # 创建不启动


def test_create_writes_the_audit_and_check_records(stack: Stack) -> None:
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, request_for())
    page = stack.database.audit.query(AuditQuery(experiment_id=experiment_id), PageRequest())
    actions = [r.event.action for r in sorted(page.items, key=lambda r: r.seq)]
    assert actions == [
        AuditAction.EXPERIMENT_CREATED,
        AuditAction.REAGENT_CHECKED,
        AuditAction.EXPERIMENT_STATE_CHANGED,
    ]
    checks = stack.database.reagent_checks.query(PageRequest()).items
    assert {c.experiment_id for c in checks} == {experiment_id} and len(checks) == 3
    assert stack.database.audit.verify_chain().ok


def test_create_in_row_order_follows_the_domain_plate_mapping(stack: Stack) -> None:
    tubes = [3, 4, 9, 17, 18, 40, 41, 80]
    experiment_id = stack.experiments.create_experiment(
        stack.login("experimenter"), request_for(LIMIT, tubes)
    )
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None
    rows = {tube_to_position(a.tube_no).row_index for a in experiment.assignments}
    assert rows == {1, 2, 3, 5, 6, 10}


@pytest.mark.parametrize(
    ("label", "codes", "outcome"),
    [
        ("过期", {**GOOD_CODES, ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_expired"]},
         ScanOutcome.EXPIRED),
        ("未备案", {**GOOD_CODES, ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_unregistered"]},
         ScanOutcome.NOT_REGISTERED),
        ("种类放错", {**GOOD_CODES, ReagentKind.WATER: SAMPLE_CODES["lal_valid"]},
         ScanOutcome.KIND_MISMATCH),
        ("读不出", {**GOOD_CODES, ReagentKind.ENDOTOXIN: "garbage"}, ScanOutcome.UNREADABLE),
        ("缺一种", {k: v for k, v in GOOD_CODES.items() if k is not ReagentKind.WATER},
         ScanOutcome.NOT_CONFIGURED),
        ("全都没有", {}, ScanOutcome.NOT_CONFIGURED),
    ],
)  # fmt: skip
def test_create_refuses_when_reagents_do_not_pass_even_if_the_ui_was_bypassed(
    stack: Stack, label: str, codes: dict[ReagentKind, str], outcome: ScanOutcome
) -> None:
    before = len(stack.audit())
    with pytest.raises(ReagentLockedError) as caught:
        stack.experiments.create_experiment(stack.login("experimenter"), request_for(codes=codes))
    assert outcome in {scan.outcome for scan in caught.value.scans.values()}, label
    assert caught.value.code is ServiceErrorCode.REAGENT_LOCKED
    assert experiments_in(stack) == 0  # 没有保存任何试验
    assert stack.engine.started == []
    new = stack.audit()[before:]
    assert any("流程锁定" in d for d in new), label  # 锁定写审计


def test_the_ui_cannot_smuggle_a_passed_flag_only_raw_codes_count(stack: Stack) -> None:
    """请求里只有扫码原文：服务层自己解析、查备案、按今天的日期判断。"""
    request = request_for(
        codes={**GOOD_CODES, ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_expired"]}
    )
    assert "passed" not in NewExperimentRequest.__dataclass_fields__
    with pytest.raises(ReagentLockedError):
        stack.experiments.create_experiment(stack.login("experimenter"), request)


@pytest.mark.parametrize(
    ("tubes", "code"),
    [
        (range(1, 17), ServiceErrorCode.DRAFT_COUNT_MISMATCH),  # 少一个
        (range(1, 19), ServiceErrorCode.DRAFT_COUNT_MISMATCH),  # 多一个
        ([], ServiceErrorCode.DRAFT_COUNT_MISMATCH),
    ],
)
def test_create_refuses_a_tube_count_that_differs_from_the_template(
    stack: Stack, tubes: object, code: ServiceErrorCode
) -> None:
    draft = ExperimentDraft()
    draft.choose_template(SENSITIVITY)
    draft.set_selected(tubes)  # type: ignore[arg-type]
    request = NewExperimentRequest(SENSITIVITY.template_id, [
        SampleAssignmentInput(t, f"样品{t}") for t in draft.selected
    ], GOOD_CODES)  # fmt: skip
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(stack.login("experimenter"), request)
    assert caught.value.code is code
    assert experiments_in(stack) == 0


def test_create_refuses_duplicate_out_of_range_and_blank(stack: Stack) -> None:
    session = stack.login("experimenter")
    base = [SampleAssignmentInput(t, f"样品{t}") for t in range(1, 9)]

    def attempt(assignments: list[SampleAssignmentInput]) -> ServiceErrorCode:
        with pytest.raises(ServiceError) as caught:
            stack.experiments.create_experiment(
                session, NewExperimentRequest(LIMIT.template_id, assignments, GOOD_CODES)
            )
        return caught.value.code

    assert (
        attempt([*base[:7], SampleAssignmentInput(7, "重复")])
        is ServiceErrorCode.DRAFT_TUBES_INVALID
    )
    assert (
        attempt([*base[:7], SampleAssignmentInput(81, "越界")])
        is ServiceErrorCode.DRAFT_TUBES_INVALID
    )
    assert attempt([*base[:7], SampleAssignmentInput(8, "  ")]) is (
        ServiceErrorCode.DRAFT_SAMPLE_NAME_REQUIRED
    )
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(
            session, NewExperimentRequest("custom.nope", base, GOOD_CODES)
        )
    assert caught.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND
    assert experiments_in(stack) == 0


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_only_the_experimenter_can_create(stack: Stack, role: str) -> None:
    with pytest.raises(PermissionDeniedError):
        stack.experiments.create_experiment(stack.login(role), request_for())
    assert experiments_in(stack) == 0
    assert any("拒绝 experiment_create" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))
    with pytest.raises(PermissionDeniedError):
        stack.experiments.create_experiment(None, request_for())


def test_a_locked_screen_cannot_create(stack: Stack) -> None:
    session = stack.login("experimenter")
    stack.auth.lock(session)
    with pytest.raises(PermissionDeniedError):
        stack.experiments.create_experiment(session, request_for())


# ---------------------------------------------------------------------------- 启动


def test_start_hands_the_ready_experiment_to_the_engine(stack: Stack) -> None:
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, request_for())
    stack.experiments.start_experiment(session, experiment_id)
    assert stack.engine.started == [(experiment_id, "experimenter")]


def save_ready(stack: Stack, reagents: tuple[ReagentBatch, ...], experiment_id: str = "E-X") -> str:
    """绕过创建服务，直接把一个「就绪」的试验写进库（模拟被篡改或隔夜过期）。"""
    from tests.services.engine_helpers import ready_experiment

    experiment = ready_experiment(range(1, 9), experiment_id)
    experiment = dataclasses.replace(experiment, reagents=reagents)
    stack.database.experiments.save(experiment)
    return experiment_id


def good_batches(expiry: date) -> tuple[ReagentBatch, ...]:
    return (
        ReagentBatch(ReagentKind.LAL_REAGENT, "L1", expiry, True, 0.25),
        ReagentBatch(ReagentKind.ENDOTOXIN, "C1", expiry, True),
        ReagentBatch(ReagentKind.WATER, "W1", expiry, True),
    )


def test_start_refuses_an_experiment_whose_reagents_expired_overnight(stack: Stack) -> None:
    experiment_id = save_ready(stack, good_batches(NOW.date() - timedelta(days=1)))
    with pytest.raises(ReagentLockedError) as caught:
        stack.experiments.start_experiment(stack.login("experimenter"), experiment_id)
    assert {s.outcome for s in caught.value.scans.values()} == {ScanOutcome.EXPIRED}
    assert stack.engine.started == []
    stored = stack.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.REAGENT_LOCKED
    assert any("启动前复核未通过" in d for d in stack.audit(AuditAction.REAGENT_CHECKED))
    # 已锁定的试验不能再启动
    with pytest.raises(ServiceError) as again:
        stack.experiments.start_experiment(stack.login("experimenter"), experiment_id)
    assert again.value.code is ServiceErrorCode.EXPERIMENT_NOT_READY


def test_start_refuses_unregistered_batches_in_the_stored_experiment(stack: Stack) -> None:
    batches = list(good_batches(NOW.date() + timedelta(days=100)))
    batches[1] = ReagentBatch(ReagentKind.ENDOTOXIN, "C1", batches[1].expiry_date, False)
    experiment_id = save_ready(stack, tuple(batches))
    with pytest.raises(ReagentLockedError) as caught:
        stack.experiments.start_experiment(stack.login("experimenter"), experiment_id)
    assert caught.value.scans[ReagentKind.ENDOTOXIN].outcome is ScanOutcome.NOT_REGISTERED
    assert stack.engine.started == []


def test_start_refuses_a_ready_experiment_with_missing_reagents(stack: Stack) -> None:
    experiment_id = save_ready(stack, good_batches(NOW.date() + timedelta(days=100))[:2])
    with pytest.raises(ReagentLockedError):
        stack.experiments.start_experiment(stack.login("experimenter"), experiment_id)
    assert stack.engine.started == []


def test_start_requires_a_ready_existing_experiment(stack: Stack) -> None:
    session = stack.login("experimenter")
    with pytest.raises(ServiceError) as missing:
        stack.experiments.start_experiment(session, "E-NOPE")
    assert missing.value.code is ServiceErrorCode.EXPERIMENT_NOT_FOUND
    experiment_id = save_ready(stack, good_batches(NOW.date() + timedelta(days=100)))
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None
    experiment.apply(ExperimentEvent.STOP)
    stack.database.experiments.save(experiment)
    with pytest.raises(ServiceError) as stopped:
        stack.experiments.start_experiment(session, experiment_id)
    assert stopped.value.code is ServiceErrorCode.EXPERIMENT_NOT_READY


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_only_the_experimenter_can_start(stack: Stack, role: str) -> None:
    experiment_id = stack.experiments.create_experiment(stack.login("experimenter"), request_for())
    with pytest.raises(PermissionDeniedError):
        stack.experiments.start_experiment(stack.login(role), experiment_id)
    assert stack.engine.started == []
    assert any("拒绝 experiment_run" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))


def test_engine_refusals_become_service_errors(stack: Stack) -> None:
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, request_for())
    stack.engine.fail_with = EngineBusyError("试验 E1 正在运行")
    with pytest.raises(ServiceError) as busy:
        stack.experiments.start_experiment(session, experiment_id)
    assert busy.value.code is ServiceErrorCode.ENGINE_BUSY
    stack.engine.fail_with = EngineStateError("不是就绪")
    with pytest.raises(ServiceError) as state:
        stack.experiments.start_experiment(session, experiment_id)
    assert state.value.code is ServiceErrorCode.EXPERIMENT_NOT_READY


# ---------------------------------------------------------------------------- 放弃


def test_abandon_stops_a_ready_experiment_that_was_never_started(stack: Stack) -> None:
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, request_for())
    stack.experiments.abandon_experiment(session, experiment_id)
    stored = stack.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.STOPPED
    assert any("启动前放弃" in d for d in stack.audit(AuditAction.EXPERIMENT_STATE_CHANGED))
    with pytest.raises(ServiceError):
        stack.experiments.abandon_experiment(session, experiment_id)  # 已经不是就绪
    with pytest.raises(PermissionDeniedError):
        stack.experiments.abandon_experiment(stack.login("analyst"), experiment_id)
