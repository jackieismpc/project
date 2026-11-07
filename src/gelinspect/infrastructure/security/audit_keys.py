"""审计哈希链的 HMAC 密钥环。

密钥环保存在 ``keys/audit-chain.key``，整个文件由 ``KeyProtector`` 保护（DPAPI、钥匙串或
安装密钥）。内容是 JSON：

.. code-block:: json

    {"version": 1, "keys": [{"id": "…16 位…", "secret": "Base64 或 null", "from_seq": 123}]}

``from_seq`` 是这把密钥开始使用的序号（切换事件的序号）；``null`` 表示密钥已生成、切换事件
还没写入（两步之间进程被杀）。``secret`` 为 ``null`` 表示密钥已丢失，只保留记录，这一段
校验时报“密钥不可用”。

:func:`activate_audit_hmac` 在启动时调用：没有分段时追加切换事件，从此用 HMAC；
数据库里的分段与密钥环不一致时**不会**改密钥环去迁就数据库（那正是篡改的迹象），
而是照常写入，由校验报告不一致。
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db import Database
from gelinspect.infrastructure.db.repositories.audit import ChainKeys, ChainSegment, key_id_of
from gelinspect.infrastructure.db.seed import SYSTEM_ACTOR
from gelinspect.infrastructure.security.protectors import (
    decode_secret,
    encode_secret,
    write_private_file,
)
from gelinspect.interfaces.security import KeyProtectionError, KeyProtector

_log = logging.getLogger(__name__)

AUDIT_KEY_FILE = "audit-chain.key"
"""密钥环文件名（放在密钥目录里）。"""

_VERSION = 1


@dataclass(frozen=True, slots=True)
class RingEntry:
    """密钥环里的一把密钥。

    Attributes:
        key_id: 密钥标识。
        secret: 密钥；丢失时为 None。
        from_seq: 开始使用的序号；切换事件还没写入时为 None。
    """

    key_id: str
    secret: bytes | None
    from_seq: int | None

    def __repr__(self) -> str:
        """不含密钥的表示，防止被打进日志。"""
        return f"RingEntry(key_id={self.key_id!r}, from_seq={self.from_seq!r})"


class AuditKeyRing:
    """密钥环文件的读写。"""

    def __init__(self, path: Path, protector: KeyProtector) -> None:
        """绑定文件和保护方式。

        Args:
            path: 密钥环文件。
            protector: 密钥保护。
        """
        self._path = path
        self._protector = protector

    @property
    def path(self) -> Path:
        """密钥环文件路径。"""
        return self._path

    def load(self) -> list[RingEntry] | None:
        """读取密钥环。

        Returns:
            密钥列表；文件不存在时为 None。

        Raises:
            KeyProtectionError: 文件无法解密或内容损坏。
        """
        if not self._path.is_file():
            return None
        try:
            raw = self._protector.unprotect(self._path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
        except OSError as exc:
            raise KeyProtectionError(f"无法读取审计链密钥 {self._path.name}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KeyProtectionError(f"审计链密钥 {self._path.name} 内容损坏") from exc
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            raise KeyProtectionError(f"审计链密钥 {self._path.name} 的版本不受支持")
        entries: list[RingEntry] = []
        for item in data.get("keys", []):
            secret_text = item.get("secret")
            from_seq = item.get("from_seq")
            entries.append(
                RingEntry(
                    key_id=str(item["id"]),
                    secret=None if secret_text is None else decode_secret(str(secret_text)),
                    from_seq=None if from_seq is None else int(from_seq),
                )
            )
        return entries

    def needs_reprotect(self) -> bool:
        """文件用的不是当前首选的保护方式（例如钥匙串可用了，或为换电脑改成了安装密钥）。

        Returns:
            需要重新加密时为 True；文件不存在时为 False。
        """
        check = getattr(self._protector, "needs_upgrade", None)
        if not callable(check) or not self._path.is_file():
            return False
        return bool(check(self._path.read_bytes()))

    def save(self, entries: list[RingEntry]) -> None:
        """加密后原子地写入密钥环。

        Args:
            entries: 全部密钥。
        """
        payload = {
            "version": _VERSION,
            "keys": [
                {
                    "id": entry.key_id,
                    "secret": None if entry.secret is None else encode_secret(entry.secret),
                    "from_seq": entry.from_seq,
                }
                for entry in entries
            ],
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        write_private_file(self._path, self._protector.protect(data))


def chain_keys_of(entries: list[RingEntry]) -> ChainKeys:
    """密钥环里已生效的分段（有起点的），按起点升序。

    Args:
        entries: 密钥环。

    Returns:
        可信分段。
    """
    active = sorted((e for e in entries if e.from_seq is not None), key=lambda e: e.from_seq or 0)
    return ChainKeys(tuple(ChainSegment(e.from_seq or 0, e.key_id, e.secret) for e in active))


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """启动时启用 HMAC 的结果。

    Attributes:
        switched: 本次是否追加了切换事件（新开一段）。
        from_seq: 当前分段的起点。
        warnings: 需要提醒管理员的问题（密钥文件丢失、无法解密等），已写日志。
    """

    switched: bool
    from_seq: int
    warnings: tuple[str, ...] = ()


def activate_audit_hmac(
    database: Database,
    ring: AuditKeyRing,
    *,
    clock: Callable[[], datetime] | None = None,
) -> ActivationResult:
    """启用审计链的 HMAC：读取或建立密钥环，必要时追加切换事件，并把密钥交给数据库。

    * 没有密钥环、数据库也没有分段（第一次启用密钥保护）：生成密钥，追加切换事件。
      之前的记录仍按 SHA-256 校验。
    * 密钥环和数据库一致：直接使用。
    * 密钥环无法解密：把原文件改名留存（``.unreadable-<时间>``），换新密钥开新的一段；
      旧的一段在校验时报“密钥不可用”。不会因此拒绝启动。
    * 密钥环丢失而数据库有分段：同上，旧分段记为密钥丢失。

    Args:
        database: 已打开的数据库。
        ring: 密钥环。
        clock: 当前时间，测试注入。

    Returns:
        启用结果。

    Raises:
        StorageBusyError: 写切换事件时等写锁超时。
    """
    now = clock or (lambda: datetime.now(UTC))
    warnings: list[str] = []
    try:
        entries = ring.load()
    except KeyProtectionError as exc:
        backup = ring.path.with_name(f"{ring.path.name}.unreadable-{now():%Y%m%d%H%M%S}")
        ring.path.rename(backup)
        warnings.append(
            f"审计链密钥无法解密（{exc}），原文件已改名为 {backup.name}，改用新密钥。"
            "更早的 HMAC 记录在恢复原密钥前无法校验"
        )
        entries = None
    if entries is not None and ring.needs_reprotect():
        ring.save(entries)
        _log.info("审计链密钥已改用新的保护方式重新加密")
    stored = database.audit.segments()
    if entries is None:
        if stored:
            warnings.append(
                "审计链密钥文件缺失，而数据库里有 HMAC 分段：改用新密钥，"
                "旧分段在校验时会报告密钥不可用"
            )
        entries = [RingEntry(key_id, None, from_seq) for from_seq, key_id in stored]
    # 两步之间被打断：密钥已存、切换事件已写，但密钥环还没记下起点。
    stored_ids = {key_id: from_seq for from_seq, key_id in stored}
    resolved = [
        replace(e, from_seq=stored_ids[e.key_id])
        if e.from_seq is None and e.key_id in stored_ids
        else e
        for e in entries
    ]
    if resolved != entries:
        ring.save(resolved)
    entries = resolved
    keys = chain_keys_of(entries)
    current = keys.current
    if current is not None and current.secret is not None:
        database.use_chain_keys(keys)
        for warning in warnings:
            _log.error("%s", warning)
        return ActivationResult(False, current.from_seq, tuple(warnings))

    pending = next((e for e in entries if e.from_seq is None and e.secret is not None), None)
    if pending is None:
        secret = secrets.token_bytes(32)
        pending = RingEntry(key_id_of(secret), secret, None)
    entries = [e for e in entries if e.from_seq is not None] + [pending]
    ring.save(entries)  # 先存密钥再写库：中途被杀也不会出现库里有分段却没有密钥
    assert pending.secret is not None
    previous = keys.segments[-1].from_seq if keys.segments else None
    reason = "首次启用" if previous is None else f"序号 {previous} 起那一段的密钥不可用，换用新密钥"
    record = database.audit.begin_segment(
        pending.secret,
        AuditEvent(
            occurred_at=now(),
            actor=SYSTEM_ACTOR,
            actor_role=None,
            action=AuditAction.AUDIT_CHAIN_KEYED,
            detail=(
                f"审计链从本条起改用 HMAC-SHA256（密钥 {pending.key_id}，{reason}）。"
                "之前的记录按原算法校验；之后没有密钥就无法重算哈希"
            ),
        ),
    )
    entries = [e if e is not pending else replace(e, from_seq=record.seq) for e in entries]
    ring.save(entries)
    database.use_chain_keys(chain_keys_of(entries))
    for warning in warnings:
        _log.error("%s", warning)
    _log.info("审计链已改用 HMAC-SHA256，起点序号 %d", record.seq)
    return ActivationResult(True, record.seq, tuple(warnings))


def read_chain_keys(keys_dir: Path, protector: KeyProtector) -> ChainKeys | None:
    """只读地取出审计链的可信分段和密钥（校验工具、测试和排错用，不写任何文件）。

    Args:
        keys_dir: 密钥目录。
        protector: 密钥保护。

    Returns:
        可信分段；没有密钥环文件时为 None。

    Raises:
        KeyProtectionError: 密钥环无法解密。
    """
    entries = AuditKeyRing(keys_dir / AUDIT_KEY_FILE, protector).load()
    return None if entries is None else chain_keys_of(entries)
