"""等写锁超时：仓储抛 ``StorageBusyError``，而不是原始的 SQLAlchemy 异常。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from gelinspect.domain.entities import User
from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind, Role
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.interfaces.storage import ReagentCheckRecord, StorageBusyError

from .conftest import make_event, make_experiment


@pytest.fixture
def busy_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    """等写锁只等 20 毫秒的数据库，让“被占用”的情形立刻出现。"""
    monkeypatch.setattr("gelinspect.infrastructure.db.database.BUSY_TIMEOUT_MS", 20)
    database = open_database(tmp_path / "gel.db")
    yield database
    database.close()


@contextmanager
def write_lock_held(path: Path) -> Iterator[None]:
    """另一个连接占着写锁（模拟别的进程或线程在写）。"""
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_audit_append_raises_storage_busy_not_a_raw_sqlalchemy_error(
    busy_database: Database, tmp_path: Path
) -> None:
    with write_lock_held(tmp_path / "gel.db"):
        with pytest.raises(StorageBusyError) as caught:
            busy_database.audit.append(make_event())
        assert not isinstance(caught.value, SQLAlchemyError)
    # 锁释放后同样的写入成功，哈希链完好：被拒绝的那次什么都没有写入
    busy_database.audit.append(make_event())
    check = verify_audit_chain(busy_database)
    assert check.ok and check.checked == 1


def test_user_writes_raise_storage_busy(busy_database: Database, tmp_path: Path) -> None:
    user = User("u1", "alice", "Alice", Role.ADMIN)
    busy_database.users.add(user, password_hash="h", must_change_password=False)
    with write_lock_held(tmp_path / "gel.db"):
        with pytest.raises(StorageBusyError):
            busy_database.users.add(
                User("u2", "bob", "Bob", Role.ANALYST),
                password_hash="h",
                must_change_password=False,
            )
        with pytest.raises(StorageBusyError):
            busy_database.users.set_password_hash("u1", "h2", must_change_password=True)
    assert [u.username for u in busy_database.users.list_all()] == ["alice"]


def test_other_repository_writes_raise_storage_busy(
    busy_database: Database, tmp_path: Path
) -> None:
    template = next(iter(BUILTIN_TEMPLATES.values()))
    record = ReagentCheckRecord(
        checked_at=datetime(2026, 11, 5, tzinfo=UTC),
        actor="alice",
        kind=ReagentKind.WATER,
        batch_no="W1",
        product_code="P",
        status=ReagentCheckStatus.VALID,
        registered=True,
        expiry_date=date(2027, 1, 1),
    )
    with write_lock_held(tmp_path / "gel.db"):
        with pytest.raises(StorageBusyError):
            busy_database.templates.save(template)
        with pytest.raises(StorageBusyError):
            busy_database.experiments.save(make_experiment("E-BUSY"))
        with pytest.raises(StorageBusyError):
            busy_database.reagent_checks.add(record)
    assert busy_database.templates.get(template.template_id) is None
    assert busy_database.experiments.get("E-BUSY") is None


def test_reads_are_not_blocked_by_a_writer(busy_database: Database, tmp_path: Path) -> None:
    """WAL：别人在写时读照常进行，所以界面查询不会因此失败。"""
    busy_database.audit.append(make_event())
    with write_lock_held(tmp_path / "gel.db"):
        assert busy_database.audit.get(1) is not None


def test_non_lock_errors_are_not_swallowed(busy_database: Database) -> None:
    """只转换“拿不到锁”；重复主键等其他错误仍按原来的类型抛出。"""
    from gelinspect.interfaces.storage import DuplicateError

    user = User("u1", "alice", "Alice", Role.ADMIN)
    busy_database.users.add(user, password_hash="h", must_change_password=False)
    with pytest.raises(DuplicateError):
        busy_database.users.add(user, password_hash="h", must_change_password=False)
