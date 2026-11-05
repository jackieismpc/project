"""审计仓储：只追加的哈希链。

每条记录保存 ``prev_hash`` 和 ``hash``。``hash`` 是对「上一条 hash + 规范化序列化的
本条内容」做 SHA-256；序号参与哈希，所以删除、重排和改动都会被发现。
链头表 ``audit_chain_head`` 记录最后一条的序号和哈希，用来发现尾部被删除。

本模块不使用密钥：能改库的人若同时重算整条链和链头，校验无法发现。
密钥与加密由 P13 负责。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from sqlalchemy import ColumnElement, String, insert, or_, select, type_coerce, update

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.infrastructure.db.columns import format_utc
from gelinspect.infrastructure.db.models import AuditChainHeadRow, AuditEventRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    count_where,
    page_of,
)
from gelinspect.interfaces.storage import (
    GENESIS_HASH,
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ChainFailure,
    ChainVerification,
    Page,
    PageRequest,
)

_VERIFY_BATCH = 5000


def canonical_payload(seq: int, event: AuditEvent) -> bytes:
    """规范化序列化：键排序、无空白、UTF-8、时间为 UTC 定长文本。

    Args:
        seq: 记录序号。
        event: 审计事件。

    Returns:
        参与哈希的字节串。
    """
    return _canonical(
        seq=seq,
        occurred_at=format_utc(event.occurred_at),
        actor=event.actor,
        actor_role=None if event.actor_role is None else event.actor_role.value,
        action=event.action.value,
        detail=event.detail,
        experiment_id=event.experiment_id,
    )


def _canonical(
    *,
    seq: int,
    occurred_at: str,
    actor: str,
    actor_role: str | None,
    action: str,
    detail: str,
    experiment_id: str | None,
) -> bytes:
    """按固定字段序列化，供写入与校验共用。"""
    payload = {
        "seq": seq,
        "occurred_at": occurred_at,
        "actor": actor,
        "actor_role": actor_role,
        "action": action,
        "detail": detail,
        "experiment_id": experiment_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_hash(prev_hash: str, canonical: bytes) -> str:
    """计算一条记录的哈希。

    Args:
        prev_hash: 上一条记录的哈希。
        canonical: 本条记录的规范化序列化。

    Returns:
        64 位小写十六进制 SHA-256。
    """
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(b"\n")
    digest.update(canonical)
    return digest.hexdigest()


def _to_record(row: AuditEventRow) -> AuditRecord:
    """ORM 行转审计记录。"""
    return AuditRecord(
        seq=row.seq,
        event=AuditEvent(
            occurred_at=row.occurred_at,
            actor=row.actor,
            actor_role=None if row.actor_role is None else Role(row.actor_role),
            action=AuditAction(row.action),
            detail=row.detail,
            experiment_id=row.experiment_id,
        ),
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def _filters(query: AuditQuery) -> list[ColumnElement[bool]]:
    """把查询条件转成过滤表达式。"""
    filters: list[ColumnElement[bool]] = []
    if query.start is not None:
        filters.append(AuditEventRow.occurred_at >= query.start)
    if query.end is not None:
        filters.append(AuditEventRow.occurred_at < query.end)
    if query.actor is not None:
        filters.append(AuditEventRow.actor == query.actor)
    if query.action is not None:
        filters.append(AuditEventRow.action == query.action.value)
    if query.experiment_id is not None:
        filters.append(AuditEventRow.experiment_id == query.experiment_id)
    if query.keyword:
        filters.append(
            or_(
                AuditEventRow.detail.icontains(query.keyword, autoescape=True),
                AuditEventRow.experiment_id.icontains(query.keyword, autoescape=True),
            )
        )
    return filters


class SqlAuditRepository(AuditRepository):
    """基于 SQLAlchemy 的审计仓储。没有更新和删除方法。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def _append(self, events: Sequence[AuditEvent]) -> list[AuditRecord]:
        """在一个事务中追加事件。

        先对链头做一次空更新取得写锁，再读取链头，保证并发写入时链不分叉。

        Args:
            events: 按顺序追加的事件。

        Returns:
            新增的记录。
        """
        records: list[AuditRecord] = []
        if not events:
            return records
        with self._sessions() as session:
            session.execute(
                update(AuditChainHeadRow)
                .where(AuditChainHeadRow.id == 1)
                .values(last_seq=AuditChainHeadRow.last_seq)
            )
            # 工作单元内多次追加共用一个会话。显式跳过身份映射的缓存，读本事务最新的链头，
            # 不依赖 ORM 批量更新对会话的同步。
            head = session.get(AuditChainHeadRow, 1, populate_existing=True)
            if head is None:
                raise RuntimeError("audit_chain_head 缺失，数据库已损坏")
            seq, prev_hash = head.last_seq, head.last_hash
            rows: list[dict[str, object]] = []
            for event in events:
                seq += 1
                current = compute_hash(prev_hash, canonical_payload(seq, event))
                rows.append(
                    {
                        "seq": seq,
                        "occurred_at": event.occurred_at,
                        "actor": event.actor,
                        "actor_role": None if event.actor_role is None else event.actor_role.value,
                        "action": event.action.value,
                        "detail": event.detail,
                        "experiment_id": event.experiment_id,
                        "prev_hash": prev_hash,
                        "hash": current,
                    }
                )
                records.append(AuditRecord(seq=seq, event=event, prev_hash=prev_hash, hash=current))
                prev_hash = current
            session.execute(insert(AuditEventRow), rows)
            session.execute(
                update(AuditChainHeadRow)
                .where(AuditChainHeadRow.id == 1)
                .values(last_seq=seq, last_hash=prev_hash)
            )
        return records

    def append(self, event: AuditEvent) -> AuditRecord:
        """追加一条事件并生成哈希链。

        Args:
            event: 审计事件。

        Returns:
            已入库的记录，含序号和哈希。
        """
        return self._append([event])[0]

    def append_many(self, events: Sequence[AuditEvent]) -> int:
        """在同一事务中按顺序追加多条事件。

        Args:
            events: 事件列表。

        Returns:
            追加的条数。
        """
        return len(self._append(events))

    def get(self, seq: int) -> AuditRecord | None:
        """按序号查询一条记录。

        Args:
            seq: 序号。

        Returns:
            记录，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(AuditEventRow, seq)
            return None if row is None else _to_record(row)

    def query(self, query: AuditQuery, page: PageRequest) -> Page[AuditRecord]:
        """分页查询，按序号倒序（最新在前）。

        Args:
            query: 查询条件。
            page: 分页请求。

        Returns:
            一页审计记录。
        """
        filters = _filters(query)
        statement = page_of(
            select(AuditEventRow).where(*filters).order_by(AuditEventRow.seq.desc()), page
        )
        with self._sessions() as session:
            total = count_where(session, AuditEventRow, filters)
            items = tuple(_to_record(row) for row in session.execute(statement).scalars())
        return Page(items=items, total=total, request=page)

    def verify_chain(self) -> ChainVerification:
        """校验整条哈希链，定位第一条被篡改或删除的记录。

        依次检查：序号连续、内容与 ``hash`` 相符、``prev_hash`` 与上一条相符，
        最后核对链头。记录被删除时，``first_bad_seq`` 是缺失的序号。

        Returns:
            校验结果。
        """
        expected_seq = 1
        prev_hash = GENESIS_HASH
        columns = (
            AuditEventRow.seq,
            type_coerce(AuditEventRow.occurred_at, String),
            AuditEventRow.actor,
            AuditEventRow.actor_role,
            AuditEventRow.action,
            AuditEventRow.detail,
            AuditEventRow.experiment_id,
            AuditEventRow.prev_hash,
            AuditEventRow.hash,
        )
        with self._sessions() as session:
            last_seen = 0
            while True:
                batch = session.execute(
                    select(*columns)
                    .where(AuditEventRow.seq >= expected_seq)
                    .order_by(AuditEventRow.seq)
                    .limit(_VERIFY_BATCH)
                ).all()
                if not batch:
                    break
                for seq, at, actor, role, action, detail, exp_id, stored_prev, stored in batch:
                    if seq != expected_seq:
                        return ChainVerification(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=expected_seq,
                            failure=ChainFailure.MISSING,
                            message=f"序号 {expected_seq} 的记录缺失",
                        )
                    if stored_prev != prev_hash:
                        return ChainVerification(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=seq,
                            failure=ChainFailure.LINK_BROKEN,
                            message=f"序号 {seq} 的 prev_hash 与上一条不符",
                        )
                    canonical = _canonical(
                        seq=seq,
                        occurred_at=at,
                        actor=actor,
                        actor_role=role,
                        action=action,
                        detail=detail,
                        experiment_id=exp_id,
                    )
                    if compute_hash(prev_hash, canonical) != stored:
                        return ChainVerification(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=seq,
                            failure=ChainFailure.HASH_MISMATCH,
                            message=f"序号 {seq} 的内容与其 hash 不符",
                        )
                    prev_hash = stored
                    last_seen = seq
                    expected_seq = seq + 1
            head = session.get(AuditChainHeadRow, 1)
        if head is None:
            return ChainVerification(
                ok=False,
                checked=last_seen,
                failure=ChainFailure.HEAD_MISMATCH,
                message="链头记录缺失",
            )
        if head.last_seq > last_seen:
            return ChainVerification(
                ok=False,
                checked=last_seen,
                first_bad_seq=last_seen + 1,
                failure=ChainFailure.MISSING,
                message=f"序号 {last_seen + 1} 及之后的记录缺失（尾部被删除）",
            )
        if head.last_seq != last_seen or head.last_hash != prev_hash:
            return ChainVerification(
                ok=False,
                checked=last_seen,
                first_bad_seq=max(min(head.last_seq + 1, last_seen), 1),
                failure=ChainFailure.HEAD_MISMATCH,
                message="链头与最后一条记录不符",
            )
        return ChainVerification(ok=True, checked=last_seen, message="哈希链完好")
