"""用户与权限管理：新建、停用、重置密码、改角色；最后一个管理员和自己的保护；会话立即生效。"""

from __future__ import annotations

import pytest

from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission, PermissionGroup
from gelinspect.services.auth import (
    AccountDisabledError,
    InvalidCredentialsError,
    PasswordPolicyError,
    PermissionDeniedError,
)
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.user_service import UserService, generate_temporary_password

from .conftest import NOW, PASSWORDS, Stack


@pytest.fixture
def users(stack: Stack) -> UserService:
    return UserService(stack.auth, stack.database, clock=lambda: NOW)


def code_of(call: object) -> ServiceErrorCode:
    with pytest.raises(ServiceError) as caught:
        call()  # type: ignore[operator]
    return caught.value.code


def test_only_admins_can_manage_users(stack: Stack, users: UserService) -> None:
    for name in ("experimenter", "analyst"):
        session = stack.login(name)
        with pytest.raises(PermissionDeniedError):
            users.list_users(session)
        with pytest.raises(PermissionDeniedError):
            users.create_user(session, username="x1", display_name="X", role=Role.USER)
    assert any("user_manage" in line for line in stack.audit(AuditAction.PERMISSION_DENIED))
    with pytest.raises(PermissionDeniedError):
        users.list_users(None)


def test_internal_account_is_hidden_and_indistinguishable_from_missing_target(
    stack: Stack, users: UserService
) -> None:
    from gelinspect.domain.entities import User

    stack.database.users.add(
        User("internal-id", "maintenance", "维护账号", Role.DEVELOPER),
        password_hash=stack.auth.hash_password("Internal-Test1"),
        must_change_password=False,
    )
    admin = stack.login("admin")
    assert "maintenance" not in {record.user.username for record in users.list_users(admin)}

    for target_id in ("internal-id", "missing-id"):
        with pytest.raises(ServiceError) as caught:
            users.reset_password(admin, target_id)
        assert caught.value.code is ServiceErrorCode.USER_NOT_FOUND
        assert str(caught.value) == "用户不存在或不可操作"

    with pytest.raises(ServiceError) as caught:
        users.create_user(admin, username="maintenance", display_name="x", role=Role.USER)
    assert caught.value.code is ServiceErrorCode.USER_NOT_FOUND
    assert str(caught.value) == "登录名不可用"

    for target_id in ("internal-id", "missing-id"):
        with pytest.raises(ServiceError) as caught:
            users.change_role(admin, target_id, Role.USER)
        assert caught.value.code is ServiceErrorCode.USER_PROTECTED
        assert str(caught.value) == "无权调整该账号类别"
        assert "开发人员" not in str(caught.value)


def test_create_user_issues_a_temporary_password_and_forces_a_change(
    stack: Stack, users: UserService
) -> None:
    admin = stack.login("admin")
    issued = users.create_user(admin, username="newbie", display_name="新同事", role=Role.USER)
    assert issued.user.role is Role.USER and issued.user.active
    assert len(issued.temporary_password) >= 8
    listed = {r.user.username: r for r in users.list_users(admin)}
    assert listed["newbie"].must_change_password
    assert not listed["admin"].must_change_password
    # 用临时密码登录：必须先改密，改密前什么都做不了
    session = stack.auth.login("newbie", issued.temporary_password)
    assert session.must_change_password
    with pytest.raises(PermissionDeniedError):
        stack.auth.authorize(session, Permission.EXPERIMENT_VIEW)
    stack.auth.change_password(session, issued.temporary_password, "Brand-new-pass1")
    assert not session.permissions()
    users.set_permission_groups(admin, session.user.user_id, frozenset({PermissionGroup.VIEW}))
    stack.auth.authorize(session, Permission.EXPERIMENT_VIEW)
    # 审计记了新建，但没有密码
    text = "\n".join(stack.audit(AuditAction.USER_CHANGED))
    assert "新建用户 newbie" in text and issued.temporary_password not in text


def test_create_user_validates_and_rejects_duplicates(stack: Stack, users: UserService) -> None:
    admin = stack.login("admin")
    with pytest.raises(PasswordPolicyError):
        users.create_user(admin, username="has space", display_name="x", role=Role.USER)
    with pytest.raises(PasswordPolicyError):
        users.create_user(
            admin, username="shortpw", display_name="x", role=Role.USER, password="1234"
        )
    with pytest.raises(PasswordPolicyError):
        users.create_user(
            admin, username="samename", display_name="x", role=Role.USER, password="SameName"
        )
    assert (
        code_of(
            lambda: users.create_user(admin, username="analyst", display_name="x", role=Role.USER)
        )
        is ServiceErrorCode.USER_DUPLICATE
    )
    assert (
        code_of(
            lambda: users.create_user(
                admin, username="longname", display_name="长" * 41, role=Role.USER
            )
        )
        is ServiceErrorCode.USER_NAME_REQUIRED
    )
    assert stack.database.users.get_by_username("shortpw") is None  # 失败的什么都没写


def test_a_chosen_initial_password_is_used(stack: Stack, users: UserService) -> None:
    admin = stack.login("admin")
    issued = users.create_user(
        admin, username="chosen", display_name="", role=Role.USER, password="Chosen-Pass-9"
    )
    assert issued.temporary_password == "Chosen-Pass-9" and issued.user.display_name == "chosen"
    assert stack.auth.login("chosen", "Chosen-Pass-9").must_change_password


def test_generated_passwords_are_long_random_and_mixed() -> None:
    first, second = generate_temporary_password(), generate_temporary_password()
    assert first != second and len(first) == 12
    assert any(c.isdigit() for c in first) and any(c.isalpha() for c in first)
    assert not set(first) & set("0O1lI")


def test_disabling_blocks_login_and_ends_the_session_and_enabling_restores(
    stack: Stack, users: UserService
) -> None:
    admin = stack.login("admin")
    victim = stack.login("analyst")
    target = stack.database.users.get_by_username("analyst")
    assert target is not None
    users.set_active(admin, target.user_id, False)
    with pytest.raises(PermissionDeniedError):  # 已登录的会话立即失效
        stack.auth.authorize(victim, Permission.EXPERIMENT_VIEW)
    with pytest.raises(AccountDisabledError):
        stack.auth.login("analyst", PASSWORDS["analyst"])
    users.set_active(admin, target.user_id, True)
    assert stack.auth.login("analyst", PASSWORDS["analyst"]).role is Role.USER
    details = stack.audit(AuditAction.USER_CHANGED)
    assert "停用用户 analyst" in details and "启用用户 analyst" in details


def test_cannot_disable_yourself(stack: Stack, users: UserService) -> None:
    admin = stack.login("admin")
    me = stack.database.users.get_by_username("admin")
    assert me is not None
    assert (
        code_of(lambda: users.set_active(admin, me.user_id, False))
        is ServiceErrorCode.USER_SELF_DISABLE
    )
    assert stack.database.users.get(me.user_id).active  # type: ignore[union-attr]


def test_the_last_admin_cannot_be_disabled_or_demoted_but_a_second_one_allows_it(
    stack: Stack, users: UserService
) -> None:
    from gelinspect.domain.entities import User

    stack.database.users.add(
        User("dev", "developer", "开发", Role.DEVELOPER),
        password_hash=stack.auth.hash_password("Dev-Test-Pass1"),
        must_change_password=False,
    )
    dev = stack.auth.login("developer", "Dev-Test-Pass1")
    admin = stack.database.users.get_by_username("admin")
    assert admin is not None
    assert (
        code_of(lambda: users.set_active(dev, admin.user_id, False))
        is ServiceErrorCode.USER_LAST_ADMIN
    )
    second = users.create_user(dev, username="admin2", display_name="二号", role=Role.ADMIN)
    users.set_active(dev, admin.user_id, False)
    assert (
        code_of(lambda: users.change_role(dev, second.user.user_id, Role.USER))
        is ServiceErrorCode.USER_LAST_ADMIN
    )
    users.set_active(dev, admin.user_id, True)
    users.change_role(dev, second.user.user_id, Role.USER)
    updated = stack.database.users.get(second.user.user_id)
    assert updated is not None and updated.role is Role.USER


def test_role_change_takes_effect_in_the_current_session_immediately(
    stack: Stack, users: UserService
) -> None:
    admin = stack.login("admin")
    exp = stack.login("experimenter")
    target = exp.user
    groups = frozenset({PermissionGroup.VIEW})
    users.set_permission_groups(admin, target.user_id, groups)
    assert exp.role is Role.USER
    assert exp.can(Permission.EXPERIMENT_VIEW) and not exp.can(Permission.RESULT_REVIEW)
    stack.auth.authorize(exp, Permission.EXPERIMENT_VIEW)
    with pytest.raises(PermissionDeniedError):
        stack.auth.authorize(exp, Permission.RESULT_REVIEW)
    users.set_permission_groups(admin, target.user_id, groups)
    changes = [d for d in stack.audit(AuditAction.USER_CHANGED) if "修改用户 experimenter" in d]
    assert len(changes) == 1


def test_reset_password_forces_a_change_and_ends_the_old_session(
    stack: Stack, users: UserService
) -> None:
    admin = stack.login("admin")
    old_session = stack.login("analyst")
    target = stack.database.users.get_by_username("analyst")
    assert target is not None
    issued = users.reset_password(admin, target.user_id)
    with pytest.raises(PermissionDeniedError):
        stack.auth.authorize(old_session, Permission.EXPERIMENT_VIEW)
    with pytest.raises(InvalidCredentialsError):
        stack.auth.login("analyst", PASSWORDS["analyst"])
    fresh = stack.auth.login("analyst", issued.temporary_password)
    assert fresh.must_change_password  # 首次登录强制改密
    stack.auth.change_password(fresh, issued.temporary_password, "Another-New-Pass1")
    assert not fresh.must_change_password
    stack.auth.authorize(fresh, Permission.AUDIT_VIEW)
    text = "\n".join(stack.audit(AuditAction.USER_CHANGED))
    assert "重置用户 analyst 的密码" in text and issued.temporary_password not in text


def test_reset_password_rules(stack: Stack, users: UserService) -> None:
    admin = stack.login("admin")
    me = stack.database.users.get_by_username("admin")
    target = stack.database.users.get_by_username("analyst")
    assert me is not None and target is not None
    assert (
        code_of(lambda: users.reset_password(admin, me.user_id)) is ServiceErrorCode.USER_SELF_RESET
    )
    with pytest.raises(PasswordPolicyError):
        users.reset_password(admin, target.user_id, "short")
    assert (
        code_of(lambda: users.reset_password(admin, "missing-id"))
        is ServiceErrorCode.USER_NOT_FOUND
    )
    chosen = users.reset_password(admin, target.user_id, "Chosen-Reset-7")
    assert chosen.temporary_password == "Chosen-Reset-7"
