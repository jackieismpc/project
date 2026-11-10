"""初始数据。"""

from __future__ import annotations

import inspect

import pytest

from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.templates import ALL_BUILTIN_TEMPLATES, BUILTIN_TEMPLATES
from gelinspect.infrastructure.db import Database, seed_defaults, verify_audit_chain
from gelinspect.infrastructure.db.passwords import needs_rehash, verify_password
from gelinspect.interfaces.storage import AuditQuery, PageRequest

PASSWORD = "caller-supplied-pw"


def test_seed_creates_templates_and_admin(database: Database) -> None:
    result = seed_defaults(database, admin_password=PASSWORD)
    assert result.admin_created
    assert set(result.templates_added) == {t.template_id for t in ALL_BUILTIN_TEMPLATES}
    for template in ALL_BUILTIN_TEMPLATES:
        assert database.templates.get(template.template_id) == template
        assert database.templates.is_builtin(template.template_id)

    credentials = database.users.get_credentials("admin")
    assert credentials is not None
    assert credentials.user.role is Role.ADMIN
    assert credentials.must_change_password is False  # 密码是调用方（管理员本人）设的，直接生效
    assert credentials.password_hash.startswith("$argon2id$")
    assert PASSWORD not in credentials.password_hash
    assert verify_password(credentials.password_hash, PASSWORD)
    assert not needs_rehash(credentials.password_hash)


def test_seed_can_require_a_password_change_for_a_delegated_password(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD, must_change_password=True)
    credentials = database.users.get_credentials("admin")
    assert credentials is not None and credentials.must_change_password is True
    page = database.audit.query(AuditQuery(action=AuditAction.USER_CHANGED), PageRequest())
    assert any("首次登录须修改密码" in r.event.detail for r in page.items)


def test_seed_without_forced_change_does_not_mention_it_in_the_audit(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD)
    page = database.audit.query(AuditQuery(action=AuditAction.USER_CHANGED), PageRequest())
    assert [r.event.detail for r in page.items] == ["初始化管理员 admin"]


def test_seed_is_idempotent_and_does_not_reset_password(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD)
    credentials = database.users.get_credentials("admin")
    assert credentials is not None
    database.users.set_password_hash(
        credentials.user.user_id, credentials.password_hash, must_change_password=False
    )
    again = seed_defaults(database, admin_password="another-password")
    assert not again.admin_created
    assert again.templates_added == ()
    after = database.users.get_credentials("admin")
    assert after is not None
    assert after.must_change_password is False
    assert verify_password(after.password_hash, PASSWORD)
    assert len(database.users.list_all()) == 1


def test_seed_does_not_overwrite_edited_builtin_template(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD)
    original = BUILTIN_TEMPLATES[next(iter(BUILTIN_TEMPLATES))]
    assert database.templates.get(original.template_id) == original
    seed_defaults(database, admin_password=PASSWORD)
    assert len(database.templates.list_all()) == 6


def test_seed_writes_valid_audit_trail(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD)
    page = database.audit.query(AuditQuery(actor="system"), PageRequest(limit=100))
    assert page.total == 7
    assert verify_audit_chain(database).ok
    assert all(PASSWORD not in r.event.detail for r in page.items)


def test_no_default_password_in_code() -> None:
    parameter = inspect.signature(seed_defaults).parameters["admin_password"]
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        seed_defaults()  # type: ignore[call-arg]


@pytest.mark.parametrize("password", ["", "short", "1234567"])
def test_weak_password_rejected(database: Database, password: str) -> None:
    with pytest.raises(ValueError, match="密码"):
        seed_defaults(database, admin_password=password)
    assert database.users.list_all() == []


def test_custom_admin_username(database: Database) -> None:
    seed_defaults(database, admin_password=PASSWORD, admin_username="root")
    assert database.users.get_by_username("root") is not None
    assert database.users.get_by_username("admin") is None
