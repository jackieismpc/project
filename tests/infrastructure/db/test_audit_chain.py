"""审计哈希链：规范化、篡改与删除检测。篡改都是直接用 SQL 改库。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.infrastructure.db.repositories.audit import SqlAuditRepository
from gelinspect.interfaces.storage import GENESIS_HASH, ChainFailure

from .conftest import make_event


@pytest.fixture
def chained(tmp_path: Path) -> Database:
    db = open_database(tmp_path / "gel.db")
    db.audit.append_many([make_event(i) for i in range(10)])
    assert verify_audit_chain(db).ok
    return db


def raw_sql(database: Database, sql: str, *params: object) -> None:
    """绕过仓储，直接对库文件执行 SQL，模拟有人手工篡改。"""
    path = database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        connection.execute(sql, params)


def test_hash_matches_independent_computation(database: Database) -> None:
    record = database.audit.append(make_event(0, detail='登录 "成功"'))
    payload = {
        "seq": 1,
        "occurred_at": "2026-03-01T08:00:00.000000Z",
        "actor": "alice",
        "actor_role": "analyst",
        "action": "login",
        "detail": '登录 "成功"',
        "experiment_id": None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hashlib.sha256((GENESIS_HASH + "\n" + canonical).encode("utf-8")).hexdigest()
    assert record.seq == 1
    assert record.prev_hash == GENESIS_HASH
    assert record.hash == expected


def test_chain_links_records(database: Database) -> None:
    records = [database.audit.append(make_event(i)) for i in range(5)]
    assert [r.seq for r in records] == [1, 2, 3, 4, 5]
    for previous, current in zip(records, records[1:], strict=False):
        assert current.prev_hash == previous.hash
    result = verify_audit_chain(database)
    assert result.ok
    assert result.checked == 5
    assert result.first_bad_seq is None


def test_empty_chain_is_valid(database: Database) -> None:
    result = verify_audit_chain(database)
    assert (result.ok, result.checked) == (True, 0)


def test_append_many_equals_single_appends(tmp_path: Path) -> None:
    one = open_database(tmp_path / "one.db")
    many = open_database(tmp_path / "many.db")
    events = [make_event(i) for i in range(20)]
    for event in events:
        one.audit.append(event)
    many.audit.append_many(events)
    for seq in range(1, 21):
        a, b = one.audit.get(seq), many.audit.get(seq)
        assert a is not None and b is not None
        assert a.hash == b.hash
    assert many.audit.append_many([]) == 0
    one.close()
    many.close()


def test_modified_content_is_located(chained: Database) -> None:
    raw_sql(chained, "UPDATE audit_events SET detail = '被篡改' WHERE seq = 4")
    result = verify_audit_chain(chained)
    assert not result.ok
    assert result.first_bad_seq == 4
    assert result.failure is ChainFailure.HASH_MISMATCH
    assert result.checked == 3


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("actor", "'mallory'"),
        ("action", "'logout'"),
        ("occurred_at", "'2026-03-01T08:00:09.000000Z'"),
        ("actor_role", "'admin'"),
        ("experiment_id", "'E9'"),
    ],
)
def test_every_hashed_column_is_protected(chained: Database, column: str, value: str) -> None:
    raw_sql(chained, f"UPDATE audit_events SET {column} = {value} WHERE seq = 7")
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 7)


def test_garbage_timestamp_is_reported_not_raised(chained: Database) -> None:
    raw_sql(chained, "UPDATE audit_events SET occurred_at = 'not-a-time' WHERE seq = 2")
    # 读取表中的垃圾时间不应让校验崩溃，必须定位到该条。
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 2)


def test_rehashed_record_is_caught_at_next_link(chained: Database) -> None:
    """只重算被改记录自己的 hash，下一条的 prev_hash 会对不上。"""
    from gelinspect.infrastructure.db.repositories.audit import canonical_payload, compute_hash

    record = chained.audit.get(5)
    assert record is not None
    tampered = make_event(4, detail="被篡改并重算")
    new_hash = compute_hash(record.prev_hash, canonical_payload(5, tampered))
    raw_sql(
        chained,
        "UPDATE audit_events SET detail = ?, hash = ? WHERE seq = 5",
        tampered.detail,
        new_hash,
    )
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 6)
    assert result.failure is ChainFailure.LINK_BROKEN


def test_modified_prev_hash_is_located(chained: Database) -> None:
    raw_sql(chained, "UPDATE audit_events SET prev_hash = ? WHERE seq = 3", "f" * 64)
    result = verify_audit_chain(chained)
    assert (result.first_bad_seq, result.failure) == (3, ChainFailure.LINK_BROKEN)


@pytest.mark.parametrize("seq", [1, 5, 9])
def test_deleted_record_is_located(chained: Database, seq: int) -> None:
    raw_sql(chained, "DELETE FROM audit_events WHERE seq = ?", seq)
    result = verify_audit_chain(chained)
    assert not result.ok
    assert result.first_bad_seq == seq
    assert result.failure is ChainFailure.MISSING
    assert result.checked == seq - 1


def test_deleted_tail_is_located(chained: Database) -> None:
    raw_sql(chained, "DELETE FROM audit_events WHERE seq >= 8")
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 8)
    assert result.failure is ChainFailure.MISSING


def test_all_records_deleted_is_detected(chained: Database) -> None:
    raw_sql(chained, "DELETE FROM audit_events")
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 1)


def test_first_of_several_problems_is_reported(chained: Database) -> None:
    raw_sql(chained, "UPDATE audit_events SET detail = 'x' WHERE seq = 8")
    raw_sql(chained, "UPDATE audit_events SET detail = 'y' WHERE seq = 3")
    raw_sql(chained, "DELETE FROM audit_events WHERE seq = 6")
    assert verify_audit_chain(chained).first_bad_seq == 3


def test_head_tampering_is_detected(chained: Database) -> None:
    raw_sql(chained, "UPDATE audit_chain_head SET last_hash = ?", "e" * 64)
    result = verify_audit_chain(chained)
    assert (result.ok, result.failure) == (False, ChainFailure.HEAD_MISMATCH)


def test_head_missing_is_detected(chained: Database) -> None:
    raw_sql(chained, "DELETE FROM audit_chain_head")
    result = verify_audit_chain(chained)
    assert (result.ok, result.failure) == (False, ChainFailure.HEAD_MISMATCH)


def test_appending_after_tail_deletion_still_flags_the_gap(chained: Database) -> None:
    raw_sql(chained, "DELETE FROM audit_events WHERE seq = 10")
    chained.audit.append(make_event(99))  # 链头仍是 10，新记录序号 11
    result = verify_audit_chain(chained)
    assert (result.ok, result.first_bad_seq) == (False, 10)


def test_verification_spans_multiple_batches(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gelinspect.infrastructure.db.repositories.audit as audit_module

    monkeypatch.setattr(audit_module, "_VERIFY_BATCH", 7)
    database.audit.append_many([make_event(i) for i in range(30)])
    assert verify_audit_chain(database).checked == 30
    raw_sql(database, "DELETE FROM audit_events WHERE seq = 15")
    assert verify_audit_chain(database).first_bad_seq == 15
    raw_sql(database, "UPDATE audit_events SET detail = 'x' WHERE seq = 22")
    assert verify_audit_chain(database).first_bad_seq == 15


def test_audit_repository_is_append_only() -> None:
    public = {name for name in dir(SqlAuditRepository) if not name.startswith("_")}
    assert public == {"append", "append_many", "get", "query", "verify_chain"}
    forbidden = ("update", "delete", "remove", "set", "clear", "purge", "edit", "modify")
    assert not [name for name in public if name.startswith(forbidden)]


def test_actions_are_stored_by_value(database: Database) -> None:
    database.audit.append(make_event(action=AuditAction.REPORT_EXPORTED))
    record = database.audit.get(1)
    assert record is not None
    assert record.event.action is AuditAction.REPORT_EXPORTED


def test_concurrent_writers_do_not_fork_chain(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    db = open_database(tmp_path / "gel.db")

    def writer(worker: int) -> None:
        for i in range(20):
            db.audit.append(make_event(i, actor=f"w{worker}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))
    result = verify_audit_chain(db)
    assert (result.ok, result.checked) == (True, 80)
    db.close()
