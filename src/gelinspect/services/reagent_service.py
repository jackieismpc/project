"""试剂溯源：扫码解析、备案校验、锁定判断和校验记录。

扫码内容经 ``interfaces/traceability.py`` 的解析器和备案清单处理，二者由配置注入
（目前是 ``simulation/`` 里的模拟实现，格式和清单待甲方提供）。校验的结论只有一个口径：
:attr:`ScanOutcome.VALID` 才放行，其余（过期、未备案、有效期未知、清单不可用、没有配置）
都锁定流程，**绝不默认通过**。

向导里的每次扫码都在同一事务里写校验记录和审计；``ExperimentService`` 在创建和启动试验时
会用同一套逻辑再校验一次，所以界面被绕过也不会放行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol

from gelinspect.domain.entities import AuditEvent, ReagentBatch, User
from gelinspect.domain.enums import AuditAction, ReagentCheckStatus, ReagentKind
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import (
    AuditRepository,
    Page,
    PageRequest,
    ReagentBatchEntry,
    ReagentCheckRecord,
    ReagentCheckRepository,
    UnitOfWork,
)
from gelinspect.interfaces.traceability import (
    GovernmentTraceabilityClient,
    ReagentCode,
    ReagentCodeFormatError,
    ReagentCodeParser,
    ReagentRegistry,
    ReagentRegistryUnavailableError,
    SampleCodeProvider,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode

_log = logging.getLogger(__name__)

KIND_NAMES: Mapping[ReagentKind, str] = {
    ReagentKind.LAL_REAGENT: "鲎试剂",
    ReagentKind.ENDOTOXIN: "内毒素",
    ReagentKind.WATER: "检查用水",
}
"""试剂种类的简体中文名，写审计用；界面按当前语言另行翻译。"""

EXPIRING_SOON_DAYS = 30
"""剩余天数不超过它时，试剂溯源页提示「即将到期」。"""


class ScanOutcome(StrEnum):
    """一次扫码校验的结论。"""

    VALID = "valid"
    EXPIRED = "expired"
    NOT_REGISTERED = "not_registered"
    EXPIRY_UNKNOWN = "expiry_unknown"
    KIND_MISMATCH = "kind_mismatch"
    UNREADABLE = "unreadable"
    REGISTRY_UNAVAILABLE = "registry_unavailable"
    NOT_CONFIGURED = "not_configured"


LOCKING_OUTCOMES: frozenset[ScanOutcome] = frozenset(
    {
        ScanOutcome.EXPIRED,
        ScanOutcome.NOT_REGISTERED,
        ScanOutcome.EXPIRY_UNKNOWN,
        ScanOutcome.REGISTRY_UNAVAILABLE,
        ScanOutcome.NOT_CONFIGURED,
    }
)
"""会锁定流程的结论：试剂本身不合格，或无法确认合格。读不出码、种类放错只提示重扫，不锁定。"""

_STATUS_TO_OUTCOME: Mapping[ReagentCheckStatus, ScanOutcome] = {
    ReagentCheckStatus.VALID: ScanOutcome.VALID,
    ReagentCheckStatus.EXPIRED: ScanOutcome.EXPIRED,
    ReagentCheckStatus.NOT_REGISTERED: ScanOutcome.NOT_REGISTERED,
    ReagentCheckStatus.EXPIRY_UNKNOWN: ScanOutcome.EXPIRY_UNKNOWN,
    ReagentCheckStatus.MISSING: ScanOutcome.NOT_CONFIGURED,
}


@dataclass(frozen=True, slots=True)
class ReagentScan:
    """一次扫码校验的结果。

    Attributes:
        raw: 扫码原文。
        outcome: 结论。
        message: 简体中文说明（审计用），界面按结论重新生成当前语言的文字。
        kind: 试剂种类，读不出码时为期望的种类或 None。
        batch_no: 批号，读不出码时为 None。
        product_code: 产品编码。
        expiry_date: 有效期，未知时为 None。
        days_remaining: 距有效期的天数（含当天为 0，已过期为负），未知时为 None。
        labeled_sensitivity: 标示灵敏度，仅鲎试剂有。
        registered: 备案清单中是否有该批次。
    """

    raw: str
    outcome: ScanOutcome
    message: str
    kind: ReagentKind | None = None
    batch_no: str | None = None
    product_code: str | None = None
    expiry_date: date | None = None
    days_remaining: int | None = None
    labeled_sensitivity: float | None = None
    registered: bool = False

    @property
    def valid(self) -> bool:
        """是否通过校验。"""
        return self.outcome is ScanOutcome.VALID

    @property
    def locks(self) -> bool:
        """是否会锁定流程。"""
        return self.outcome in LOCKING_OUTCOMES

    def to_batch(self) -> ReagentBatch | None:
        """转成试验使用的试剂批次快照。

        Returns:
            批次；读不出码或种类未知时为 None。
        """
        if self.kind is None or self.batch_no is None:
            return None
        return ReagentBatch(
            kind=self.kind,
            batch_no=self.batch_no,
            expiry_date=self.expiry_date,
            registered=self.registered,
            labeled_sensitivity=self.labeled_sensitivity,
        )


class ReagentLockedError(ServiceError):
    """试剂未通过校验，流程被锁定。

    Attributes:
        scans: 每种试剂的校验结果（含合格的）。
    """

    def __init__(self, scans: Mapping[ReagentKind, ReagentScan]) -> None:
        """创建异常。

        Args:
            scans: 每种试剂的校验结果。
        """
        reasons = "；".join(
            f"{KIND_NAMES[kind]}：{scan.message}" for kind, scan in scans.items() if not scan.valid
        )
        super().__init__(f"试剂未通过校验，流程已锁定：{reasons}", ServiceErrorCode.REAGENT_LOCKED)
        self.scans = dict(scans)


class ReagentStorage(Protocol):
    """试剂服务需要的存储。"""

    @property
    def reagent_checks(self) -> ReagentCheckRepository:
        """校验记录仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


@dataclass(frozen=True, slots=True)
class GovernmentStatus:
    """政府溯源平台的接入状态（占位）。

    Attributes:
        configured: 是否配置了客户端实现。
        enabled: 客户端报告已启用。
    """

    configured: bool
    enabled: bool


class ReagentService:
    """试剂扫码、校验、记录与查询。线程安全（无共享可变状态）。"""

    def __init__(
        self,
        auth: AuthService,
        storage: ReagentStorage,
        *,
        parser: ReagentCodeParser | None = None,
        registry: ReagentRegistry | None = None,
        government: GovernmentTraceabilityClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
            parser: 试剂码解析器，没有时所有试剂都无法校验。
            registry: 备案清单，没有时所有试剂都无法校验。
            government: 政府溯源平台客户端（接入预留），没有表示未接入。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._parser = parser
        self._registry = registry
        self._government = government
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ 校验

    @property
    def configured(self) -> bool:
        """是否配置了解析器和备案清单。"""
        return self._parser is not None and self._registry is not None

    def today(self) -> date:
        """校验使用的日期（本地日历日）。

        Returns:
            今天。
        """
        return self._clock().astimezone().date()

    def evaluate(self, raw: str, expected_kind: ReagentKind | None = None) -> ReagentScan:
        """解析并校验一个试剂码，不校验权限、不写记录。

        供 ``ExperimentService`` 在创建和启动试验时再校验使用；界面入口是 :meth:`scan`。

        Args:
            raw: 扫码原文。
            expected_kind: 期望的试剂种类；码里的种类与之不符时结论是 ``KIND_MISMATCH``。

        Returns:
            校验结果。不抛出解析和备案方面的异常，都表达在结论里。
        """
        text = raw.strip()
        if self._parser is None or self._registry is None:
            return ReagentScan(
                raw=text,
                outcome=ScanOutcome.NOT_CONFIGURED,
                message="未配置试剂码解析器或备案清单，无法确认试剂合格",
                kind=expected_kind,
            )
        try:
            code = self._parser.parse(text)
        except ReagentCodeFormatError as error:
            return ReagentScan(
                raw=text, outcome=ScanOutcome.UNREADABLE, message=str(error), kind=expected_kind
            )
        kind = code.kind or expected_kind
        if kind is None:
            return ReagentScan(
                raw=text,
                outcome=ScanOutcome.UNREADABLE,
                message="试剂码里没有试剂种类",
                batch_no=code.batch_no,
                product_code=code.product_code,
            )
        if expected_kind is not None and kind is not expected_kind:
            return ReagentScan(
                raw=text,
                outcome=ScanOutcome.KIND_MISMATCH,
                message=f"这是{KIND_NAMES[kind]}，不是{KIND_NAMES[expected_kind]}",
                kind=kind,
                batch_no=code.batch_no,
                product_code=code.product_code,
            )
        return self._verify(text, code, kind)

    def _verify(self, raw: str, code: ReagentCode, kind: ReagentKind) -> ReagentScan:
        assert self._registry is not None
        today = self.today()
        try:
            verification = self._registry.verify(code, today)
        except ReagentRegistryUnavailableError as error:
            return ReagentScan(
                raw=raw,
                outcome=ScanOutcome.REGISTRY_UNAVAILABLE,
                message=f"备案清单不可用，无法确认试剂合格：{error}",
                kind=kind,
                batch_no=code.batch_no,
                product_code=code.product_code,
                expiry_date=code.expiry_date,
            )
        expiry = verification.expiry_date or code.expiry_date
        registered = verification.status is not ReagentCheckStatus.NOT_REGISTERED
        batch = ReagentBatch(
            kind=kind,
            batch_no=code.batch_no,
            expiry_date=expiry,
            registered=registered,
            labeled_sensitivity=verification.labeled_sensitivity,
        )
        # 备案清单说不合格就是不合格；说合格时再用领域规则按日期复核一遍，两者取更严的。
        status = verification.status
        if status is ReagentCheckStatus.VALID:
            status = batch.check(today)
        outcome = _STATUS_TO_OUTCOME[status]
        message = verification.message or _default_message(outcome, expiry)
        return ReagentScan(
            raw=raw,
            outcome=outcome,
            message=message,
            kind=kind,
            batch_no=code.batch_no,
            product_code=code.product_code,
            expiry_date=expiry,
            days_remaining=None if expiry is None else (expiry - today).days,
            labeled_sensitivity=verification.labeled_sensitivity,
            registered=registered,
        )

    def evaluate_batches(self, batches: Iterable[ReagentBatch]) -> dict[ReagentKind, ReagentScan]:
        """按今天的日期复核已保存的试剂批次（启动前用），缺少的种类视为未配置（锁定）。

        只复核批次自带的备案标记和有效期，不再询问备案清单（保存的批次没有试剂码原文）。

        Args:
            batches: 试验保存的试剂批次。

        Returns:
            三种试剂各一个结果；同一种类有多个批次时，取第一个不合格的。
        """
        today = self.today()
        results: dict[ReagentKind, ReagentScan] = {}
        grouped: dict[ReagentKind, list[ReagentBatch]] = {kind: [] for kind in ReagentKind}
        for batch in batches:
            grouped[batch.kind].append(batch)
        for kind, items in grouped.items():
            if not items:
                results[kind] = ReagentScan(
                    raw="",
                    outcome=ScanOutcome.NOT_CONFIGURED,
                    message=f"没有录入{KIND_NAMES[kind]}",
                    kind=kind,
                )
                continue
            chosen = next(
                (b for b in items if b.check(today) is not ReagentCheckStatus.VALID), items[0]
            )
            outcome = _STATUS_TO_OUTCOME[chosen.check(today)]
            results[kind] = ReagentScan(
                raw="",
                outcome=outcome,
                message=_default_message(outcome, chosen.expiry_date),
                kind=kind,
                batch_no=chosen.batch_no,
                expiry_date=chosen.expiry_date,
                days_remaining=(
                    None if chosen.expiry_date is None else (chosen.expiry_date - today).days
                ),
                labeled_sensitivity=chosen.labeled_sensitivity,
                registered=chosen.registered,
            )
        return results

    def evaluate_all(self, codes: Mapping[ReagentKind, str]) -> dict[ReagentKind, ReagentScan]:
        """校验一个试验需要的三种试剂，缺少的种类视为未配置（锁定）。

        Args:
            codes: 种类到扫码原文。

        Returns:
            三种试剂各一个结果。
        """
        results: dict[ReagentKind, ReagentScan] = {}
        for kind in ReagentKind:
            raw = codes.get(kind, "")
            results[kind] = (
                self.evaluate(raw, kind)
                if raw.strip()
                else ReagentScan(
                    raw="",
                    outcome=ScanOutcome.NOT_CONFIGURED,
                    message=f"没有录入{KIND_NAMES[kind]}",
                    kind=kind,
                )
            )
        return results

    # ------------------------------------------------------------------ 界面入口

    def scan(
        self,
        session: Session | None,
        raw: str,
        *,
        expected_kind: ReagentKind | None = None,
        experiment_id: str | None = None,
    ) -> ReagentScan:
        """向导里的扫码：校验、写校验记录和审计。过期、未备案等会锁定流程，同样留痕。

        Args:
            session: 调用者的会话，需要 ``reagent_manage`` 权限。
            raw: 扫码原文（扫码枪回车或手输）。
            expected_kind: 这张卡片期望的种类。
            experiment_id: 关联的试验，向导里试验尚未创建时为 None。

        Returns:
            校验结果。

        Raises:
            PermissionDeniedError: 没有权限。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        user = self._auth.authorize(session, Permission.REAGENT_MANAGE)
        scan = self.evaluate(raw, expected_kind)
        self._record(user, scan, experiment_id)
        return scan

    def lookup(self, session: Session | None, raw: str) -> ReagentScan:
        """试剂溯源页的查询：只校验，不写校验记录，所有角色可用。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            raw: 扫码原文。

        Returns:
            校验结果。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return self.evaluate(raw)

    def record_checks(
        self, user: User, scans: Iterable[ReagentScan], experiment_id: str, uow: UnitOfWork
    ) -> None:
        """在调用方的事务里为试验记下用到的批次（创建试验时用）。

        Args:
            user: 操作者。
            scans: 校验结果，读不出码的跳过。
            experiment_id: 试验标识。
            uow: 调用方的工作单元。
        """
        for scan in scans:
            record = self._to_record(user, scan, experiment_id)
            if record is not None:
                uow.reagent_checks.add(record)

    def _record(self, user: User, scan: ReagentScan, experiment_id: str | None) -> None:
        record = self._to_record(user, scan, experiment_id)
        kind_name = KIND_NAMES.get(scan.kind, "试剂") if scan.kind is not None else "试剂"
        batch = scan.batch_no or "(未知)"
        suffix = "，流程锁定" if scan.locks else ""
        event = AuditEvent(
            occurred_at=self._clock(),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.REAGENT_CHECKED,
            detail=(
                f"扫码校验{kind_name}批号 {batch}：{scan.outcome.value}（{scan.message}）{suffix}"
            ),
            experiment_id=experiment_id,
        )
        with self._storage.unit_of_work() as uow:
            if record is not None:
                uow.reagent_checks.add(record)
            uow.audit.append(event)

    def _to_record(
        self, user: User, scan: ReagentScan, experiment_id: str | None
    ) -> ReagentCheckRecord | None:
        if scan.kind is None or scan.batch_no is None:
            return None
        status = {
            ScanOutcome.VALID: ReagentCheckStatus.VALID,
            ScanOutcome.EXPIRED: ReagentCheckStatus.EXPIRED,
            ScanOutcome.NOT_REGISTERED: ReagentCheckStatus.NOT_REGISTERED,
            ScanOutcome.EXPIRY_UNKNOWN: ReagentCheckStatus.EXPIRY_UNKNOWN,
        }.get(scan.outcome)
        if status is None:
            return None  # 种类放错、清单不可用等没有校验结论，只写审计
        return ReagentCheckRecord(
            checked_at=self._clock(),
            actor=user.username,
            kind=scan.kind,
            batch_no=scan.batch_no,
            product_code=scan.product_code or "",
            status=status,
            registered=scan.registered,
            expiry_date=scan.expiry_date,
            labeled_sensitivity=scan.labeled_sensitivity,
            message=scan.message,
            raw_code=scan.raw,
            experiment_id=experiment_id,
        )

    # ------------------------------------------------------------------ 查询

    def list_batches(self, session: Session | None, limit: int = 200) -> list[ReagentBatchEntry]:
        """已录入的试剂批次（同一批号取最近一次校验）。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            limit: 最多返回几个批次。

        Returns:
            批次列表，最近校验的在前。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return self._storage.reagent_checks.list_batches(limit)

    def recent_checks(self, session: Session | None, limit: int = 100) -> Page[ReagentCheckRecord]:
        """最近的校验记录。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            limit: 最多返回几条。

        Returns:
            一页校验记录，最新在前。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return self._storage.reagent_checks.query(PageRequest(limit=limit))

    def days_remaining(self, expiry: date | None) -> int | None:
        """距有效期的天数。

        Args:
            expiry: 有效期。

        Returns:
            天数（有效期当天为 0，过期为负）；未知时为 None。
        """
        return None if expiry is None else (expiry - self.today()).days

    def sample_codes(self, kind: ReagentKind | None = None) -> list[tuple[str, str]]:
        """示例扫码内容（仅模拟配置提供），按种类过滤。

        Args:
            kind: 只要这个种类的示例，None 表示全部。

        Returns:
            ``(显示名称, 扫码原文)`` 列表；真实配置没有示例时为空。
        """
        provider = self._parser
        if not isinstance(provider, SampleCodeProvider) or self._parser is None:
            return []
        result: list[tuple[str, str]] = []
        for label, raw in provider.sample_codes().items():
            try:
                code = self._parser.parse(raw)
            except ReagentCodeFormatError:
                continue
            if kind is None or code.kind is kind:
                result.append((label, raw))
        return result

    def government_status(self) -> GovernmentStatus:
        """政府溯源平台的接入状态（接入预留，目前只报告是否启用）。

        Returns:
            接入状态。
        """
        if self._government is None:
            return GovernmentStatus(configured=False, enabled=False)
        try:
            enabled = self._government.is_enabled()
        except Exception:
            _log.exception("查询政府溯源平台状态失败")
            enabled = False
        return GovernmentStatus(configured=True, enabled=enabled)


def _default_message(outcome: ScanOutcome, expiry: date | None) -> str:
    """结论的默认说明（简体中文）。"""
    if outcome is ScanOutcome.VALID:
        return "已备案，在有效期内"
    if outcome is ScanOutcome.EXPIRED:
        return f"已于 {expiry.isoformat()} 过期" if expiry is not None else "已过期"
    if outcome is ScanOutcome.NOT_REGISTERED:
        return "未在备案清单中"
    if outcome is ScanOutcome.EXPIRY_UNKNOWN:
        return "有效期未知，无法确认在有效期内"
    return "无法确认试剂合格"
