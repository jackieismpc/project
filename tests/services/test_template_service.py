"""检测模板：内置只读可复制，自定义可新建和编辑，权限和审计。"""

from __future__ import annotations

import pytest

from gelinspect.domain.enums import AuditAction, ExperimentType, TubeRole
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.template_service import (
    TemplateDraft,
    TemplateItemInput,
    draft_of,
)

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
