"""视频、图像与报告的文件记录仓储。数据库只存路径与 SHA-256，不存文件内容。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gelinspect.infrastructure.db.models import MediaFileRow, ReportRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    DuplicateError,
    MediaFile,
    MediaKind,
    MediaRepository,
    NotFoundError,
    ReportRecord,
    ReportRepository,
)

_HEX = frozenset("0123456789abcdef")


def _require_sha256(value: str) -> None:
    """校验 SHA-256 为 64 位小写十六进制。

    Args:
        value: 待校验的文本。

    Raises:
        ValueError: 格式不符。
    """
    if len(value) != 64 or not set(value) <= _HEX:
        raise ValueError("sha256 应为 64 位小写十六进制")


def _translate(error: IntegrityError, what: str) -> Exception:
    """把完整性错误转换为存储层异常。

    Args:
        error: SQLAlchemy 抛出的完整性错误。
        what: 记录的描述。

    Returns:
        外键失败时为 :class:`NotFoundError`，其余为 :class:`DuplicateError`。
    """
    if "FOREIGN KEY" in str(error.orig).upper():
        return NotFoundError(f"{what} 关联的试验不存在")
    return DuplicateError(f"{what} 已存在")


class SqlMediaRepository(MediaRepository):
    """基于 SQLAlchemy 的媒体文件记录仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def add(self, media: MediaFile) -> None:
        """新增记录。

        Args:
            media: 文件记录。

        Raises:
            DuplicateError: 文件标识已存在。
            NotFoundError: 试验不存在。
            ValueError: ``sha256`` 格式不符，或排号超出 1–10。
        """
        _require_sha256(media.sha256)
        if media.row_index is not None and not 1 <= media.row_index <= 10:
            raise ValueError(f"排号应在 1–10 内：{media.row_index}")
        try:
            with translate_lock_errors(), self._sessions() as session:
                session.add(
                    MediaFileRow(
                        file_id=media.file_id,
                        experiment_id=media.experiment_id,
                        kind=media.kind.value,
                        path=media.path,
                        sha256=media.sha256,
                        row_index=media.row_index,
                        size_bytes=media.size_bytes,
                        created_at=media.created_at,
                    )
                )
        except IntegrityError as error:
            raise _translate(error, f"文件 {media.file_id}") from error

    @staticmethod
    def _to_media(row: MediaFileRow) -> MediaFile:
        """ORM 行转文件记录。"""
        return MediaFile(
            file_id=row.file_id,
            experiment_id=row.experiment_id,
            kind=MediaKind(row.kind),
            path=row.path,
            sha256=row.sha256,
            created_at=row.created_at,
            row_index=row.row_index,
            size_bytes=row.size_bytes,
        )

    def get(self, file_id: str) -> MediaFile | None:
        """按标识查询。

        Args:
            file_id: 文件标识。

        Returns:
            文件记录，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(MediaFileRow, file_id)
            return None if row is None else self._to_media(row)

    def list_for_experiment(
        self,
        experiment_id: str,
        *,
        kind: MediaKind | None = None,
        row_index: int | None = None,
    ) -> list[MediaFile]:
        """列出试验的文件，按排号和记录时间排序。

        Args:
            experiment_id: 试验标识。
            kind: 种类过滤。
            row_index: 排号过滤。

        Returns:
            文件记录列表。
        """
        statement = (
            select(MediaFileRow)
            .where(MediaFileRow.experiment_id == experiment_id)
            .order_by(MediaFileRow.row_index, MediaFileRow.created_at, MediaFileRow.file_id)
        )
        if kind is not None:
            statement = statement.where(MediaFileRow.kind == kind.value)
        if row_index is not None:
            statement = statement.where(MediaFileRow.row_index == row_index)
        with self._sessions() as session:
            return [self._to_media(row) for row in session.execute(statement).scalars()]


class SqlReportRepository(ReportRepository):
    """基于 SQLAlchemy 的报告记录仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    @staticmethod
    def _to_report(row: ReportRow) -> ReportRecord:
        """ORM 行转报告记录。"""
        return ReportRecord(
            report_id=row.report_id,
            experiment_id=row.experiment_id,
            path=row.path,
            sha256=row.sha256,
            created_by=row.created_by,
            created_at=row.created_at,
            signed=row.signed,
        )

    def add(self, report: ReportRecord) -> None:
        """新增记录。

        Args:
            report: 报告记录。

        Raises:
            DuplicateError: 报告标识已存在。
            NotFoundError: 试验不存在。
            ValueError: ``sha256`` 格式不符。
        """
        _require_sha256(report.sha256)
        try:
            with translate_lock_errors(), self._sessions() as session:
                session.add(
                    ReportRow(
                        report_id=report.report_id,
                        experiment_id=report.experiment_id,
                        path=report.path,
                        sha256=report.sha256,
                        created_by=report.created_by,
                        created_at=report.created_at,
                        signed=report.signed,
                    )
                )
        except IntegrityError as error:
            raise _translate(error, f"报告 {report.report_id}") from error

    def get(self, report_id: str) -> ReportRecord | None:
        """按标识查询。

        Args:
            report_id: 报告标识。

        Returns:
            报告记录，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(ReportRow, report_id)
            return None if row is None else self._to_report(row)

    def list_for_experiment(self, experiment_id: str) -> list[ReportRecord]:
        """列出试验的报告，按生成时间排序。

        Args:
            experiment_id: 试验标识。

        Returns:
            报告记录列表。
        """
        with self._sessions() as session:
            rows = session.execute(
                select(ReportRow)
                .where(ReportRow.experiment_id == experiment_id)
                .order_by(ReportRow.created_at, ReportRow.report_id)
            ).scalars()
            return [self._to_report(row) for row in rows]
