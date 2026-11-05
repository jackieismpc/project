"""新试验：创建、再校验试剂、启动、放弃。

向导（界面）只收集输入。**所有会改变结果的判断都在这里重做一遍**，界面被绕过也不会放行：

* 创建试验时，用扫码原文重新解析并校验三种试剂；有任何一种过期、未备案、读不出或没录入，
  就写审计并抛 :class:`~gelinspect.services.reagent_service.ReagentLockedError`，不保存试验。
* 启动试验时，对已保存的试剂批次按今天的日期复核；过期了（隔夜才启动）就把试验转为
  「试剂锁定」并拒绝启动。
* 启动由 ``ExperimentEngine.start`` 完成，它立即返回；启动前检查（连接设备、温度、视频）
  在引擎线程上，失败时试验仍为就绪，并通过 ``EngineAlert(START_FAILED)`` 报告，界面据此显示原因。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.entities import (
    AuditEvent,
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    User,
    check_reagents,
)
from gelinspect.domain.enums import AuditAction, ReagentKind
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.interfaces.storage import (
    ExperimentRepository,
    NotFoundError,
    StateConflictError,
    TemplateRepository,
    UnitOfWork,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.engine_types import EngineBusyError, EngineError, EngineStateError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import NewExperimentRequest
from gelinspect.services.reagent_service import (
    KIND_NAMES,
    ReagentLockedError,
    ReagentScan,
    ReagentService,
)

_log = logging.getLogger(__name__)


def check_limit(value: float | None, field: str) -> float | None:
    """校验 MVD 或内毒素限值：留空，或大于 0 的有限数。

    Args:
        value: 输入的值。
        field: 字段名（``mvd`` 或 ``endotoxin_limit``），写入错误参数。

    Returns:
        原值。

    Raises:
        ServiceError: 不是大于 0 的有限数。
    """
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ServiceError(
            f"{field} 必须是大于 0 的数字：{value}", ServiceErrorCode.LIMITS_INVALID, field=field
        )
    return value


def _limits_text(mvd: float | None, limit: float | None) -> str:
    shown = {"MVD": mvd, "内毒素限值": limit}
    return "，".join(f"{name} {value:g}" for name, value in shown.items() if value is not None)


class ExperimentStarter(Protocol):
    """能启动试验的引擎（``ExperimentEngine``）。"""

    def start(self, experiment_id: str, user: User) -> None:
        """启动试验，立即返回。"""
        ...


class ExperimentStorage(Protocol):
    """试验服务需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    @property
    def templates(self) -> TemplateRepository:
        """模板仓储。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


class ExperimentService:
    """创建、启动和放弃试验。"""

    def __init__(
        self,
        auth: AuthService,
        storage: ExperimentStorage,
        reagents: ReagentService,
        engine: ExperimentStarter,
        *,
        instrument_no: str = "GI-001",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
            reagents: 试剂服务，用来重新校验试剂。
            engine: 试验引擎。
            instrument_no: 写入新试验的仪器编号。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._reagents = reagents
        self._engine = engine
        self._instrument_no = instrument_no
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def instrument_no(self) -> str:
        """写入新试验的仪器编号。"""
        return self._instrument_no

    def set_instrument_no(self, instrument_no: str) -> None:
        """更换写入新试验的仪器编号（仪器设置保存后调用，已有试验不变）。

        Args:
            instrument_no: 新的仪器编号。
        """
        self._instrument_no = instrument_no

    # ------------------------------------------------------------------ 创建

    def create_experiment(self, session: Session | None, request: NewExperimentRequest) -> str:
        """创建试验并通过试剂校验，状态为就绪，尚未启动。

        Args:
            session: 调用者的会话，需要 ``experiment_create`` 权限。
            request: 创建请求（原始输入，服务层自己重新校验）。

        Returns:
            试验标识。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 模板不存在、样品和管位对不上。
            ReagentLockedError: 试剂未通过校验（已写审计，试验没有保存）。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_CREATE)
        mvd = check_limit(request.mvd, "mvd")
        endotoxin_limit = check_limit(request.endotoxin_limit, "endotoxin_limit")
        template = self._storage.templates.get(request.template_id)
        if template is None:
            raise ServiceError(
                f"模板不存在：{request.template_id}", ServiceErrorCode.TEMPLATE_NOT_FOUND
            )
        ordered_items = sorted(template.items, key=lambda i: i.tube_no)
        assignments = sorted(request.assignments, key=lambda a: a.tube_no)
        if len(assignments) != len(ordered_items):
            raise ServiceError(
                f"模板要求 {len(ordered_items)} 支试管，实际安排了 {len(assignments)} 支",
                ServiceErrorCode.DRAFT_COUNT_MISMATCH,
                expected=len(ordered_items),
                actual=len(assignments),
            )
        tube_nos = [a.tube_no for a in assignments]
        try:
            for tube_no in tube_nos:
                tube_to_position(tube_no)
        except InvalidPlatePositionError as error:
            raise ServiceError(str(error), ServiceErrorCode.DRAFT_TUBES_INVALID) from error
        if len(set(tube_nos)) != len(tube_nos):
            raise ServiceError("管号重复", ServiceErrorCode.DRAFT_TUBES_INVALID)
        for index, assignment in enumerate(assignments, start=1):
            if not assignment.name.strip():
                raise ServiceError(
                    f"第 {index} 行的样品名称为空",
                    ServiceErrorCode.DRAFT_SAMPLE_NAME_REQUIRED,
                    index=index,
                )

        scans = self._reagents.evaluate_all(request.reagent_codes)
        now = self._clock()
        experiment_id = self._new_id(now)
        if any(not scan.valid for scan in scans.values()):
            self._audit_lock(user, scans, experiment_id=None, why="创建试验被拒绝")
            raise ReagentLockedError(scans)

        batches: list[ReagentBatch] = []
        for scan in scans.values():
            batch = scan.to_batch()
            assert batch is not None  # 全部 VALID 时必有批次
            batches.append(batch)
        report = check_reagents(batches, self._reagents.today())
        labeled = next(
            (b.labeled_sensitivity for b in batches if b.kind is ReagentKind.LAL_REAGENT), None
        )
        if template.requires_lambda and (labeled is None or labeled <= 0):
            raise ServiceError(
                "这个模板按鲎试剂的标示灵敏度 λ 计算外加内毒素，但试剂没有标示灵敏度",
                ServiceErrorCode.LAMBDA_REQUIRED,
            )
        experiment = Experiment(
            experiment_id=experiment_id,
            experiment_type=template.experiment_type,
            template_id=template.template_id,
            instrument_no=self._instrument_no,
            operator=user.username,
            created_at=now,
            assignments=tuple(
                TubeAssignment(
                    tube_no=a.tube_no,
                    sample=Sample(
                        sample_id=f"{experiment_id}-{a.tube_no:02d}",
                        name=a.name.strip(),
                        batch_no=a.batch_no.strip() or None,
                    ),
                    role=item.role,
                    dilution_factor=a.dilution_factor,
                    # 按倍数的管位由这里按 λ 算出并固化，不信任界面传来的值。
                    spiked_endotoxin=(
                        a.spiked_endotoxin
                        if item.lambda_multiple is None
                        else item.spike_for(labeled)
                    ),
                )
                for a, item in zip(assignments, ordered_items, strict=True)
            ),
            reagents=tuple(batches),
            mvd=mvd,
            endotoxin_limit=endotoxin_limit,
            layout_confirmed=not template.needs_confirmation,
        )
        experiment.apply(ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK)
        if not report.passed:  # 领域规则复核：理论上不会发生，发生就是不放行
            self._audit_lock(user, scans, experiment_id=None, why="领域规则复核未通过")
            raise ReagentLockedError(scans)
        experiment.apply(report.event)

        names = "、".join(f"{KIND_NAMES[b.kind]} {b.batch_no}" for b in batches)
        events = [
            AuditEvent(
                occurred_at=now,
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.EXPERIMENT_CREATED,
                detail=(
                    f"创建试验：模板 {template.name}，{len(assignments)} 管，"
                    f"仪器 {self._instrument_no}"
                    + (f"，{text}" if (text := _limits_text(mvd, endotoxin_limit)) else "")
                ),
                experiment_id=experiment_id,
            ),
            AuditEvent(
                occurred_at=now,
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.REAGENT_CHECKED,
                detail=f"试剂校验通过：{names}",
                experiment_id=experiment_id,
            ),
            AuditEvent(
                occurred_at=now,
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.EXPERIMENT_STATE_CHANGED,
                detail=f"{ExperimentState.DRAFT.value} → {experiment.state.value}（试剂校验通过）",
                experiment_id=experiment_id,
            ),
        ]
        with self._storage.unit_of_work() as uow:
            uow.experiments.save(experiment)
            self._reagents.record_checks(user, scans.values(), experiment_id, uow)
            uow.audit.append_many(events)
        return experiment_id

    # ------------------------------------------------------------------ MVD 与限值

    def update_limits(
        self,
        session: Session | None,
        experiment_id: str,
        *,
        mvd: float | None,
        endotoxin_limit: float | None,
    ) -> None:
        """修改试验的 MVD 和内毒素限值。**已签署的试验不能改**，改动写审计（旧值和新值）。

        Args:
            session: 调用者的会话，需要 ``experiment_create`` 权限。
            experiment_id: 试验标识。
            mvd: 新的 MVD，None 表示清空。
            endotoxin_limit: 新的内毒素限值，None 表示清空。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 试验不存在、已签署，或数值不合法。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_CREATE)
        check_limit(mvd, "mvd")
        check_limit(endotoxin_limit, "endotoxin_limit")
        try:
            with self._storage.unit_of_work() as uow:
                experiment = uow.experiments.get(experiment_id)
                if experiment is None:
                    raise NotFoundError(experiment_id)
                if experiment.state is ExperimentState.SIGNED:
                    raise StateConflictError(experiment_id)
                uow.experiments.update_limits(
                    experiment_id, mvd=mvd, endotoxin_limit=endotoxin_limit
                )
                old = _limits_text(experiment.mvd, experiment.endotoxin_limit) or "无"
                new = _limits_text(mvd, endotoxin_limit) or "无"
                uow.audit.append(
                    AuditEvent(
                        occurred_at=self._clock(),
                        actor=user.username,
                        actor_role=user.role,
                        action=AuditAction.LIMITS_CHANGED,
                        detail=f"修改 MVD 和内毒素限值：{old} → {new}",
                        experiment_id=experiment_id,
                    )
                )
        except NotFoundError as error:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            ) from error
        except StateConflictError as error:
            raise ServiceError(
                f"试验 {experiment_id} 已签署，MVD 和限值不能再修改",
                ServiceErrorCode.EXPERIMENT_SIGNED,
            ) from error

    # ------------------------------------------------------------------ 启动

    def start_experiment(self, session: Session | None, experiment_id: str) -> None:
        """启动已就绪的试验。立即返回；启动前检查在引擎线程上，失败由引擎告警报告。

        启动前对保存的试剂批次按今天的日期复核；过期了就转为「试剂锁定」并拒绝。

        Args:
            session: 调用者的会话，需要 ``experiment_run`` 权限。
            experiment_id: 试验标识。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 试验不存在、不是就绪状态、已有试验在运行。
            ReagentLockedError: 试剂已过期或不合格（试验转为试剂锁定）。
            StorageBusyError: 数据库忙，可重试。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_RUN)
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        if experiment.state is not ExperimentState.READY:
            raise ServiceError(
                f"试验 {experiment_id} 的状态是 {experiment.state.value}，只有就绪的试验才能启动",
                ServiceErrorCode.EXPERIMENT_NOT_READY,
                id=experiment_id,
            )
        scans = self._reagents.evaluate_batches(experiment.reagents)
        if any(not scan.valid for scan in scans.values()):
            self._lock_experiment(user, experiment, scans)
            raise ReagentLockedError(scans)
        try:
            self._engine.start(experiment_id, user)
        except EngineBusyError as error:
            raise ServiceError(str(error), ServiceErrorCode.ENGINE_BUSY) from error
        except EngineStateError as error:
            raise ServiceError(
                str(error), ServiceErrorCode.EXPERIMENT_NOT_READY, id=experiment_id
            ) from error
        except EngineError as error:
            raise ServiceError(str(error), ServiceErrorCode.ENGINE_REFUSED) from error

    def abandon_experiment(self, session: Session | None, experiment_id: str) -> None:
        """放弃一个就绪但没有启动的试验（启动失败后用户不再重试时）。

        Args:
            session: 调用者的会话，需要 ``experiment_create`` 权限。
            experiment_id: 试验标识。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 试验不存在，或不是就绪状态。
            StorageBusyError: 数据库忙，可重试。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_CREATE)
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        if experiment.state is not ExperimentState.READY:
            raise ServiceError(
                f"试验 {experiment_id} 的状态是 {experiment.state.value}，只能放弃就绪的试验",
                ServiceErrorCode.EXPERIMENT_NOT_READY,
                id=experiment_id,
            )
        experiment.apply(ExperimentEvent.STOP)
        event = AuditEvent(
            occurred_at=self._clock(),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.EXPERIMENT_STATE_CHANGED,
            detail=f"{ExperimentState.READY.value} → {experiment.state.value}（启动前放弃）",
            experiment_id=experiment_id,
        )
        with self._storage.unit_of_work() as uow:
            uow.experiments.update_state(
                experiment_id, experiment.state, expected=ExperimentState.READY
            )
            uow.audit.append(event)

    # ------------------------------------------------------------------ 内部

    def _new_id(self, now: datetime) -> str:
        base = f"EXP-{now.astimezone().strftime('%Y%m%d-%H%M%S')}"
        candidate, index = base, 1
        while self._storage.experiments.get(candidate) is not None:
            index += 1
            candidate = f"{base}-{index}"
        return candidate

    def _audit_lock(
        self,
        user: User,
        scans: dict[ReagentKind, ReagentScan],
        *,
        experiment_id: str | None,
        why: str,
    ) -> None:
        reasons = "；".join(
            f"{KIND_NAMES[kind]} {scan.batch_no or '(无批号)'}：{scan.message}"
            for kind, scan in scans.items()
            if not scan.valid
        )
        event = AuditEvent(
            occurred_at=self._clock(),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.REAGENT_CHECKED,
            detail=f"{why}，流程锁定：{reasons}",
            experiment_id=experiment_id,
        )
        with self._storage.unit_of_work() as uow:
            uow.audit.append(event)

    def _lock_experiment(
        self, user: User, experiment: Experiment, scans: dict[ReagentKind, ReagentScan]
    ) -> None:
        """把就绪但试剂已不合格的试验转为试剂锁定（就绪 → 草稿 → 校验 → 锁定）。"""
        experiment.apply(ExperimentEvent.EDIT)
        experiment.apply(ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK)
        experiment.apply(ExperimentEvent.REAGENT_CHECK_FAILED)
        reasons = "；".join(
            f"{KIND_NAMES[kind]} {scan.batch_no or '(无批号)'}：{scan.message}"
            for kind, scan in scans.items()
            if not scan.valid
        )
        event = AuditEvent(
            occurred_at=self._clock(),
            actor=user.username,
            actor_role=user.role,
            action=AuditAction.REAGENT_CHECKED,
            detail=f"启动前复核未通过，试验转为 {experiment.state.value}：{reasons}",
            experiment_id=experiment.experiment_id,
        )
        with self._storage.unit_of_work() as uow:
            uow.experiments.update_state(
                experiment.experiment_id, experiment.state, expected=ExperimentState.READY
            )
            uow.audit.append(event)
