"""审计仓储：只追加的哈希链。

每条记录保存 ``prev_hash`` 和 ``hash``。``hash`` 是对“上一条 hash + 规范化序列化的
本条内容”做 SHA-256 或 HMAC-SHA256；序号参与哈希，所以删除、重排和改动都会被发现。
链头表 ``audit_chain_head`` 记录最后一条的序号和哈希，用来发现尾部被删除。

**分段**：只用 SHA-256 时，能改库的人重算整条链和链头，校验就发现不了。所以链可以从
某个序号起改用 HMAC-SHA256，密钥由 ``KeyProtector`` 保护、不在数据库里（见
``infrastructure/security/audit_keys.py``）。``audit_chain_segments`` 记录每段的起点和密钥标识，
校验时以密钥文件（受系统凭据保护）里的分段为准：

* 起点之前的旧记录仍按 SHA-256 校验（兼容旧链）；起点那条是切换事件，它的 ``prev_hash``
  链接着旧链的最后一条，所以旧记录被改动同样会被发现。
* 数据库里的分段与密钥文件不符（被降级回 SHA-256、切换点被挪动）报 ``segment_mismatch``。
* 某段的密钥不可用报 ``key_unavailable``。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, String, insert, or_, select, type_coerce, update
from sqlalchemy.orm import Session

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.infrastructure.db.columns import format_utc
from gelinspect.infrastructure.db.models import (
    AuditChainHeadRow,
    AuditChainSegmentRow,
    AuditEventRow,
)
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    count_where,
    page_of,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    GENESIS_HASH,
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ChainFailure,
    ChainType,
    ChainVerification,
    Page,
    PageRequest,
    StorageError,
)

_VERIFY_BATCH = 5000

HMAC_ALGORITHM = "hmac-sha256"
"""分段表里记录的算法名。"""


@dataclass(frozen=True, slots=True)
class ChainSegment:
    """一段 HMAC 链。

    Attributes:
        from_seq: 起点序号（切换事件的序号）。
        key_id: 密钥标识（密钥 SHA-256 的前 16 位十六进制），可以公开。
        secret: HMAC 密钥；丢失时为 None（这一段无法校验，也不能再写入）。
    """

    from_seq: int
    key_id: str
    secret: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ChainKeys:
    """可信的分段（来自受保护的密钥文件），按起点升序。

    Attributes:
        segments: 全部分段。
    """

    segments: tuple[ChainSegment, ...]

    @property
    def current(self) -> ChainSegment | None:
        """最后一段，新记录用它的密钥；没有分段时为 None。"""
        return self.segments[-1] if self.segments else None

    def segment_for(self, seq: int) -> ChainSegment | None:
        """某个序号所在的分段；在第一段之前（SHA-256 部分）为 None。

        Args:
            seq: 序号。

        Returns:
            分段或 None。
        """
        found: ChainSegment | None = None
        for segment in self.segments:
            if segment.from_seq <= seq:
                found = segment
            else:
                break
        return found


def key_id_of(secret: bytes) -> str:
    """密钥标识：密钥 SHA-256 的前 16 位十六进制。

    Args:
        secret: 密钥。

    Returns:
        标识。
    """
    return hashlib.sha256(b"gelinspect-audit-key-id\n" + secret).hexdigest()[:16]


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


def compute_hmac(secret: bytes, prev_hash: str, canonical: bytes) -> str:
    """用密钥计算一条记录的哈希（HMAC-SHA256，输入与 :func:`compute_hash` 相同）。

    Args:
        secret: HMAC 密钥。
        prev_hash: 上一条记录的哈希。
        canonical: 本条记录的规范化序列化。

    Returns:
        64 位小写十六进制。
    """
    mac = hmac.new(secret, digestmod=hashlib.sha256)
    mac.update(prev_hash.encode("ascii"))
    mac.update(b"\n")
    mac.update(canonical)
    return mac.hexdigest()


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
    if query.max_seq is not None:
        filters.append(AuditEventRow.seq <= query.max_seq)
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

    def __init__(self, sessions: SessionProvider, keys: ChainKeys | None = None) -> None:
        """记录会话提供者和可信的分段。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
            keys: 可信的 HMAC 分段（来自受保护的密钥文件）；None 表示没有密钥，
                只能读写纯 SHA-256 链。
        """
        self._sessions = sessions
        self._keys = keys

    @staticmethod
    def _lock_head(session: Session) -> AuditChainHeadRow:
        """对链头做一次空更新取得写锁，再读取最新的链头。"""
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
        return head

    def _writer_secret(self, session: Session) -> bytes | None:
        """新记录用的 HMAC 密钥；纯 SHA-256 链为 None。

        Raises:
            StorageError: 数据库已切换到 HMAC，但没有可用的密钥。
        """
        current = None if self._keys is None else self._keys.current
        if current is not None:
            if current.secret is None:
                raise StorageError("审计链当前分段的密钥不可用，不能写入审计记录")
            return current.secret
        keyed = session.scalar(select(AuditChainSegmentRow.from_seq).limit(1))
        if keyed is not None:
            raise StorageError("审计链已改用 HMAC-SHA256，没有密钥不能写入审计记录")
        return None

    @staticmethod
    def _row(seq: int, event: AuditEvent, prev_hash: str, current: str) -> dict[str, object]:
        return {
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

    def _append(self, events: Sequence[AuditEvent]) -> list[AuditRecord]:
        """在一个事务中追加事件。

        先对链头做一次空更新取得写锁，再读取链头，保证并发写入时链不分叉。

        Args:
            events: 按顺序追加的事件。

        Returns:
            新增的记录。

        Raises:
            StorageBusyError: 等写锁超时，什么都没有写入，可以稍后重试。
            StorageError: 链已改用 HMAC 但没有密钥。
        """
        records: list[AuditRecord] = []
        if not events:
            return records
        with translate_lock_errors(), self._sessions() as session:
            head = self._lock_head(session)
            secret = self._writer_secret(session)
            seq, prev_hash = head.last_seq, head.last_hash
            rows: list[dict[str, object]] = []
            for event in events:
                seq += 1
                canonical = canonical_payload(seq, event)
                current = (
                    compute_hash(prev_hash, canonical)
                    if secret is None
                    else compute_hmac(secret, prev_hash, canonical)
                )
                rows.append(self._row(seq, event, prev_hash, current))
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

    def segments(self) -> list[tuple[int, str]]:
        """数据库里记录的 HMAC 分段（起点序号、密钥标识），按起点升序。

        Returns:
            分段列表；纯 SHA-256 链为空。
        """
        with self._sessions() as session:
            rows = session.execute(
                select(AuditChainSegmentRow.from_seq, AuditChainSegmentRow.key_id).order_by(
                    AuditChainSegmentRow.from_seq
                )
            ).all()
        return [(int(from_seq), str(key_id)) for from_seq, key_id in rows]

    def begin_segment(self, secret: bytes, event: AuditEvent) -> AuditRecord:
        """开始新的一段 HMAC 链：在同一事务里追加切换事件并记录分段。

        切换事件本身就用新密钥计算，它的 ``prev_hash`` 链接上一条（SHA-256 或旧密钥），
        所以之前的记录被改动也会在这里断开。

        Args:
            secret: 新密钥（32 字节）。
            event: 切换事件，说明为什么切换。

        Returns:
            切换事件的记录；它的序号就是新分段的起点。

        Raises:
            StorageBusyError: 等写锁超时。
        """
        with translate_lock_errors(), self._sessions() as session:
            head = self._lock_head(session)
            seq = head.last_seq + 1
            current = compute_hmac(secret, head.last_hash, canonical_payload(seq, event))
            session.execute(insert(AuditEventRow), [self._row(seq, event, head.last_hash, current)])
            session.execute(
                insert(AuditChainSegmentRow).values(
                    from_seq=seq, key_id=key_id_of(secret), algorithm=HMAC_ALGORITHM
                )
            )
            session.execute(
                update(AuditChainHeadRow)
                .where(AuditChainHeadRow.id == 1)
                .values(last_seq=seq, last_hash=current)
            )
            return AuditRecord(seq=seq, event=event, prev_hash=head.last_hash, hash=current)

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

    def latest_seq(self) -> int:
        """当前已提交的最大序号（链头记录的序号）。

        Returns:
            最大序号；还没有任何记录时为 0。
        """
        with self._sessions() as session:
            last = session.scalar(
                select(AuditChainHeadRow.last_seq).where(AuditChainHeadRow.id == 1)
            )
        return int(last or 0)

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

    def _trusted_segments(self, stored: list[tuple[int, str]]) -> tuple[ChainKeys, int | None]:
        """校验用的分段和第一处不一致的起点。

        有密钥文件时以它为准，数据库里的副本与之不符就记下第一处不一致；
        没有密钥时只能用数据库里的分段（全部视为密钥不可用）。
        """
        if self._keys is None:
            return ChainKeys(tuple(ChainSegment(f, k) for f, k in stored)), None
        trusted = [(s.from_seq, s.key_id) for s in self._keys.segments]
        if trusted == stored:
            return self._keys, None
        differing = sorted(set(trusted) ^ set(stored))
        return self._keys, differing[0][0]

    def verify_chain(self, progress: Callable[[int], None] | None = None) -> ChainVerification:
        """校验整条哈希链，定位第一条被篡改或删除的记录，并报告链的类型。

        依次检查：分段与密钥文件一致、序号连续、内容与 ``hash`` 相符（按所在分段用 SHA-256
        或 HMAC-SHA256）、``prev_hash`` 与上一条相符，最后核对链头。记录被删除时，
        ``first_bad_seq`` 是缺失的序号。

        Args:
            progress: 每校验完一批记录后调用，参数是已校验到的序号；在调用本方法的线程上执行。

        Returns:
            校验结果。
        """
        stored = self.segments()
        keys, mismatch_at = self._trusted_segments(stored)
        first_from = keys.segments[0].from_seq if keys.segments else None
        if first_from is None:
            chain_type = ChainType.SHA256
        elif first_from == 1:
            chain_type = ChainType.HMAC_SHA256
        else:
            chain_type = ChainType.MIXED

        def result(
            *,
            ok: bool,
            checked: int,
            message: str,
            first_bad_seq: int | None = None,
            failure: ChainFailure | None = None,
        ) -> ChainVerification:
            return ChainVerification(
                ok=ok,
                checked=checked,
                first_bad_seq=first_bad_seq,
                failure=failure,
                message=message,
                chain_type=chain_type,
                hmac_from_seq=first_from,
            )

        if mismatch_at is not None:
            return result(
                ok=False,
                checked=0,
                first_bad_seq=mismatch_at,
                failure=ChainFailure.SEGMENT_MISMATCH,
                message=(
                    f"序号 {mismatch_at} 处的链类型与密钥文件的记录不符："
                    "链可能被降级回 SHA-256 后重算，或切换点被伪造"
                ),
            )
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
                for seq, at, actor, role, action, detail, exp_id, stored_prev, stored_hash in batch:
                    if seq != expected_seq:
                        return result(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=expected_seq,
                            failure=ChainFailure.MISSING,
                            message=f"序号 {expected_seq} 的记录缺失",
                        )
                    if stored_prev != prev_hash:
                        return result(
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
                    segment = keys.segment_for(seq)
                    if segment is None:
                        expected = compute_hash(prev_hash, canonical)
                    elif segment.secret is None:
                        return result(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=segment.from_seq,
                            failure=ChainFailure.KEY_UNAVAILABLE,
                            message=(
                                f"序号 {segment.from_seq} 起的记录用 HMAC 计算，"
                                f"但密钥 {segment.key_id} 不可用，无法校验"
                            ),
                        )
                    else:
                        expected = compute_hmac(segment.secret, prev_hash, canonical)
                    if not hmac.compare_digest(expected, stored_hash):
                        return result(
                            ok=False,
                            checked=last_seen,
                            first_bad_seq=seq,
                            failure=ChainFailure.HASH_MISMATCH,
                            message=f"序号 {seq} 的内容与其 hash 不符",
                        )
                    prev_hash = stored_hash
                    last_seen = seq
                    expected_seq = seq + 1
                if progress is not None:
                    progress(last_seen)
            head = session.get(AuditChainHeadRow, 1)
        if head is None:
            return result(
                ok=False,
                checked=last_seen,
                failure=ChainFailure.HEAD_MISMATCH,
                message="链头记录缺失",
            )
        if head.last_seq > last_seen:
            return result(
                ok=False,
                checked=last_seen,
                first_bad_seq=last_seen + 1,
                failure=ChainFailure.MISSING,
                message=f"序号 {last_seen + 1} 及之后的记录缺失（尾部被删除）",
            )
        if head.last_seq != last_seen or head.last_hash != prev_hash:
            return result(
                ok=False,
                checked=last_seen,
                first_bad_seq=max(min(head.last_seq + 1, last_seen), 1),
                failure=ChainFailure.HEAD_MISMATCH,
                message="链头与最后一条记录不符",
            )
        if keys.segments and keys.segments[-1].from_seq > last_seen:
            return result(
                ok=False,
                checked=last_seen,
                first_bad_seq=keys.segments[-1].from_seq,
                failure=ChainFailure.MISSING,
                message=f"密钥文件记录的切换事件（序号 {keys.segments[-1].from_seq}）缺失",
            )
        return result(ok=True, checked=last_seen, message="哈希链完好")
