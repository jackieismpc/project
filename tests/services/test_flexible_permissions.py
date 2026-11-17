"""灵活授权的服务层安全边界、持久化、事务回滚和会话失效。"""

from dataclasses import replace

import pytest

from gelinspect.app_context import AppContext
from gelinspect.domain.entities import User
from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import PERMISSION_GROUPS, Permission
from gelinspect.domain.permissions import PermissionGroup as G
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.auth import PermissionDeniedError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.user_service import UserService
from tests.services.conftest import Stack


def developer(stack: Stack) -> Session:
    user = User("developer-id", "developer", "开发人员", Role.DEVELOPER)
    stack.database.users.add(
        user, password_hash=stack.auth.hash_password("Developer-Test1"), must_change_password=False
    )
    return stack.auth.login("developer", "Developer-Test1")


@pytest.mark.parametrize("group", list(G))
def test_each_group_granted_and_removed_is_persisted_and_immediate(stack: Stack, group: G) -> None:
    users = UserService(stack.auth, stack.database)
    admin = stack.login("admin")
    issued = users.create_user(admin, username="worker", display_name="用户", role=Role.USER)
    session = stack.auth.login("worker", issued.temporary_password)
    stack.auth.change_password(session, issued.temporary_password, "Worker-Test1")
    assert not session.permissions()
    users.set_permission_groups(admin, session.user.user_id, frozenset({group}))
    assert session.permissions() == PERMISSION_GROUPS[group]
    for permission in PERMISSION_GROUPS[group]:
        stack.auth.authorize(session, permission)
    stored = stack.database.users.get(session.user.user_id)
    assert stored is not None and stored.permission_groups == frozenset({group})
    users.set_permission_groups(admin, session.user.user_id, frozenset())
    assert not session.permissions()
    for permission in PERMISSION_GROUPS[group]:
        with pytest.raises(PermissionDeniedError):
            stack.auth.authorize(session, permission)
    new_session = stack.auth.login("worker", "Worker-Test1")
    assert not new_session.permissions()


def test_all_groups_cannot_manage_accounts_or_company(stack: Stack) -> None:
    users = UserService(stack.auth, stack.database)
    session = stack.login("experimenter")
    users.set_permission_groups(stack.login("admin"), session.user.user_id, frozenset(G))
    for permission in (Permission.USER_MANAGE, Permission.COMPANY_CONFIGURE):
        with pytest.raises(PermissionDeniedError):
            stack.auth.authorize(session, permission)
    with pytest.raises(PermissionDeniedError):
        users.create_user(session, username="bad", display_name="bad", role=Role.ADMIN)
    with pytest.raises(PermissionDeniedError):
        users.set_permission_groups(session, session.user.user_id, frozenset(G))


def test_read_only_user_is_denied_review_run_and_signing(stack: Stack) -> None:
    from gelinspect.domain.permissions import PermissionGroup

    session = stack.login("analyst")
    users = UserService(stack.auth, stack.database)
    admin = stack.login("admin")
    users.set_permission_groups(admin, session.user.user_id, frozenset({PermissionGroup.VIEW}))
    for permission in (
        Permission.RESULT_REVIEW,
        Permission.EXPERIMENT_RUN,
        Permission.EXPERIMENT_SIGN,
    ):
        with pytest.raises(PermissionDeniedError):
            stack.auth.authorize(session, permission)


def test_admin_cannot_create_or_modify_developer_or_admin_category(stack: Stack) -> None:
    dev = developer(stack)
    users = UserService(stack.auth, stack.database)
    admin = stack.login("admin")
    for role in (Role.ADMIN, Role.DEVELOPER):
        with pytest.raises(ServiceError):
            users.create_user(admin, username="bad", display_name="bad", role=role)
    with pytest.raises(ServiceError):
        users.create_user(dev, username="another-dev", display_name="bad", role=Role.DEVELOPER)
    for target in (dev.user, admin.user):
        for operation in (
            lambda target=target: users.set_active(admin, target.user_id, False),
            lambda target=target: users.reset_password(admin, target.user_id),
            lambda target=target: users.change_role(admin, target.user_id, Role.USER),
            lambda target=target: users.set_permission_groups(admin, target.user_id, frozenset(G)),
        ):
            with pytest.raises(ServiceError):
                operation()
    assert len(stack.database.users.list_all()) == 4


def test_forged_snapshot_does_not_override_database_permissions(stack: Stack) -> None:
    session = stack.login("experimenter")
    session.user = replace(session.user, role=Role.DEVELOPER)
    with pytest.raises(PermissionDeniedError):
        stack.auth.authorize(session, Permission.USER_MANAGE)
    assert session.user.role is Role.USER


def test_revocation_disable_reset_logout_and_lock_remove_effective_permissions(
    stack: Stack,
) -> None:
    users = UserService(stack.auth, stack.database)
    admin = stack.login("admin")
    session = stack.login("experimenter")
    stack.auth.lock(session)
    assert not session.permissions()
    stack.auth.unlock(session, "12345678")
    assert session.permissions()
    users.set_active(admin, session.user.user_id, False)
    assert not session.permissions() and not stack.auth.sync_session(session)
    users.set_active(admin, session.user.user_id, True)
    with pytest.raises(PermissionDeniedError):
        stack.auth.authorize(session, Permission.EXPERIMENT_RUN)
    session = stack.auth.login("experimenter", "12345678")
    issued = users.reset_password(admin, session.user.user_id)
    assert not session.permissions()
    session = stack.auth.login("experimenter", issued.temporary_password)
    assert not session.permissions()
    stack.auth.change_password(session, issued.temporary_password, "Worker-New-Test1")
    assert session.permissions()
    stack.auth.logout(session)
    assert not session.permissions()


def test_unknown_group_is_rejected_without_mutation(stack: Stack) -> None:
    users = UserService(stack.auth, stack.database)
    session = stack.login("experimenter")
    before = session.user
    with pytest.raises(ServiceError):
        users.set_permission_groups(
            stack.login("admin"),
            before.user_id,
            frozenset({"user_manage"}),  # type: ignore[arg-type]
        )
    assert stack.database.users.get(before.user_id) == before


def test_failed_audit_rolls_back_grants_and_keeps_sessions_unchanged(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gelinspect.infrastructure.db.repositories.audit import SqlAuditRepository

    session = stack.login("experimenter")
    admin = stack.login("admin")
    before = session.user

    def fail(*args: object) -> None:
        raise StorageBusyError("injected audit failure")

    monkeypatch.setattr(SqlAuditRepository, "append", fail)
    with pytest.raises(StorageBusyError):
        UserService(stack.auth, stack.database).set_permission_groups(
            admin, before.user_id, frozenset({G.VIEW})
        )
    assert stack.database.users.get(before.user_id) == before
    assert session.user == before


def test_password_reset_and_change_invalidate_other_auth_instances(stack: Stack) -> None:
    from gelinspect.bootstrap import Argon2Hasher
    from gelinspect.services.auth import AuthService

    other_auth = AuthService(stack.database, Argon2Hasher())
    other_session = other_auth.login("experimenter", "12345678")
    users = UserService(stack.auth, stack.database)
    issued = users.reset_password(stack.login("admin"), other_session.user.user_id)
    with pytest.raises(PermissionDeniedError):
        other_auth.authorize(other_session, Permission.EXPERIMENT_RUN)
    assert not other_session.permissions()
    session = stack.auth.login("experimenter", issued.temporary_password)
    stack.auth.change_password(session, issued.temporary_password, "Worker-Test2")
    other_session = other_auth.login("experimenter", "Worker-Test2")
    stack.auth.change_password(session, "Worker-Test2", "Worker-Test3")
    assert not other_auth.sync_session(other_session)
    assert session.permissions()


def test_waiting_management_write_rechecks_actor_under_write_lock(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager

    from gelinspect.interfaces.storage import UnitOfWork

    admin = stack.login("admin")
    original = stack.database.unit_of_work

    @contextmanager
    def after_demotion() -> Iterator[UnitOfWork]:
        stack.database.users.update(replace(admin.user, role=Role.USER))
        with original() as uow:
            yield uow

    monkeypatch.setattr(stack.database, "unit_of_work", after_demotion)
    with pytest.raises(PermissionDeniedError):
        UserService(stack.auth, stack.database).create_user(
            admin, username="should-not-exist", display_name="x", role=Role.USER
        )
    assert stack.database.users.get_by_username("should-not-exist") is None


def test_revoking_operator_does_not_stop_an_already_running_experiment(
    real_app: AppContext,
) -> None:
    from gelinspect.domain.state_machine import ExperimentState
    from tests.services.engine_helpers import eventually, ready_experiment

    experiment = ready_experiment(range(1, 9))
    real_app.database.experiments.save(experiment)
    session = real_app.auth.login("experimenter", "12345678")
    operator = real_app.auth.authorize(session, Permission.EXPERIMENT_RUN)
    real_app.engine.start(experiment.experiment_id, operator)
    assert real_app.engine.active_experiment_id == experiment.experiment_id

    def started() -> bool:
        current = real_app.database.experiments.get(experiment.experiment_id)
        return current is not None and current.state is ExperimentState.INCUBATING

    assert eventually(started)
    admin = real_app.auth.login("admin", "12345678")
    real_app.users.set_permission_groups(admin, operator.user_id, frozenset({G.VIEW}))
    assert real_app.engine.active_experiment_id == experiment.experiment_id
    with pytest.raises(PermissionDeniedError):
        real_app.monitor.stop(session, experiment.experiment_id, "withdrawn permission")
    assert real_app.engine.active_experiment_id == experiment.experiment_id
    real_app.monitor.stop(admin, experiment.experiment_id, "test cleanup")


def test_login_during_password_reset_does_not_adopt_new_version_using_old_password(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = stack.database.users.get_by_username("experimenter")
    assert user is not None
    original = stack.auth._hasher.verify
    reset_hash = stack.auth.hash_password("Reset-During-Login1")

    def verify_then_reset(encoded: str, password: str) -> bool:
        verified = original(encoded, password)
        stack.database.users.set_password_hash(user.user_id, reset_hash, must_change_password=True)
        return verified

    monkeypatch.setattr(stack.auth._hasher, "verify", verify_then_reset)
    with pytest.raises(PermissionDeniedError):
        stack.auth.login("experimenter", "12345678")


def test_password_change_does_not_overwrite_concurrent_admin_reset(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gelinspect.infrastructure.db.repositories.users import SqlUserRepository

    session = stack.login("experimenter")
    original = SqlUserRepository.set_password_hash
    reset_hash = stack.auth.hash_password("Concurrent-Reset1")

    def concurrent_reset(
        repo: SqlUserRepository,
        user_id: str,
        password_hash: str,
        *,
        must_change_password: bool,
        expected_version: int | None = None,
    ) -> None:
        original(repo, user_id, reset_hash, must_change_password=True)
        original(
            repo,
            user_id,
            password_hash,
            must_change_password=must_change_password,
            expected_version=expected_version,
        )

    monkeypatch.setattr(SqlUserRepository, "set_password_hash", concurrent_reset)
    with pytest.raises(PermissionDeniedError):
        stack.auth.change_password(session, "12345678", "Should-Not-Replace1")
    assert not session.permissions()
    credentials = stack.database.users.get_credentials("experimenter")
    assert credentials is not None and credentials.password_hash == reset_hash
