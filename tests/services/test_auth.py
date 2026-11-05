"""登录、锁定、改密、会话与服务层权限校验。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gelinspect.bootstrap import Argon2Hasher, DatabaseFirstAdminSeeder, initialize_accounts
from gelinspect.domain.entities import User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission
from gelinspect.infrastructure.db import Database, open_database
from gelinspect.infrastructure.db.passwords import hash_password
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import (
    AccountDisabledError,
    AccountLockedError,
    AuthService,
    InvalidCredentialsError,
    LoginPolicy,
    PasswordPolicyError,
    PermissionDeniedError,
    SetupNotAllowedError,
)
from gelinspect.services.queries import QueryService
from simulation.demo_accounts import DEMO_ACCOUNTS, DemoAccounts

PASSWORDS = {a.username: a.password for a in DEMO_ACCOUNTS}
T0 = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = open_database(tmp_path / "auth.db")
    initialize_accounts(db, DemoAccounts())
    return db


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def auth(database: Database, clock: Clock) -> AuthService:
    return AuthService(
        database,
        Argon2Hasher(),
        policy=LoginPolicy(max_attempts=3, lockout_s=60.0),
        clock=clock,
        seeder=DatabaseFirstAdminSeeder(database),
    )


def audit_of(database: Database, action: AuditAction, actor: str | None = None) -> list[str]:
    page = database.audit.query(AuditQuery(action=action, actor=actor), PageRequest(limit=200))
    return [r.event.detail for r in sorted(page.items, key=lambda r: r.seq)]


def test_login_success_writes_audit(auth: AuthService, database: Database) -> None:
    session = auth.login("experimenter", "Experimenter@2026")
    assert session.user.role is Role.EXPERIMENTER
    assert not session.must_change_password
    assert audit_of(database, AuditAction.LOGIN, "experimenter") == ["登录成功"]


def test_wrong_password_is_rejected_and_audited(auth: AuthService, database: Database) -> None:
    with pytest.raises(InvalidCredentialsError) as info:
        auth.login("admin", "wrong")
    assert info.value.remaining_attempts == 2
    details = audit_of(database, AuditAction.LOGIN_FAILED, "admin")
    assert len(details) == 1 and "密码错误" in details[0]


def test_unknown_user_is_audited_and_indistinguishable(
    auth: AuthService, database: Database
) -> None:
    with pytest.raises(InvalidCredentialsError):
        auth.login("nobody", "whatever1")
    assert audit_of(database, AuditAction.LOGIN_FAILED, "nobody")


def test_lockout_after_max_failures_and_release_after_timeout(
    auth: AuthService, database: Database, clock: Clock
) -> None:
    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            auth.login("analyst", "bad")
    with pytest.raises(AccountLockedError) as info:
        auth.login("analyst", "bad")
    assert info.value.retry_after_s == pytest.approx(60.0)
    # 锁定期间连正确的密码也被拒绝
    with pytest.raises(AccountLockedError):
        auth.login("analyst", "Analyst@2026")
    assert any("锁定" in d for d in audit_of(database, AuditAction.LOGIN_FAILED, "analyst"))
    clock.advance(61)
    assert auth.login("analyst", "Analyst@2026").user.username == "analyst"


def test_success_resets_the_failure_counter(auth: AuthService) -> None:
    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            auth.login("admin", "bad")
    auth.login("admin", "Admin@2026")
    with pytest.raises(InvalidCredentialsError) as info:
        auth.login("admin", "bad")
    assert info.value.remaining_attempts == 2


def test_disabled_account_cannot_log_in(auth: AuthService, database: Database) -> None:
    user = database.users.get_by_username("analyst")
    assert user is not None
    database.users.update(
        User(user.user_id, user.username, user.display_name, user.role, active=False)
    )
    with pytest.raises(AccountDisabledError):
        auth.login("analyst", "Analyst@2026")


def test_logout_audits_once_and_invalidates_the_session(
    auth: AuthService, database: Database
) -> None:
    session = auth.login("admin", "Admin@2026")
    auth.logout(session)
    auth.logout(session)
    assert audit_of(database, AuditAction.LOGOUT, "admin") == ["退出登录"]
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.TEMPLATE_VIEW)


def test_authorize_checks_role_and_audits_denial(auth: AuthService, database: Database) -> None:
    session = auth.login("experimenter", "Experimenter@2026")
    assert auth.authorize(session, Permission.EXPERIMENT_RUN).username == "experimenter"
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.USER_MANAGE)
    assert audit_of(database, AuditAction.PERMISSION_DENIED, "experimenter") == [
        "拒绝 user_manage：角色无此权限"
    ]


def test_authorize_rejects_missing_session(auth: AuthService) -> None:
    with pytest.raises(PermissionDeniedError):
        auth.authorize(None, Permission.TEMPLATE_VIEW)


def test_role_change_takes_effect_immediately(auth: AuthService, database: Database) -> None:
    session = auth.login("analyst", "Analyst@2026")
    auth.authorize(session, Permission.RESULT_REVIEW)
    user = database.users.get_by_username("analyst")
    assert user is not None
    database.users.update(User(user.user_id, user.username, user.display_name, Role.ADMIN))
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.RESULT_REVIEW)


def test_disabling_the_account_ends_access_mid_session(
    auth: AuthService, database: Database
) -> None:
    session = auth.login("admin", "Admin@2026")
    user = database.users.get_by_username("admin")
    assert user is not None
    database.users.update(
        User(user.user_id, user.username, user.display_name, user.role, active=False)
    )
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.TEMPLATE_VIEW)


def test_lock_blocks_every_operation_until_unlocked(auth: AuthService, database: Database) -> None:
    session = auth.login("admin", "Admin@2026")
    auth.lock(session)
    assert session.locked and session.permissions() == frozenset()
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.TEMPLATE_VIEW)
    with pytest.raises(InvalidCredentialsError):
        auth.unlock(session, "bad")
    assert session.locked
    auth.unlock(session, "Admin@2026")
    assert not session.locked
    auth.authorize(session, Permission.TEMPLATE_VIEW)
    assert audit_of(database, AuditAction.SCREEN_LOCKED, "admin")
    assert audit_of(database, AuditAction.SCREEN_UNLOCKED, "admin")


def test_unlock_failures_count_towards_lockout(auth: AuthService) -> None:
    session = auth.login("admin", "Admin@2026")
    auth.lock(session)
    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            auth.unlock(session, "bad")
    with pytest.raises(AccountLockedError):
        auth.unlock(session, "bad")


def test_change_password_flow(auth: AuthService, database: Database) -> None:
    session = auth.login("admin", "Admin@2026")
    with pytest.raises(InvalidCredentialsError):
        auth.change_password(session, "wrong", "NewPassw0rd!")
    with pytest.raises(PasswordPolicyError):
        auth.change_password(session, "Admin@2026", "short")
    with pytest.raises(PasswordPolicyError):
        auth.change_password(session, "Admin@2026", "Admin@2026")
    auth.change_password(session, "Admin@2026", "NewPassw0rd!")
    assert audit_of(database, AuditAction.PASSWORD_CHANGED, "admin")
    with pytest.raises(InvalidCredentialsError):
        auth.login("admin", "Admin@2026")
    assert auth.login("admin", "NewPassw0rd!")


def test_must_change_password_blocks_everything_else(auth: AuthService, database: Database) -> None:
    database.users.add(
        User("u9", "fresh", "新人", Role.ADMIN),
        password_hash=hash_password("Initial-pass1"),
        must_change_password=True,
    )
    session = auth.login("fresh", "Initial-pass1")
    assert session.must_change_password
    with pytest.raises(PermissionDeniedError):
        auth.authorize(session, Permission.TEMPLATE_VIEW)
    auth.change_password(session, "Initial-pass1", "Better-pass2")
    assert not session.must_change_password
    auth.authorize(session, Permission.TEMPLATE_VIEW)


def test_first_admin_requires_empty_system(tmp_path: Path, clock: Clock) -> None:
    db = open_database(tmp_path / "empty.db")
    service = AuthService(db, Argon2Hasher(), clock=clock, seeder=DatabaseFirstAdminSeeder(db))
    assert service.needs_first_admin()
    with pytest.raises(PasswordPolicyError):
        service.create_first_admin(username="root", display_name="", password="short")
    with pytest.raises(PasswordPolicyError):
        service.create_first_admin(username="has space", display_name="", password="long-enough1")
    service.create_first_admin(username="root", display_name="", password="long-enough1")
    assert not service.needs_first_admin()
    # 密码是管理员自己设的，直接生效、首次登录不强制改密；系统里没有任何默认账号
    assert [u.username for u in db.users.list_all()] == ["root"]
    session = service.login("root", "long-enough1")
    assert not session.must_change_password and session.role is Role.ADMIN
    service.authorize(session, Permission.USER_MANAGE)
    with pytest.raises(SetupNotAllowedError):
        service.create_first_admin(username="again", display_name="", password="long-enough1")
    db.close()


def test_first_admin_not_available_without_a_seeder(tmp_path: Path) -> None:
    db = open_database(tmp_path / "noseed.db")
    service = AuthService(db, Argon2Hasher())
    with pytest.raises(SetupNotAllowedError):
        service.create_first_admin(username="root", display_name="", password="long-enough1")
    db.close()


@pytest.mark.parametrize(
    ("call", "role", "allowed"),
    [
        (lambda q, s: q.recent_experiments(s), Role.ANALYST, True),
        (lambda q, s: q.faulted_experiments(s), Role.EXPERIMENTER, True),
        (lambda q, s: q.recent_audit(s), Role.ADMIN, True),
        (lambda q, s: q.recent_audit(s), Role.ANALYST, True),
        (lambda q, s: q.recent_audit(s), Role.EXPERIMENTER, False),
    ],
)
def test_query_service_rechecks_permissions(
    auth: AuthService,
    database: Database,
    call: Callable[[QueryService, object], object],
    role: Role,
    allowed: bool,
) -> None:
    username = {Role.ADMIN: "admin", Role.EXPERIMENTER: "experimenter", Role.ANALYST: "analyst"}
    session = auth.login(username[role], PASSWORDS[username[role]])
    queries = QueryService(auth, database)
    if allowed:
        call(queries, session)
    else:
        with pytest.raises(PermissionDeniedError):
            call(queries, session)


def test_query_service_rejects_a_forged_session(auth: AuthService, database: Database) -> None:
    """UI 被绕过：没有登录就直接调用服务也被拒绝。"""
    from gelinspect.services.auth import Session

    forged = Session(
        user=User("x", "forged", "伪造", Role.ADMIN),
        must_change_password=False,
        started_at=T0,
        session_id="not-registered",
    )
    queries = QueryService(auth, database)
    with pytest.raises(PermissionDeniedError):
        queries.recent_audit(forged)
    with pytest.raises(PermissionDeniedError):
        queries.recent_experiments(None)


# ---------------------------------------------------------------------------
# 等写锁超时：抛 AuthStorageBusyError（不是原始的 SQLAlchemy 异常），不崩溃

import sqlite3  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from contextlib import contextmanager  # noqa: E402

from gelinspect.services.auth import AuthErrorCode, AuthStorageBusyError  # noqa: E402


@pytest.fixture
def busy_auth(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[AuthService, Database, Path]]:
    monkeypatch.setattr("gelinspect.infrastructure.db.database.BUSY_TIMEOUT_MS", 20)
    path = tmp_path / "busy.db"
    db = open_database(path)
    initialize_accounts(db, DemoAccounts())
    service = AuthService(db, Argon2Hasher(), clock=clock, seeder=DatabaseFirstAdminSeeder(db))
    yield service, db, path
    db.close()


@contextmanager
def writer_holding_the_lock(path: Path) -> Iterator[None]:
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_login_while_the_database_is_busy_raises_a_clear_error(
    busy_auth: tuple[AuthService, Database, Path],
) -> None:
    service, db, path = busy_auth
    with writer_holding_the_lock(path):
        with pytest.raises(AuthStorageBusyError) as caught:
            service.login("admin", PASSWORDS["admin"])
        assert caught.value.code is AuthErrorCode.STORAGE_BUSY
        assert not isinstance(caught.value, sqlite3.Error)
    # 没有留痕的登录不成功，也没有留下半个会话；锁释放后可以正常登录
    assert audit_of(db, AuditAction.LOGIN, "admin") == []
    session = service.login("admin", PASSWORDS["admin"])
    assert audit_of(db, AuditAction.LOGIN, "admin") == ["登录成功"]
    service.authorize(session, Permission.USER_MANAGE)


def test_wrong_password_while_busy_is_still_reported_as_wrong(
    busy_auth: tuple[AuthService, Database, Path],
) -> None:
    service, _db, path = busy_auth
    with writer_holding_the_lock(path), pytest.raises(InvalidCredentialsError):
        service.login("admin", "wrong-password")


def test_protective_actions_still_work_while_busy(
    busy_auth: tuple[AuthService, Database, Path],
) -> None:
    """拒绝、锁屏、退出是保护动作：数据库忙时照样生效，只是审计缺失只记日志。"""
    service, _db, path = busy_auth
    session = service.login("analyst", PASSWORDS["analyst"])
    with writer_holding_the_lock(path):
        with pytest.raises(PermissionDeniedError) as denied:
            service.authorize(session, Permission.USER_MANAGE)
        assert denied.value.code is AuthErrorCode.DENIED
        service.lock(session)
        assert session.locked
        with pytest.raises(PermissionDeniedError):
            service.authorize(session, Permission.EXPERIMENT_VIEW)
    service.unlock(session, PASSWORDS["analyst"])
    with writer_holding_the_lock(path):
        service.logout(session)  # 不抛异常
    with pytest.raises(PermissionDeniedError):
        service.authorize(session, Permission.EXPERIMENT_VIEW)


def test_unlock_while_busy_keeps_the_screen_locked(
    busy_auth: tuple[AuthService, Database, Path],
) -> None:
    service, db, path = busy_auth
    session = service.login("analyst", PASSWORDS["analyst"])
    service.lock(session)
    with writer_holding_the_lock(path):
        with pytest.raises(AuthStorageBusyError):
            service.unlock(session, PASSWORDS["analyst"])
        assert session.locked
    service.unlock(session, PASSWORDS["analyst"])
    assert not session.locked
    assert audit_of(db, AuditAction.SCREEN_UNLOCKED, "analyst") == ["解锁屏幕"]


def test_change_password_while_busy_changes_nothing(
    busy_auth: tuple[AuthService, Database, Path],
) -> None:
    service, _db, path = busy_auth
    session = service.login("analyst", PASSWORDS["analyst"])
    with writer_holding_the_lock(path), pytest.raises(AuthStorageBusyError):
        service.change_password(session, PASSWORDS["analyst"], "Brand-new-pass1")
    service.login("analyst", PASSWORDS["analyst"])  # 旧密码仍然有效
    with pytest.raises(InvalidCredentialsError):
        service.login("analyst", "Brand-new-pass1")


def test_first_admin_while_busy_raises_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gelinspect.infrastructure.db.database.BUSY_TIMEOUT_MS", 20)
    path = tmp_path / "empty.db"
    db = open_database(path)
    service = AuthService(db, Argon2Hasher(), seeder=DatabaseFirstAdminSeeder(db))
    with writer_holding_the_lock(path), pytest.raises(AuthStorageBusyError):
        service.create_first_admin(username="root", display_name="", password="long-enough1")
    assert service.needs_first_admin()
    service.create_first_admin(username="root", display_name="", password="long-enough1")
    assert not service.needs_first_admin()
    db.close()
