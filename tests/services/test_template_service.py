"""检测模板：内置只读可复制，自定义可新建和编辑，权限和审计。"""

from __future__ import annotations

import pytest

from gelinspect.domain.enums import AuditAction, ExperimentType, TubeRole
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate, TemplateTubeItem
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.template_service import (
    TemplateDraft,
    TemplateItemInput,
    draft_of,
)
from tests.services.engine_helpers import ready_experiment

from .conftest import Stack

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]


def custom_draft(name: str = "我的模板", count: int = 3) -> TemplateDraft:
    return TemplateDraft(
        name=name,
        experiment_type=ExperimentType.LIMIT,
        items=[
            TemplateItemInput(
                f"样品{i}", TubeRole.TEST, dilution_factor=2.0 * i, spiked_endotoxin=0.5
            )
            for i in range(1, count + 1)
        ],
    )


def test_list_shows_builtin_first_in_type_order_then_custom(stack: Stack) -> None:
    stack.templates.create_template(stack.login("admin"), custom_draft("乙"))
    stack.templates.create_template(stack.login("admin"), custom_draft("甲"))
    infos = stack.templates.list_templates(stack.login("analyst"))  # 所有角色可查看
    assert [i.builtin for i in infos] == [True] * 6 + [False] * 2
    types = [i.template.experiment_type for i in infos[:6]]
    assert types == [
        ExperimentType.SENSITIVITY_CONFIRMATION,
        ExperimentType.INTERFERENCE,
        ExperimentType.SEMI_QUANTITATIVE,
        ExperimentType.LIMIT,
        ExperimentType.LIMIT,
        ExperimentType.GENERAL,
    ]
    assert [i.template.name for i in infos[6:]] == sorted(["乙", "甲"])  # 自定义的按名称排


def test_builtin_templates_are_read_only_but_can_be_copied(stack: Stack) -> None:
    admin = stack.login("admin")
    with pytest.raises(ServiceError) as caught:
        stack.templates.update_template(admin, SENSITIVITY.template_id, custom_draft())
    assert caught.value.code is ServiceErrorCode.TEMPLATE_READ_ONLY
    unchanged = stack.templates.get_template(admin, SENSITIVITY.template_id)
    assert unchanged.template == SENSITIVITY and unchanged.builtin

    copy = stack.templates.copy_template(admin, SENSITIVITY.template_id)
    assert copy.name == "灵敏度复核 (2λ:1) 副本"
    assert copy.template_id.startswith("custom.") and copy.tube_count == 18
    assert [i.spiked_endotoxin for i in sorted(copy.items, key=lambda i: i.tube_no)] == [
        i.spiked_endotoxin for i in sorted(SENSITIVITY.items, key=lambda i: i.tube_no)
    ]
    info = stack.templates.get_template(admin, copy.template_id)
    assert not info.builtin
    # 复制品可以编辑，源模板不受影响
    edited = stack.templates.update_template(
        admin,
        copy.template_id,
        TemplateDraft("改过的副本", copy.experiment_type, draft_of(copy).items[:5]),
    )
    assert edited.tube_count == 5 and edited.name == "改过的副本"
    assert stack.templates.get_template(admin, SENSITIVITY.template_id).template == SENSITIVITY


def test_copy_of_a_builtin_layout_keeps_roles_and_values(stack: Stack) -> None:
    source = BUILTIN_TEMPLATES[ExperimentType.INTERFERENCE]
    copy = stack.templates.copy_template(stack.login("admin"), source.template_id)
    assert not copy.needs_confirmation and copy.tube_count == 28
    assert [i.role for i in copy.items] == [i.role for i in source.items]
    assert [i.lambda_multiple for i in copy.items] == [i.lambda_multiple for i in source.items]


def test_copy_names_do_not_collide(stack: Stack) -> None:
    admin = stack.login("admin")
    first = stack.templates.copy_template(admin, SENSITIVITY.template_id)
    second = stack.templates.copy_template(admin, SENSITIVITY.template_id)
    assert first.name != second.name and second.name.endswith("副本 2")


def test_create_and_edit_a_custom_template(stack: Stack) -> None:
    admin = stack.login("admin")
    created = stack.templates.create_template(admin, custom_draft())
    assert created.tube_count == 3
    assert [i.tube_no for i in created.items] == [1, 2, 3]  # 管号按顺序从 1 编
    assert [i.dilution_factor for i in created.items] == [2.0, 4.0, 6.0]
    loaded = stack.templates.get_template(admin, created.template_id)
    assert loaded.template == created and not loaded.builtin
    updated = stack.templates.update_template(
        admin,
        created.template_id,
        TemplateDraft(
            "改名了",
            ExperimentType.LIMIT,
            [TemplateItemInput("阴性对照", TubeRole.NEGATIVE_CONTROL)],
        ),
    )
    assert updated.template_id == created.template_id
    assert updated.name == "改名了" and updated.experiment_type is ExperimentType.LIMIT
    assert updated.items[0].role is TubeRole.NEGATIVE_CONTROL
    assert updated.items[0].dilution_factor is None


@pytest.mark.parametrize(
    ("draft", "code"),
    [
        (TemplateDraft("  ", ExperimentType.LIMIT, [TemplateItemInput("a")]),
         ServiceErrorCode.TEMPLATE_NAME_REQUIRED),
        (TemplateDraft("x" * 61, ExperimentType.LIMIT, [TemplateItemInput("a")]),
         ServiceErrorCode.TEMPLATE_NAME_TOO_LONG),
        (TemplateDraft("空", ExperimentType.LIMIT, []), ServiceErrorCode.TEMPLATE_TUBE_COUNT),
        (TemplateDraft("太多", ExperimentType.LIMIT, [TemplateItemInput("a")] * 97),
         ServiceErrorCode.TEMPLATE_TUBE_COUNT),
        (TemplateDraft(
            "缺名", ExperimentType.LIMIT, [TemplateItemInput("a"), TemplateItemInput(" ")]
         ), ServiceErrorCode.TEMPLATE_SAMPLE_NAME_REQUIRED),
        (TemplateDraft("负数", ExperimentType.LIMIT, [TemplateItemInput("a", dilution_factor=-1)]),
         ServiceErrorCode.TEMPLATE_VALUE_INVALID),
    ],
)  # fmt: skip
def test_invalid_templates_are_rejected_and_nothing_is_saved(
    stack: Stack, draft: TemplateDraft, code: ServiceErrorCode
) -> None:
    with pytest.raises(ServiceError) as caught:
        stack.templates.create_template(stack.login("admin"), draft)
    assert caught.value.code is code
    assert len(stack.templates.list_templates(stack.login("admin"))) == 6


def test_duplicate_names_are_rejected(stack: Stack) -> None:
    admin = stack.login("admin")
    with pytest.raises(ServiceError) as caught:
        stack.templates.create_template(admin, custom_draft("干扰试验"))  # 与内置同名
    assert caught.value.code is ServiceErrorCode.TEMPLATE_NAME_DUPLICATE
    first = stack.templates.create_template(admin, custom_draft("A"))
    stack.templates.update_template(
        admin, first.template_id, custom_draft("A")
    )  # 自己的名字可以保留
    other = stack.templates.create_template(admin, custom_draft("B"))
    with pytest.raises(ServiceError):
        stack.templates.update_template(admin, other.template_id, custom_draft("A"))


def test_unknown_template_is_reported(stack: Stack) -> None:
    admin = stack.login("admin")
    for call in (
        lambda: stack.templates.get_template(admin, "custom.nope"),
        lambda: stack.templates.copy_template(admin, "custom.nope"),
        lambda: stack.templates.update_template(admin, "custom.nope", custom_draft()),
    ):
        with pytest.raises(ServiceError) as caught:
            call()
        assert caught.value.code is ServiceErrorCode.TEMPLATE_NOT_FOUND


@pytest.mark.parametrize("role", ["experimenter", "analyst"])
def test_only_the_admin_can_manage_templates(stack: Stack, role: str) -> None:
    session = stack.login(role)
    stack.templates.list_templates(session)  # 可以查看
    with pytest.raises(PermissionDeniedError):
        stack.templates.create_template(session, custom_draft())
    with pytest.raises(PermissionDeniedError):
        stack.templates.copy_template(session, SENSITIVITY.template_id)
    created = stack.templates.create_template(stack.login("admin"), custom_draft())
    with pytest.raises(PermissionDeniedError):
        stack.templates.update_template(session, created.template_id, custom_draft("改"))
    assert len(stack.templates.list_templates(session)) == 7
    assert any("拒绝 template_manage" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))


def test_forged_and_missing_sessions_are_rejected(stack: Stack) -> None:
    with pytest.raises(PermissionDeniedError):
        stack.templates.list_templates(None)
    with pytest.raises(PermissionDeniedError):
        stack.templates.create_template(None, custom_draft())


def test_changes_are_audited_in_the_same_transaction(stack: Stack) -> None:
    admin = stack.login("admin")
    created = stack.templates.create_template(admin, custom_draft("审计模板"))
    stack.templates.copy_template(admin, created.template_id)
    stack.templates.update_template(admin, created.template_id, custom_draft("审计模板2"))
    details = stack.audit(AuditAction.TEMPLATE_CHANGED)
    assert any("新建模板 审计模板" in d and created.template_id in d for d in details)
    assert any("复制模板 审计模板 为 审计模板 副本" in d for d in details)
    assert any("编辑模板 审计模板2" in d for d in details)
    assert stack.database.audit.verify_chain().ok
    page = stack.database.audit.query(
        AuditQuery(action=AuditAction.TEMPLATE_CHANGED, actor="admin"), PageRequest()
    )
    assert page.total == 3 and all(r.event.actor_role is not None for r in page.items)


def test_builtin_can_be_hidden_and_restored_only_by_template_managers(stack: Stack) -> None:
    admin = stack.login("admin")
    analyst = stack.login("analyst")
    with pytest.raises(PermissionDeniedError):
        stack.templates.list_templates(analyst, include_hidden=True)
    audit_count = len(stack.audit(AuditAction.TEMPLATE_CHANGED))
    stack.templates.set_builtin_hidden(admin, SENSITIVITY.template_id, hidden=True)
    assert all(
        i.template.template_id != SENSITIVITY.template_id
        for i in stack.templates.list_templates(admin)
    )
    assert [t.template_id for t in stack.templates.list_hidden_builtins(admin)] == [
        SENSITIVITY.template_id
    ]
    with pytest.raises(ServiceError):
        stack.templates.get_template(admin, SENSITIVITY.template_id)
    with pytest.raises(ServiceError):
        stack.templates.copy_template(admin, SENSITIVITY.template_id)
    stack.templates.set_builtin_hidden(admin, SENSITIVITY.template_id, hidden=False)
    assert stack.templates.get_template(admin, SENSITIVITY.template_id).template == SENSITIVITY
    assert len(stack.audit(AuditAction.TEMPLATE_CHANGED)) == audit_count + 2


def test_custom_delete_is_soft_and_audited_without_changing_experiment_snapshot(
    stack: Stack,
) -> None:
    admin = stack.login("admin")
    created = stack.templates.create_template(admin, custom_draft("待删除"))
    stack.templates.delete_template(admin, created.template_id)
    with pytest.raises(ServiceError):
        stack.templates.get_template(admin, created.template_id)
    with pytest.raises(ServiceError):
        stack.templates.copy_template(admin, created.template_id)
    assert all(
        i.template.template_id != created.template_id for i in stack.templates.list_templates(admin)
    )
    assert stack.database.templates.is_deleted(created.template_id)
    assert any("删除自建模板 待删除" in d for d in stack.audit(AuditAction.TEMPLATE_CHANGED))


def test_builtin_cannot_be_soft_deleted(stack: Stack) -> None:
    with pytest.raises(ServiceError) as caught:
        stack.templates.delete_template(stack.login("admin"), SENSITIVITY.template_id)
    assert caught.value.code is ServiceErrorCode.TEMPLATE_READ_ONLY


def test_experiment_template_draft_uses_persisted_snapshot_not_current_template(
    stack: Stack,
) -> None:
    experiment = ready_experiment([17, 25], "EXP-SNAPSHOT")
    experiment.template_snapshot = ExperimentTemplate(
        "builtin.limit",
        "原试验配置",
        ExperimentType.LIMIT,
        (
            TemplateTubeItem(17, "本次供试品", TubeRole.SAMPLE, 4.0, lambda_multiple=0.5),
            TemplateTubeItem(25, "本次阴性", TubeRole.NEGATIVE_CONTROL, lambda_multiple=0.0),
        ),
    )
    stack.database.experiments.save(experiment)
    draft = stack.templates.template_draft_from_experiment(
        stack.login("admin"), experiment.experiment_id
    )
    assert draft.name == "原试验配置"
    assert tuple(draft.tube_numbers or ()) == (17, 25)
    assert [item.lambda_multiple for item in draft.items] == [0.5, 0.0]


def test_experiment_without_template_snapshot_cannot_be_saved_as_template(
    stack: Stack,
) -> None:
    experiment = ready_experiment([17, 25], "EXP-LEGACY-SNAPSHOT")
    stack.database.experiments.save(experiment)
    with pytest.raises(ServiceError) as caught:
        stack.templates.template_draft_from_experiment(
            stack.login("admin"), experiment.experiment_id
        )
    assert caught.value.code is ServiceErrorCode.EXPERIMENT_TEMPLATE_SNAPSHOT_MISSING


def test_experiment_template_draft_requires_template_manager(stack: Stack) -> None:
    experiment = ready_experiment([1, 2], "EXP-SNAPSHOT-PERM")
    stack.database.experiments.save(experiment)
    with pytest.raises(PermissionDeniedError):
        stack.templates.template_draft_from_experiment(
            stack.login("analyst"), experiment.experiment_id
        )


@pytest.mark.parametrize("numbers", [[True], ["4"], [0], [97], [1, 1]])
def test_custom_template_tube_numbers_are_validated(stack: Stack, numbers: list[object]) -> None:
    draft = TemplateDraft(
        "坏管位",
        ExperimentType.GENERAL,
        [TemplateItemInput("样品") for _ in range(len(numbers))],
        tube_numbers=numbers,  # type: ignore[arg-type]
    )
    with pytest.raises(ServiceError) as caught:
        stack.templates.create_template(stack.login("admin"), draft)
    assert caught.value.code is ServiceErrorCode.TEMPLATE_VALUE_INVALID


def test_custom_template_preserves_sparse_tube_numbers(stack: Stack) -> None:
    created = stack.templates.create_template(
        stack.login("admin"),
        TemplateDraft(
            "稀疏布局",
            ExperimentType.GENERAL,
            [TemplateItemInput("甲"), TemplateItemInput("乙")],
            tube_numbers=(17, 25),
        ),
    )
    assert [item.tube_no for item in created.items] == [17, 25]
    copied = stack.templates.copy_template(stack.login("admin"), created.template_id)
    assert [item.tube_no for item in copied.items] == [17, 25]
