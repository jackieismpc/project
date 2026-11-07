"""审计链 HMAC：启用、兼容旧链、切换事件、改库重算被发现、密钥丢失、两步之间被打断。

篡改都直接用 SQL 改库文件，并模拟“懂算法的人”重算整条链和链头。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.infrastructure.db.repositories.audit import (
    compute_hash,
    compute_hmac,
    key_id_of,
)
from gelinspect.infrastructure.security.audit_keys import (
    AUDIT_KEY_FILE,
    AuditKeyRing,
    RingEntry,
    activate_audit_hmac,
    read_chain_keys,
)
from gelinspect.infrastructure.security.protectors import INSTALL_SECRET_FILE, FileKeyProtector
from gelinspect.interfaces.storage import GENESIS_HASH, ChainFailure, ChainType, StorageError

from ..db.conftest import make_event

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def ring_of(tmp_path: Path) -> AuditKeyRing:
    keys = tmp_path / "keys"
    return AuditKeyRing(keys / AUDIT_KEY_FILE, FileKeyProtector(keys / INSTALL_SECRET_FILE))


def connect(database: Database) -> sqlite3.Connection:
    path = database.engine.url.database
    assert path is not None
    return sqlite3.connect(path)


def canonical(row: sqlite3.Row) -> bytes:
    payload = {
        "seq": row["seq"],
        "occurred_at": row["occurred_at"],
        "actor": row["actor"],
        "actor_role": row["actor_role"],
        "action": row["action"],
        "detail": row["detail"],
        "experiment_id": row["experiment_id"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def rewrite_chain(database: Database, *, secret: bytes | None = None) -> None:
    """模拟能写库的人：重算整条链（SHA-256，或用给定的“密钥”算 HMAC）和链头。"""
    with connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM audit_events ORDER BY seq").fetchall()
        prev = GENESIS_HASH
        for row in rows:
            data = canonical(row)
            digest = (
                compute_hash(prev, data) if secret is None else compute_hmac(secret, prev, data)
            )
            connection.execute(
                "UPDATE audit_events SET prev_hash = ?, hash = ? WHERE seq = ?",
                (prev, digest, row["seq"]),
            )
            prev = digest
        connection.execute(
            "UPDATE audit_chain_head SET last_seq = ?, last_hash = ? WHERE id = 1",
            (rows[-1]["seq"], prev),
        )


def tamper(database: Database, seq: int, detail: str) -> None:
    with connect(database) as connection:
        connection.execute("UPDATE audit_events SET detail = ? WHERE seq = ?", (detail, seq))


@pytest.fixture
def legacy(tmp_path: Path) -> Database:
    """P13 之前的库：10 条纯 SHA-256 记录。"""
    database = open_database(tmp_path / "gel.db")
    database.audit.append_many([make_event(i) for i in range(10)])
    result = verify_audit_chain(database)
    assert result.ok and result.chain_type is ChainType.SHA256
    return database


def test_legacy_chain_switches_to_hmac_and_records_the_switch(
    legacy: Database, tmp_path: Path
) -> None:
    activation = activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    assert activation.switched and activation.from_seq == 11
    switch = legacy.audit.get(11)
    assert switch is not None and switch.event.action is AuditAction.AUDIT_CHAIN_KEYED
    assert switch.event.actor == "system" and "HMAC-SHA256" in switch.event.detail
    legacy.audit.append_many([make_event(i) for i in range(10, 15)])
    result = verify_audit_chain(legacy)
    assert result.ok and result.checked == 16
    assert result.chain_type is ChainType.MIXED and result.hmac_from_seq == 11
    assert legacy.audit.segments() == [(11, switch_key_id(tmp_path))]


def switch_key_id(tmp_path: Path) -> str:
    keys = read_chain_keys(
        tmp_path / "keys", FileKeyProtector(tmp_path / "keys" / INSTALL_SECRET_FILE)
    )
    assert keys is not None and keys.current is not None
    return keys.current.key_id


def test_old_records_still_verify_with_the_old_algorithm(legacy: Database, tmp_path: Path) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    with connect(legacy) as connection:
        stored = connection.execute(
            "SELECT hash, prev_hash FROM audit_events WHERE seq = 5"
        ).fetchone()
        previous = connection.execute("SELECT hash FROM audit_events WHERE seq = 4").fetchone()
    assert stored[1] == previous[0]
    assert len(stored[0]) == 64  # 旧记录的哈希没有被改写
    tamper(legacy, 5, "改过的旧记录")
    result = verify_audit_chain(legacy)
    assert not result.ok and result.first_bad_seq == 5
    assert result.failure is ChainFailure.HASH_MISMATCH


def test_fresh_database_is_hmac_from_the_first_record(tmp_path: Path) -> None:
    database = open_database(tmp_path / "gel.db")
    activation = activate_audit_hmac(database, ring_of(tmp_path), clock=lambda: NOW)
    assert activation.from_seq == 1
    database.audit.append(make_event(1))
    result = verify_audit_chain(database)
    assert result.ok and result.chain_type is ChainType.HMAC_SHA256 and result.hmac_from_seq == 1


def test_restart_reuses_the_key_without_another_switch(legacy: Database, tmp_path: Path) -> None:
    first = activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    path = legacy.engine.url.database
    assert path is not None
    legacy.close()
    reopened = open_database(path)
    second = activate_audit_hmac(reopened, ring_of(tmp_path), clock=lambda: NOW)
    assert not second.switched and second.from_seq == first.from_seq
    reopened.audit.append(make_event(99))
    assert verify_audit_chain(reopened).ok
    assert len(reopened.audit.segments()) == 1


def test_editing_the_database_and_recomputing_the_sha_chain_is_detected(
    legacy: Database, tmp_path: Path
) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    legacy.audit.append_many([make_event(i) for i in range(10, 13)])
    tamper(legacy, 13, "改过的新记录")
    rewrite_chain(legacy)  # 重算整条 SHA-256 链和链头：P13 之前这样做发现不了
    result = verify_audit_chain(legacy)
    assert not result.ok
    assert result.first_bad_seq == 11  # 第一条 HMAC 记录（切换事件）就对不上了
    assert result.failure is ChainFailure.HASH_MISMATCH


def test_editing_an_old_record_and_recomputing_is_detected_at_the_switch(
    legacy: Database, tmp_path: Path
) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    tamper(legacy, 3, "改过的旧记录")
    rewrite_chain(legacy)
    result = verify_audit_chain(legacy)
    assert not result.ok and result.first_bad_seq == 11


def test_downgrading_to_a_pure_sha_chain_is_detected(legacy: Database, tmp_path: Path) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    tamper(legacy, 2, "改过")
    with connect(legacy) as connection:
        connection.execute("DELETE FROM audit_chain_segments")
    rewrite_chain(legacy)
    result = verify_audit_chain(legacy)
    assert not result.ok and result.failure is ChainFailure.SEGMENT_MISMATCH
    assert result.first_bad_seq == 11


def test_recomputing_with_a_guessed_key_is_detected(legacy: Database, tmp_path: Path) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    tamper(legacy, 11, "改过")
    rewrite_chain(legacy, secret=hashlib.sha256(b"guess").digest())
    result = verify_audit_chain(legacy)
    assert not result.ok and result.failure is ChainFailure.HASH_MISMATCH


def test_a_keyed_chain_cannot_be_written_without_the_key(legacy: Database, tmp_path: Path) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    path = legacy.engine.url.database
    assert path is not None
    keyless = open_database(path)
    with pytest.raises(StorageError, match="没有密钥"):
        keyless.audit.append(make_event(1))
    result = verify_audit_chain(keyless)
    assert not result.ok and result.failure is ChainFailure.KEY_UNAVAILABLE


def test_lost_key_file_starts_a_new_segment_and_reports_the_old_one(
    legacy: Database, tmp_path: Path
) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    (tmp_path / "keys" / AUDIT_KEY_FILE).unlink()
    activation = activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    assert activation.switched and activation.from_seq == 12 and activation.warnings
    legacy.audit.append(make_event(5))
    result = verify_audit_chain(legacy)
    assert not result.ok and result.failure is ChainFailure.KEY_UNAVAILABLE
    assert result.first_bad_seq == 11


def test_unreadable_key_file_is_kept_aside_and_does_not_block_startup(
    legacy: Database, tmp_path: Path
) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    (tmp_path / "keys" / INSTALL_SECRET_FILE).write_text("00" * 32)  # 换了安装密钥：解不开
    activation = activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    assert activation.switched and any("无法解密" in w for w in activation.warnings)
    assert list((tmp_path / "keys").glob(f"{AUDIT_KEY_FILE}.unreadable-*"))
    legacy.audit.append(make_event(1))  # 仍能写入审计


def test_interrupted_switch_is_completed_on_the_next_start(
    legacy: Database, tmp_path: Path
) -> None:
    """密钥已存、切换事件已写，但进程在记下起点前被杀。"""
    ring = ring_of(tmp_path)
    secret = hashlib.sha256(b"interrupted").digest()
    pending = RingEntry(key_id_of(secret), secret, None)
    ring.save([pending])
    legacy.audit.begin_segment(secret, make_event(0, action=AuditAction.AUDIT_CHAIN_KEYED))
    activation = activate_audit_hmac(legacy, ring, clock=lambda: NOW)
    assert not activation.switched and activation.from_seq == 11
    entries = ring.load()
    assert entries is not None and entries[0].from_seq == 11
    assert verify_audit_chain(legacy).ok


def test_ring_file_is_encrypted_and_never_shows_the_key(legacy: Database, tmp_path: Path) -> None:
    activate_audit_hmac(legacy, ring_of(tmp_path), clock=lambda: NOW)
    raw = (tmp_path / "keys" / AUDIT_KEY_FILE).read_bytes()
    assert raw.startswith(b"GIKP1:file:") and b"secret" not in raw
    entries = ring_of(tmp_path).load()
    assert entries is not None
    assert "secret" not in repr(entries[0]) and entries[0].key_id in repr(entries[0])


def test_changing_the_protection_method_re_encrypts_the_ring(
    legacy: Database, tmp_path: Path
) -> None:
    from gelinspect.infrastructure.security.protectors import create_key_protector

    from .fake_security import FakeSecurity

    keys = tmp_path / "keys"
    file_ring = AuditKeyRing(keys / AUDIT_KEY_FILE, create_key_protector(keys, "file"))
    activate_audit_hmac(legacy, file_ring, clock=lambda: NOW)
    keychain = create_key_protector(keys, "auto", runner=FakeSecurity(), platform="darwin")
    activation = activate_audit_hmac(
        legacy, AuditKeyRing(keys / AUDIT_KEY_FILE, keychain), clock=lambda: NOW
    )
    assert not activation.switched
    assert (keys / AUDIT_KEY_FILE).read_bytes().startswith(b"GIKP1:keychain:")
    legacy.audit.append(make_event(1))
    assert verify_audit_chain(legacy).ok
