"""创建和启动试验：服务层再校验试剂、数量、权限，界面被绕过也不放行。"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

import pytest

from gelinspect.domain.entities import ReagentBatch
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    PermissionGroup,
    ReagentKind,
    TubeRole,
)
from gelinspect.domain.plate import TUBE_COUNT, tube_to_position
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate
from gelinspect.interfaces.storage import AuditQuery, ExperimentQuery, PageRequest
from gelinspect.interfaces.traceability import ReagentInput
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.engine_types import EngineBusyError, EngineStateError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import (
    ExperimentDraft,
    NewExperimentRequest,
    SampleAssignmentInput,
)
from gelinspect.services.reagent_service import ReagentLockedError, ScanOutcome
from gelinspect.services.template_service import TemplateDraft, TemplateItemInput
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
        request.template_id,
        request.assignments,
        GOOD_CODES if codes is None else codes,
        template_snapshot=request.template_snapshot,
    )


def experiments_in(stack: Stack) -> int:
    return stack.database.experiments.query(ExperimentQuery(), PageRequest()).total


# ---------------------------------------------------------------------------- 创建


def test_create_saves_a_ready_experiment_with_the_template_mapping(stack: Stack) -> None:
    session = stack.login("experimenter")
    request = request_for(tubes=range(10, 28))
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
    assert [a.tube_no for a in experiment.assignments] == list(range(10, 28))
    for assignment, item in zip(experiment.assignments, items, strict=True):
        assert assignment.role is item.role
        assert assignment.spiked_endotoxin == item.spike_for(0.25)  # 白名单的 λ
    assert experiment.assignments[-1].role is TubeRole.NEGATIVE_CONTROL
    assert {b.kind for b in experiment.reagents} == set(ReagentKind)
    assert all(b.registered and b.expiry_date is not None for b in experiment.reagents)
    assert stack.engine.started == []  # 创建不启动


def test_manual_reagent_input_creates_and_starts_after_server_recheck(stack: Stack) -> None:
    request = request_for(
        codes={
            ReagentKind.ENDOTOXIN: GOOD_CODES[ReagentKind.ENDOTOXIN],
            ReagentKind.WATER: GOOD_CODES[ReagentKind.WATER],
        }
    )
    request = dataclasses.replace(
        request,
        reagent_inputs={
            ReagentKind.LAL_REAGENT: ReagentInput(
                mode="manual", product_code="GL-LAL-0.25", batch_no="LAL2606A"
            )
        },
    )
    session = stack.login("experimenter")
    experiment_id = stack.experiments.create_experiment(session, request)
    saved = stack.database.experiments.get(experiment_id)
    assert saved is not None
    lal = next(batch for batch in saved.reagents if batch.kind is ReagentKind.LAL_REAGENT)
    assert lal.batch_no == "LAL2606A" and lal.labeled_sensitivity == 0.25
    stack.experiments.start_experiment(session, experiment_id)
    assert stack.engine.started == [(experiment_id, "experimenter")]


def test_only_template_manager_can_retain_deleted_template_snapshot(stack: Stack) -> None:
    admin = stack.login("admin")
    template = stack.templates.create_template(
        admin,
        TemplateDraft(
            "草稿模板",
            ExperimentType.LIMIT,
            [TemplateItemInput("供试品"), TemplateItemInput("阴性对照", TubeRole.NEGATIVE_CONTROL)],
        ),
    )
    request = request_for(template)
    stack.templates.delete_template(admin, template.template_id)
    forged = dataclasses.replace(request, template_snapshot=SENSITIVITY)
    with pytest.raises(ServiceError) as mismatched:
        stack.experiments.create_experiment(admin, forged)
    assert mismatched.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(stack.login("experimenter"), request)
    assert caught.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND
    experiment_id = stack.experiments.create_experiment(admin, request)
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None and experiment.template_id == template.template_id
    assert experiment.experiment_type is ExperimentType.LIMIT
    assert len(experiment.assignments) == 2
    assert experiment.assignments[1].role is TubeRole.NEGATIVE_CONTROL


def test_ad_hoc_configuration_runs_without_persisting_a_template(stack: Stack) -> None:
    source = request_for()
    snapshot = dataclasses.replace(source.template_snapshot, template_id="draft.one")
    request = dataclasses.replace(
        source, template_id="draft.one", template_snapshot=snapshot, source_template_id=None
    )
    experiment_id = stack.experiments.create_experiment(stack.login("experimenter"), request)
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None
    assert experiment.template_id == "draft.one"
    assert len(experiment.assignments) == 18
    assert stack.database.templates.get("draft.one") is None


def test_ad_hoc_configuration_accepts_exactly_96_tubes(stack: Stack) -> None:
    template = BUILTIN_TEMPLATES[ExperimentType.GENERAL]
    source = request_for(template)
    snapshot = dataclasses.replace(template, template_id="draft.ninety-six")
    request = dataclasses.replace(
        source, template_id=snapshot.template_id, template_snapshot=snapshot
    )
    experiment_id = stack.experiments.create_experiment(stack.login("experimenter"), request)
    experiment = stack.database.experiments.get(experiment_id)
    assert experiment is not None and len(experiment.assignments) == TUBE_COUNT


def test_ad_hoc_configuration_rejects_non_contiguous_tube_configuration(stack: Stack) -> None:
    template = ExperimentTemplate(
        "draft.gapped",
        "间隔管位",
        ExperimentType.LIMIT,
        (
            dataclasses.replace(LIMIT.items[0], tube_no=1),
            dataclasses.replace(LIMIT.items[1], tube_no=3),
        ),
    )
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(stack.login("experimenter"), request_for(template))
    assert caught.value.code is ServiceErrorCode.DRAFT_TUBES_INVALID


def test_ad_hoc_configuration_keeps_source_revocation_guard(stack: Stack) -> None:
    admin = stack.login("admin")
    source_template = stack.templates.create_template(
        admin,
        TemplateDraft("來源模板", ExperimentType.LIMIT, [TemplateItemInput("供試品")]),
    )
    source = request_for(source_template)
    snapshot = dataclasses.replace(source.template_snapshot, template_id="draft.two")
    request = dataclasses.replace(
        source,
        template_id="draft.two",
        template_snapshot=snapshot,
        source_template_id=source_template.template_id,
    )
    stack.templates.delete_template(admin, source_template.template_id)
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(stack.login("experimenter"), request)
    assert caught.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND
    assert stack.experiments.create_experiment(admin, request)


def test_snapshot_reuse_rechecks_revoked_template_permission(stack: Stack) -> None:
    admin = stack.login("admin")
    template = stack.templates.create_template(
        admin, TemplateDraft("草稿模板", ExperimentType.LIMIT, [TemplateItemInput("供试品")])
    )
    stack.templates.delete_template(admin, template.template_id)
    session = stack.login("experimenter")
    user = stack.database.users.get(session.user.user_id)
    assert user is not None
    manager = dataclasses.replace(
        user, permission_groups=frozenset({PermissionGroup.EXPERIMENT, PermissionGroup.TEMPLATE})
    )
    stack.database.users.update(manager)
    stack.auth.refresh_sessions(manager)
    request = request_for(template)
    # 权限在会话打开后被撤销；authorize 必须用新权限刷新会话。
    revoked = dataclasses.replace(
        manager, permission_groups=frozenset({PermissionGroup.EXPERIMENT})
    )
    stack.database.users.update(revoked)
    stack.auth.refresh_sessions(revoked)
    with pytest.raises(ServiceError) as caught:
        stack.experiments.create_experiment(session, request)
    assert caught.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND


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
        (range(1, 18), ServiceErrorCode.DRAFT_COUNT_MISMATCH),  # 少一个
        (range(1, 20), ServiceErrorCode.DRAFT_COUNT_MISMATCH),  # 多一个
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
        attempt([*base[:7], SampleAssignmentInput(TUBE_COUNT + 1, "越界")])
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


def test_user_without_experiment_group_cannot_create(stack: Stack) -> None:
    from gelinspect.domain.enums import PermissionGroup
    from gelinspect.services.user_service import UserService

    admin = stack.login("admin")
    analyst = stack.database.users.get_by_username("analyst")
    assert analyst is not None
    UserService(stack.auth, stack.database).set_permission_groups(
        admin, analyst.user_id, frozenset({PermissionGroup.VIEW})
    )
    with pytest.raises(PermissionDeniedError):
        stack.experiments.create_experiment(stack.login("analyst"), request_for())
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
    """绕过创建服务，直接把一个“就绪”的试验写进库（模拟被篡改或隔夜过期）。"""
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


def test_user_without_experiment_group_cannot_start(stack: Stack) -> None:
    from gelinspect.domain.enums import PermissionGroup

    admin = stack.login("admin")
    user = stack.database.users.get_by_username("analyst")
    assert user is not None
    from gelinspect.services.user_service import UserService

    UserService(stack.auth, stack.database).set_permission_groups(
        admin, user.user_id, frozenset({PermissionGroup.VIEW})
    )
    experiment_id = stack.experiments.create_experiment(stack.login("experimenter"), request_for())
    with pytest.raises(PermissionDeniedError):
        stack.experiments.start_experiment(stack.login("analyst"), experiment_id)
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
    from gelinspect.domain.enums import PermissionGroup
    from gelinspect.services.user_service import UserService

    admin = stack.login("admin")
    analyst = stack.database.users.get_by_username("analyst")
    assert analyst is not None
    UserService(stack.auth, stack.database).set_permission_groups(
        admin, analyst.user_id, frozenset({PermissionGroup.VIEW})
    )
    with pytest.raises(PermissionDeniedError):
        stack.experiments.abandon_experiment(stack.login("analyst"), experiment_id)
