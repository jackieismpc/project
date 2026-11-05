"""Excel 导出服务：试验结果、历史列表、审计记录。

权限：导出需要 ``data_export``（管理员和分析员）；导出审计还需要 ``audit_view``；
服务层每次都重新校验。成功后写一条 ``data_exported`` 审计（导出了什么、多少行）。
文件格式和公式注入防护在 :mod:`gelinspect.services.excel_export`。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from gelinspect.domain.entities import AuditEvent, Experiment, User
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ExperimentQuery,
    ExperimentRepository,
    ExperimentSummary,
    PageRequest,
    StorageBusyError,
)
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.excel_export import (
    ExportLabels,
    safe_filename,
    write_audit,
    write_history,
    write_results,
)

_log = logging.getLogger(__name__)

_PAGE = 1000
MAX_EXPORT_ROWS = 500_000
"""单次导出的行数上限（Excel 单表最多约 104 万行，留足余量）。"""


class ExportStorage(Protocol):
    """导出需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...


@dataclass(frozen=True, slots=True)
class ExportSummary:
    """一次导出的结果。

    Attributes:
        path: 写出的文件。
        rows: 主表的行数（试验结果为单管行数）。
        elapsed_s: 耗时（秒）。
    """

    path: Path
    rows: int
    elapsed_s: float


def suggested_filename(kind: str, *, now: datetime | None = None) -> str:
    """保存对话框里建议的文件名，已处理成各平台都安全的形式。

    Args:
        kind: 内容名称，例如 ``results``、``audit``。
        now: 时间戳来源，默认当前时间。

    Returns:
        形如 ``results-20261106-093000.xlsx``。
    """
    stamp = (now or datetime.now(UTC)).astimezone().strftime("%Y%m%d-%H%M%S")
    return safe_filename(f"{kind}-{stamp}")


class ExportService:
    """导出服务。耗时的导出由界面放到后台线程调用。"""

    def __init__(
        self,
        auth: AuthService,
        storage: ExportStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限。
            storage: 存储。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ 试验结果

    def export_results(
        self,
        session: Session | None,
        experiment_ids: Sequence[str],
        destination: Path,
        labels: ExportLabels | None = None,
    ) -> ExportSummary:
        """导出试验结果：试验汇总、单管结果（含结论、λc、单管特征和复核）、结论依据。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            experiment_ids: 试验标识（至少一个）。
            destination: 目标文件。
            labels: 文字翻译，默认简体中文。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有选择试验、试验不存在，或写文件失败。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        if not experiment_ids:
            raise ServiceError("没有选择要导出的试验", ServiceErrorCode.EXPORT_EMPTY)
        started = time.perf_counter()
        experiments = list(self._load(experiment_ids))
        path, rows = self._write(lambda: write_results(experiments, destination, labels))
        elapsed = time.perf_counter() - started
        self._audit(
            user,
            f"导出试验结果到 Excel：{len(experiments)} 个试验，{rows} 管，文件 {path.name}",
            experiment_ids[0] if len(experiment_ids) == 1 else None,
        )
        return ExportSummary(path, rows, elapsed)

    def _load(self, experiment_ids: Sequence[str]) -> Iterator[Experiment]:
        for experiment_id in dict.fromkeys(experiment_ids):
            experiment = self._storage.experiments.get(experiment_id)
            if experiment is None:
                raise ServiceError(
                    f"试验不存在：{experiment_id}",
                    ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                    id=experiment_id,
                )
            yield experiment

    # ------------------------------------------------------------------ 历史列表

    def export_history(
        self,
        session: Session | None,
        query: ExperimentQuery,
        destination: Path,
        labels: ExportLabels | None = None,
    ) -> ExportSummary:
        """导出历史列表：符合筛选条件的全部试验（不只当前页）。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            query: 筛选条件。
            destination: 目标文件。
            labels: 文字翻译。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有符合条件的试验，或写文件失败。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        started = time.perf_counter()
        items = list(self._history(query))
        if not items:
            raise ServiceError("没有符合条件的试验可导出", ServiceErrorCode.EXPORT_EMPTY)
        path, rows = self._write(lambda: write_history(items, destination, labels))
        elapsed = time.perf_counter() - started
        self._audit(user, f"导出历史列表到 Excel：{rows} 行，文件 {path.name}")
        return ExportSummary(path, rows, elapsed)

    def _history(self, query: ExperimentQuery) -> Iterator[ExperimentSummary]:
        offset = 0
        while offset < MAX_EXPORT_ROWS:
            page = self._storage.experiments.query(query, PageRequest(offset=offset, limit=_PAGE))
            yield from page.items
            offset += _PAGE
            if offset >= page.total:
                return

    # ------------------------------------------------------------------ 审计

    def export_audit(
        self,
        session: Session | None,
        audit_filter: AuditFilter,
        destination: Path,
        labels: ExportLabels | None = None,
    ) -> ExportSummary:
        """导出审计记录：符合查询条件的全部记录（最新在前）。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 和 ``data_export`` 权限。
            audit_filter: 查询条件。
            destination: 目标文件。
            labels: 文字翻译。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 条件不合法、没有符合条件的记录，或写文件失败。
        """
        self._auth.authorize(session, Permission.AUDIT_VIEW)
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        query = audit_filter.to_query()
        started = time.perf_counter()
        records = list(self._audit_records(query))
        if not records:
            raise ServiceError("没有符合条件的审计记录可导出", ServiceErrorCode.EXPORT_EMPTY)
        path, rows = self._write(lambda: write_audit(records, destination, labels))
        elapsed = time.perf_counter() - started
        self._audit(
            user,
            f"导出审计追踪到 Excel：{rows} 条，条件 {audit_filter.describe()}，文件 {path.name}",
        )
        return ExportSummary(path, rows, elapsed)

    def _audit_records(self, query: AuditQuery) -> Iterator[AuditRecord]:
        """按序号往回取，每页从上一页最后一条之前开始：不用越来越大的 OFFSET，10 万条也是线性的。"""
        bound = query.max_seq
        fetched = 0
        while fetched < MAX_EXPORT_ROWS:
            page = self._storage.audit.query(
                replace(query, max_seq=bound), PageRequest(offset=0, limit=_PAGE)
            )
            yield from page.items
            fetched += len(page.items)
            if len(page.items) < _PAGE:
                return
            bound = page.items[-1].seq - 1

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _write(call: Callable[[], tuple[Path, int]]) -> tuple[Path, int]:
        try:
            return call()
        except OSError as error:
            raise ServiceError(
                f"写入 Excel 文件失败：{error}", ServiceErrorCode.EXPORT_FAILED, reason=str(error)
            ) from error

    def _audit(self, user: User, detail: str, experiment_id: str | None = None) -> None:
        try:
            self._storage.audit.append(
                AuditEvent(
                    occurred_at=self._clock(),
                    actor=user.username,
                    actor_role=user.role,
                    action=AuditAction.DATA_EXPORTED,
                    detail=detail,
                    experiment_id=experiment_id,
                )
            )
        except StorageBusyError:
            _log.error("数据库正忙，导出审计缺失：%s", detail)
