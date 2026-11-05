"""温度日志与设备日志仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, insert, select
from sqlalchemy.exc import IntegrityError

from gelinspect.infrastructure.db.models import DeviceLogRow, TemperatureLogRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    count_where,
    page_of,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    DeviceLogEntry,
    NotFoundError,
    Page,
    PageRequest,
    TelemetryRepository,
    TemperatureSample,
)


class SqlTelemetryRepository(TelemetryRepository):
    """基于 SQLAlchemy 的温度与设备日志仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def add_temperatures(self, samples: Sequence[TemperatureSample]) -> int:
        """批量写入温度采样。

        Args:
            samples: 采样列表。

        Returns:
            写入条数。

        Raises:
            NotFoundError: 关联的试验不存在。
        """
        if not samples:
            return 0
        try:
            with translate_lock_errors(), self._sessions() as session:
                session.execute(
                    insert(TemperatureLogRow),
                    [
                        {
                            "experiment_id": s.experiment_id,
                            "recorded_at": s.recorded_at,
                            "channel": s.channel,
                            "celsius": s.celsius,
                        }
                        for s in samples
                    ],
                )
        except IntegrityError as error:
            raise NotFoundError("温度采样关联的试验不存在") from error
        return len(samples)

    def query_temperatures(
        self,
        *,
        experiment_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: PageRequest,
    ) -> Page[TemperatureSample]:
        """分页查询温度采样，按时间正序。

        Args:
            experiment_id: 试验过滤。
            start: 起始时间（含）。
            end: 结束时间（不含）。
            page: 分页请求。

        Returns:
            一页采样。
        """
        filters: list[ColumnElement[bool]] = []
        if experiment_id is not None:
            filters.append(TemperatureLogRow.experiment_id == experiment_id)
        if start is not None:
            filters.append(TemperatureLogRow.recorded_at >= start)
        if end is not None:
            filters.append(TemperatureLogRow.recorded_at < end)
        statement = page_of(
            select(TemperatureLogRow)
            .where(*filters)
            .order_by(TemperatureLogRow.recorded_at, TemperatureLogRow.id),
            page,
        )
        with self._sessions() as session:
            total = count_where(session, TemperatureLogRow, filters)
            items = tuple(
                TemperatureSample(
                    recorded_at=row.recorded_at,
                    channel=row.channel,
                    celsius=row.celsius,
                    experiment_id=row.experiment_id,
                )
                for row in session.execute(statement).scalars()
            )
        return Page(items=items, total=total, request=page)

    def add_device_logs(self, entries: Sequence[DeviceLogEntry]) -> int:
        """批量写入设备日志。

        Args:
            entries: 日志列表。

        Returns:
            写入条数。

        Raises:
            NotFoundError: 关联的试验不存在。
        """
        if not entries:
            return 0
        try:
            with translate_lock_errors(), self._sessions() as session:
                session.execute(
                    insert(DeviceLogRow),
                    [
                        {
                            "experiment_id": e.experiment_id,
                            "recorded_at": e.recorded_at,
                            "level": e.level,
                            "source": e.source,
                            "message": e.message,
                        }
                        for e in entries
                    ],
                )
        except IntegrityError as error:
            raise NotFoundError("设备日志关联的试验不存在") from error
        return len(entries)

    def query_device_logs(
        self,
        *,
        experiment_id: str | None = None,
        level: str | None = None,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: PageRequest,
    ) -> Page[DeviceLogEntry]:
        """分页查询设备日志，按时间倒序。

        Args:
            experiment_id: 试验过滤。
            level: 级别过滤。
            keyword: 在来源和内容中做子串匹配。
            start: 起始时间（含）。
            end: 结束时间（不含）。
            page: 分页请求。

        Returns:
            一页日志。
        """
        filters: list[ColumnElement[bool]] = []
        if experiment_id is not None:
            filters.append(DeviceLogRow.experiment_id == experiment_id)
        if level is not None:
            filters.append(DeviceLogRow.level == level)
        if keyword:
            filters.append(
                DeviceLogRow.message.icontains(keyword, autoescape=True)
                | DeviceLogRow.source.icontains(keyword, autoescape=True)
            )
        if start is not None:
            filters.append(DeviceLogRow.recorded_at >= start)
        if end is not None:
            filters.append(DeviceLogRow.recorded_at < end)
        statement = page_of(
            select(DeviceLogRow)
            .where(*filters)
            .order_by(DeviceLogRow.recorded_at.desc(), DeviceLogRow.id.desc()),
            page,
        )
        with self._sessions() as session:
            total = count_where(session, DeviceLogRow, filters)
            items = tuple(
                DeviceLogEntry(
                    recorded_at=row.recorded_at,
                    level=row.level,
                    source=row.source,
                    message=row.message,
                    experiment_id=row.experiment_id,
                )
                for row in session.execute(statement).scalars()
            )
        return Page(items=items, total=total, request=page)
