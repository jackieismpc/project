"""开发数据采用新结构重建，旧结构明确拒绝，不自动迁移或删除。"""

import sqlite3
from pathlib import Path

import pytest

from gelinspect.domain.entities import User
from gelinspect.domain.enums import PermissionGroup, Role
from gelinspect.infrastructure.db import open_database
from gelinspect.interfaces.storage import DatabaseVersionError


def test_old_permission_database_is_rejected_and_preserved(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO users VALUES ('old-user')")
    with pytest.raises(DatabaseVersionError, match="不迁移旧开发数据"):
        open_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == [("old-user",)]
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='alembic_version'"
        ).fetchall()


def test_fresh_database_preserves_groups_and_password_revision(tmp_path: Path) -> None:
    db = open_database(tmp_path / "new.db")
    try:
        user = User(
            "u",
            "worker",
            "用户",
            Role.USER,
            permission_groups=frozenset({PermissionGroup.EXPERIMENT, PermissionGroup.TEMPLATE}),
        )
        db.users.add(user, password_hash="hash1", must_change_password=False)
        assert db.users.get("u") == user
        db.users.set_password_hash("u", "hash2", must_change_password=True)
        current = db.users.get("u")
        assert current is not None and current.auth_version == 1
        assert current.permission_groups == user.permission_groups
    finally:
        db.close()


def test_stale_password_update_cannot_overwrite_reset(tmp_path: Path) -> None:
    from gelinspect.interfaces.storage import StateConflictError

    db = open_database(tmp_path / "cas.db")
    try:
        db.users.add(
            User("u", "worker", "用户", Role.USER),
            password_hash="initial",
            must_change_password=False,
        )
        db.users.set_password_hash("u", "admin-reset", must_change_password=True)
        with pytest.raises(StateConflictError):
            db.users.set_password_hash(
                "u", "stale-update", must_change_password=False, expected_version=0
            )
        credentials = db.users.get_credentials("worker")
        assert credentials is not None and credentials.password_hash == "admin-reset"
        assert credentials.must_change_password and credentials.user.auth_version == 1
    finally:
        db.close()
