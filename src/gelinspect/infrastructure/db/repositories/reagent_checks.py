"""试剂校验记录仓储：只追加，按批次汇总。"""

from __future__ import annotations

from sqlalchemy import func, select

from gelinspect.domain.enums import ReagentCheckStatus, ReagentKind
from gelinspect.infrastructure.db.models import ReagentAttentionDismissalRow, ReagentCheckRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    count_where,
    page_of,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    Page,
    PageRequest,
    ReagentBatchEntry,
    ReagentCheckRecord,
    ReagentCheckRepository,
)


def _to_record(row: ReagentCheckRow) -> ReagentCheckRecord:
    """ORM 行转校验记录。"""
    return ReagentCheckRecord(
        checked_at=row.checked_at,
        actor=row.actor,
        kind=ReagentKind(row.kind),
        batch_no=row.batch_no,
        product_code=row.product_code,
        status=ReagentCheckStatus(row.status),
        registered=row.registered,
        expiry_date=row.expiry_date,
        labeled_sensitivity=row.labeled_sensitivity,
        message=row.message,
        raw_code=row.raw_code,
        input_mode=row.input_mode,
        experiment_id=row.experiment_id,
        record_id=row.id,
    )


class SqlReagentCheckRepository(ReagentCheckRepository):
    """基于 SQLAlchemy 的试剂校验记录仓储。没有更新和删除方法。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def add(self, record: ReagentCheckRecord) -> None:
        """追加一条校验记录。

        Args:
            record: 校验记录。

        Raises:
            StorageBusyError: 等写锁超时，什么都没有写入。
        """
        with translate_lock_errors(), self._sessions() as session:
            session.add(
                ReagentCheckRow(
                    checked_at=record.checked_at,
                    actor=record.actor,
                    kind=record.kind.value,
                    batch_no=record.batch_no,
                    product_code=record.product_code,
                    status=record.status.value,
                    registered=record.registered,
                    expiry_date=record.expiry_date,
                    labeled_sensitivity=record.labeled_sensitivity,
                    message=record.message,
                    raw_code=record.raw_code,
                    input_mode=record.input_mode,
                    experiment_id=record.experiment_id,
                )
            )

    def list_batches(self, limit: int = 200) -> list[ReagentBatchEntry]:
        """列出已录入的批次：同一种类同一批号取最近一次校验，按最近校验时间倒序。

        Args:
            limit: 最多返回几个批次。

        Returns:
            批次汇总。
        """
        latest = (
            select(
                func.max(ReagentCheckRow.id).label("last_id"),
                func.count().label("check_count"),
            )
            .group_by(ReagentCheckRow.kind, ReagentCheckRow.batch_no)
            .subquery()
        )
        statement = (
            select(ReagentCheckRow, latest.c.check_count)
            .join(latest, ReagentCheckRow.id == latest.c.last_id)
            .order_by(ReagentCheckRow.id.desc())
            .limit(limit)
        )
        with self._sessions() as session:
            return [
                ReagentBatchEntry(
                    kind=ReagentKind(row.kind),
                    batch_no=row.batch_no,
                    product_code=row.product_code,
                    expiry_date=row.expiry_date,
                    registered=row.registered,
                    labeled_sensitivity=row.labeled_sensitivity,
                    last_checked_at=row.checked_at,
                    last_actor=row.actor,
                    check_count=int(count),
                    last_check_id=row.id,
                )
                for row, count in session.execute(statement)
            ]

    def dismiss_attention(self, kind: ReagentKind, batch_no: str, through_id: int) -> bool:
        """标记当前最新记录，保留历史及此后新增的校验。"""
        with translate_lock_errors(), self._sessions() as session:
            latest_id = session.scalar(
                select(func.max(ReagentCheckRow.id)).where(
                    ReagentCheckRow.kind == kind.value, ReagentCheckRow.batch_no == batch_no
                )
            )
            if latest_id is None or through_id <= 0 or through_id > latest_id:
                return False
            latest_id = through_id
            marker = session.get(ReagentAttentionDismissalRow, (kind.value, batch_no))
            if marker is not None and marker.through_id >= latest_id:
                return False
            if marker is None:
                session.add(
                    ReagentAttentionDismissalRow(
                        kind=kind.value, batch_no=batch_no, through_id=latest_id
                    )
                )
            else:
                marker.through_id = latest_id
            return True

    def attention_dismissed(self, kind: ReagentKind, batch_no: str) -> bool:
        """检查最新校验是否已经关闭提醒。"""
        with self._sessions() as session:
            marker = session.get(ReagentAttentionDismissalRow, (kind.value, batch_no))
            if marker is None:
                return False
            latest_id = session.scalar(
                select(func.max(ReagentCheckRow.id)).where(
                    ReagentCheckRow.kind == kind.value, ReagentCheckRow.batch_no == batch_no
                )
            )
            return latest_id is not None and latest_id <= marker.through_id

    def query(self, page: PageRequest) -> Page[ReagentCheckRecord]:
        """分页查询校验记录，最新在前。

        Args:
            page: 分页请求。

        Returns:
            一页校验记录。
        """
        statement = page_of(select(ReagentCheckRow).order_by(ReagentCheckRow.id.desc()), page)
        with self._sessions() as session:
            total = count_where(session, ReagentCheckRow, [])
            items = tuple(_to_record(row) for row in session.execute(statement).scalars())
        return Page(items=items, total=total, request=page)
