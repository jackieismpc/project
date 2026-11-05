"""Excel 导出服务：试验结果、历史列表、审计记录。

权限：导出需要 ``data_export``（管理员和分析员）；导出审计还需要 ``audit_view``；
服务层每次都重新校验。成功后写一条 ``data_exported`` 审计（导出了什么、多少行）。
文件格式和公式注入防护在 :mod:`gelinspect.services.excel_export`。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
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
from gelinspect.services.encrypted_zip import (
    MIN_PASSWORD_LENGTH,
    EncryptedZipError,
    check_password,
    write_encrypted_zip,
)
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
        path: 写出的文件（加密时是 ``.zip``）。
        rows: 主表的行数（试验结果为单管行数）。
        elapsed_s: 耗时（秒）。
        encrypted: 是否打成了带密码的 ZIP。
    """

    path: Path
    rows: int
    elapsed_s: float
    encrypted: bool = False


class ExportPolicy:
    """导出加密策略：Excel 导出和批量导出报告共用，设置保存后立即生效。线程安全。"""

    def __init__(self, encrypt: bool = False) -> None:
        """创建策略。

        Args:
            encrypt: 是否要求敏感导出加密（配置 ``exports.encrypt``）。
        """
        self._encrypt = encrypt
        self._lock = threading.Lock()

    @property
    def encrypt(self) -> bool:
        """是否要求加密。"""
        with self._lock:
            return self._encrypt

    def set_encrypt(self, value: bool) -> None:
        """修改是否要求加密。

        Args:
            value: 新值。
        """
        with self._lock:
            self._encrypt = value

    def check(self, password: str | None) -> None:
        """导出前检查密码：要求加密时必须有密码；有密码时长度要够。

        Args:
            password: 用户输入的导出密码，None 表示不加密。

        Raises:
            ServiceError: 要求加密却没有密码（``EXPORT_PASSWORD_REQUIRED``），
                或密码太短（``EXPORT_PASSWORD_WEAK``）。
        """
        if password is None:
            if self.encrypt:
                raise ServiceError(
                    "已开启导出加密，必须设置导出密码", ServiceErrorCode.EXPORT_PASSWORD_REQUIRED
                )
            return
        try:
            check_password(password)
        except EncryptedZipError as exc:
            raise ServiceError(
                str(exc), ServiceErrorCode.EXPORT_PASSWORD_WEAK, min=MIN_PASSWORD_LENGTH
            ) from exc


def encrypt_into_zip(plain: Path, destination: Path, password: str) -> Path:
    """把一个已写好的明文文件打进加密 ZIP，然后覆盖并删除明文。

    Args:
        plain: 明文文件。
        destination: 目标 ZIP。
        password: 密码。

    Returns:
        ZIP 文件路径。
    """
    try:
        return write_encrypted_zip(destination, [(plain.name, plain)], password)
    finally:
        wipe_file(plain)


def wipe_file(path: Path) -> None:
    """用随机数据覆盖后删除（尽力而为，SSD 和写时复制文件系统上不保证物理不可恢复）。

    Args:
        path: 文件，不存在时什么都不做。
    """
    try:
        size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.write(os.urandom(size))
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError:
        return
    except OSError:
        _log.warning("覆盖临时明文文件失败：%s", path.name)
    path.unlink(missing_ok=True)


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
        policy: ExportPolicy | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限。
            storage: 存储。
            clock: 返回带时区当前时间的函数，测试注入。
            policy: 导出加密策略，默认不要求加密。
        """
        self._auth = auth
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))
        self._policy = policy or ExportPolicy()

    @property
    def policy(self) -> ExportPolicy:
        """导出加密策略。"""
        return self._policy

    @property
    def encryption_required(self) -> bool:
        """当前是否要求导出加密（界面据此弹出密码框）。"""
        return self._policy.encrypt

    # ------------------------------------------------------------------ 试验结果

    def export_results(
        self,
        session: Session | None,
        experiment_ids: Sequence[str],
        destination: Path,
        labels: ExportLabels | None = None,
        *,
        password: str | None = None,
    ) -> ExportSummary:
        """导出试验结果：试验汇总、单管结果（含结论、λc、单管特征和复核）、结论依据。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            experiment_ids: 试验标识（至少一个）。
            destination: 目标文件。
            labels: 文字翻译，默认简体中文。
            password: 导出密码。给出时打成 AES-256 加密的 ZIP（``destination`` 改为 ``.zip``）；
                开启了导出加密时必须给出。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有选择试验、试验不存在、密码不符合要求，或写文件失败。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        self._policy.check(password)
        if not experiment_ids:
            raise ServiceError("没有选择要导出的试验", ServiceErrorCode.EXPORT_EMPTY)
        started = time.perf_counter()
        experiments = list(self._load(experiment_ids))
        path, rows = self._write(
            lambda dest: write_results(experiments, dest, labels), destination, password
        )
        elapsed = time.perf_counter() - started
        self._audit(
            user,
            f"导出试验结果到 Excel：{len(experiments)} 个试验，{rows} 管，文件 {path.name}"
            + _encrypted_note(password),
            experiment_ids[0] if len(experiment_ids) == 1 else None,
        )
        return ExportSummary(path, rows, elapsed, password is not None)

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
        *,
        password: str | None = None,
    ) -> ExportSummary:
        """导出历史列表：符合筛选条件的全部试验（不只当前页）。

        Args:
            session: 调用者的会话，需要 ``data_export`` 权限。
            query: 筛选条件。
            destination: 目标文件。
            labels: 文字翻译。
            password: 导出密码，同 :meth:`export_results`。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 没有符合条件的试验、密码不符合要求，或写文件失败。
        """
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        self._policy.check(password)
        started = time.perf_counter()
        items = list(self._history(query))
        if not items:
            raise ServiceError("没有符合条件的试验可导出", ServiceErrorCode.EXPORT_EMPTY)
        path, rows = self._write(
            lambda dest: write_history(items, dest, labels), destination, password
        )
        elapsed = time.perf_counter() - started
        self._audit(
            user, f"导出历史列表到 Excel：{rows} 行，文件 {path.name}" + _encrypted_note(password)
        )
        return ExportSummary(path, rows, elapsed, password is not None)

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
        *,
        password: str | None = None,
    ) -> ExportSummary:
        """导出审计记录：符合查询条件的全部记录（最新在前）。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 和 ``data_export`` 权限。
            audit_filter: 查询条件。
            destination: 目标文件。
            labels: 文字翻译。
            password: 导出密码，同 :meth:`export_results`。

        Returns:
            导出结果。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 条件不合法、没有符合条件的记录、密码不符合要求，或写文件失败。
        """
        self._auth.authorize(session, Permission.AUDIT_VIEW)
        user = self._auth.authorize(session, Permission.DATA_EXPORT)
        self._policy.check(password)
        query = audit_filter.to_query()
        started = time.perf_counter()
        records = list(self._audit_records(query))
        if not records:
            raise ServiceError("没有符合条件的审计记录可导出", ServiceErrorCode.EXPORT_EMPTY)
        path, rows = self._write(
            lambda dest: write_audit(records, dest, labels), destination, password
        )
        elapsed = time.perf_counter() - started
        self._audit(
            user,
            f"导出审计追踪到 Excel：{rows} 条，条件 {audit_filter.describe()}，文件 {path.name}"
            + _encrypted_note(password),
        )
        return ExportSummary(path, rows, elapsed, password is not None)

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
    def _write(
        call: Callable[[Path], tuple[Path, int]], destination: Path, password: str | None
    ) -> tuple[Path, int]:
        """写 Excel；有密码时先写到同目录的临时文件，再打成加密 ZIP 并覆盖删除明文。"""
        try:
            if password is None:
                return call(destination)
            target = destination.with_suffix(".zip")
            target.parent.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix=".export-", dir=target.parent))
            try:
                stem = (
                    destination.stem if destination.suffix.lower() == ".xlsx" else destination.name
                )
                plain, rows = call(work / f"{stem}.xlsx")
                return encrypt_into_zip(plain, target, password), rows
            finally:
                for leftover in work.glob("*"):
                    wipe_file(leftover)
                work.rmdir()
        except (OSError, EncryptedZipError) as error:
            raise ServiceError(
                f"写入导出文件失败：{error}", ServiceErrorCode.EXPORT_FAILED, reason=str(error)
            ) from error

    def _audit(self, user: User, detail: str, experiment_id: str | None = None) -> None:
        """写 ``data_exported`` 审计（数据库忙时只记日志）。"""
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


def _encrypted_note(password: str | None) -> str:
    """审计里注明是否加密（不记录密码）。"""
    return "，已加密（AES-256 ZIP）" if password is not None else ""
