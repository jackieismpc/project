"""试验监控页的数据与操作：试验视图、停止试验、预览帧和视频事件的订阅。

界面不碰数据库和引擎线程，只通过这里读试验的完整视图（信息卡、管位、结果、各排阶段），
以及停止试验。每个入口先校验权限。实时更新由 ``ui/bridge.py`` 订阅引擎事件和这里的预览、
视频事件，页面据此增量刷新；打开页面时用 :meth:`MonitorService.view` 取一次完整快照。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from gelinspect.domain.conclusions import ExperimentConclusion, provisional_conclusion
from gelinspect.domain.entities import AuditEvent, Experiment, TubeResult, User
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    Interpretation,
    ReagentKind,
    TubeRole,
)
from gelinspect.domain.permissions import Permission
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRepository,
    ExperimentQuery,
    ExperimentRepository,
    PageRequest,
    TemperatureSample,
    TemplateRepository,
)
from gelinspect.interfaces.video import VideoInfo
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.engine_run import FLIP_ANGLE_KEY
from gelinspect.services.engine_types import (
    EngineError,
    EngineSnapshot,
    RowPhase,
    WellStatus,
)
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.video.events import VideoEvent
from gelinspect.services.video.preview import PreviewFrame

STOPPABLE_STATES = frozenset(
    {
        ExperimentState.INCUBATING,
        ExperimentState.READING,
        ExperimentState.ANALYZING,
        ExperimentState.FAULTED,
    }
)
"""可以停止的试验状态。"""

LIVE_STATES = STOPPABLE_STATES | {ExperimentState.READY}
"""显示实时画面的试验状态（就绪的试验启动前检查期间也算）。"""

_STARTED_MARK = f"{ExperimentState.READY.value} → {ExperimentState.INCUBATING.value}"
"""引擎写审计时“就绪 → 孵育中”的文字前缀，用来找开始时间。"""


class MonitorStorage(Protocol):
    """监控服务需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...

    @property
    def templates(self) -> TemplateRepository:
        """模板仓储。"""
        ...

    @property
    def users(self) -> _UserLookup:
        """用户仓储。"""
        ...

    @property
    def telemetry(self) -> _TelemetryLookup:
        """温度日志仓储。"""
        ...


class _UserLookup(Protocol):
    def get_by_username(self, username: str) -> User | None: ...


class _TelemetryLookup(Protocol):
    def query_temperatures(
        self,
        *,
        experiment_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: PageRequest,
    ) -> _SamplePage: ...


class _SamplePage(Protocol):
    @property
    def items(self) -> tuple[TemperatureSample, ...]: ...


class MonitorEngine(Protocol):
    """监控服务需要的引擎能力（``ExperimentEngine``）。"""

    @property
    def active_experiment_id(self) -> str | None:
        """正在运行的试验。"""
        ...

    def snapshot(self) -> EngineSnapshot | None:
        """最近一次运行的快照。"""
        ...

    def stop(self, user: User, reason: str = "") -> bool:
        """停止正在运行的试验。"""
        ...

    def stop_faulted(self, experiment_id: str, user: User, reason: str = "") -> None:
        """停止没有在运行的异常试验。"""
        ...

    def resume(self, user: User) -> bool:
        """从异常恢复正在运行的试验。"""
        ...

    def retry_analysis(self, user: User) -> bool:
        """重试正在运行的试验里分析失败的排。"""
        ...

    def retry_interrupted(self, experiment_id: str, user: User) -> None:
        """软件重启后离线重试分析。"""
        ...

    def retryable_rows(self, experiment_id: str) -> tuple[int, ...]:
        """重启后的异常试验里，还能离线重新分析的排。"""
        ...


class MonitorPipeline(Protocol):
    """监控服务需要的视频管线能力（``VideoPipeline``）。"""

    @property
    def info(self) -> VideoInfo | None:
        """视频源的实际参数。"""
        ...

    def set_preview_callback(self, callback: Callable[[PreviewFrame], None] | None) -> None:
        """更换预览回调。"""
        ...

    def add_event_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """添加事件监听者。"""
        ...

    def remove_event_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """移除事件监听者。"""
        ...

    def recent_events(self) -> list[VideoEvent]:
        """最近的视频事件。"""
        ...


@dataclass(frozen=True, slots=True)
class TubeRow:
    """试验数据记录的一行（一支试管）。

    Attributes:
        tube_no: 管号。
        row_index: 排号。
        sample_name: 样品名称。
        serial_no: 序列号。
        batch_no: 样品批号。
        dilution_factor: 稀释倍数。
        spiked_endotoxin: 外加内毒素。
        role: 试管用途。
        status: 孔的显示状态；没有结果且试验不在运行时为 None。
        result: 单管结果，没有时为 None。
    """

    tube_no: int
    row_index: int
    sample_name: str
    serial_no: str | None
    batch_no: str | None
    dilution_factor: float | None
    spiked_endotoxin: float | None
    role: TubeRole
    status: WellStatus | None
    result: TubeResult | None

    @property
    def reviewed(self) -> bool:
        """是否已人工复核。"""
        return self.result is not None and self.result.reviewed_interpretation is not None


@dataclass(frozen=True, slots=True)
class RowInfo:
    """一排的状态。

    Attributes:
        row_index: 排号。
        tubes: 本排有样品的管号。
        phase: 运行中的阶段；试验不在运行时为 None。
        remaining_s: 距到时的设备时间；不在运行时为 None。
    """

    row_index: int
    tubes: tuple[int, ...]
    phase: RowPhase | None
    remaining_s: float | None


@dataclass(frozen=True, slots=True)
class ExperimentView:
    """监控页需要的试验完整视图。

    Attributes:
        experiment_id: 试验标识。
        experiment_type: 试验类型。
        template_name: 模板名称（模板已删除时为空串）。
        template_builtin: 模板是否为内置模板（内置模板名称界面按类型翻译，自定义的原样显示）。
        state: 试验状态。
        operator: 操作人用户名。
        operator_name: 操作人显示名称。
        instrument_no: 仪器编号。
        created_at: 创建时间。
        started_at: 开始孵育的时间，从未开始时为 None。
        reagent_batches: 三种试剂的批号。
        labeled_sensitivity: 标示灵敏度 λ（EU/ml）。
        measured_sensitivity: 实测灵敏度 λc，没有结论的旧试验为 None；结论功能关闭时恒为 None。
        tubes: 全部试管，按管号升序。
        rows: 有样品的排，按排号升序。
        pending_review: 待复核的管号。
        live: 引擎正在运行这个试验。
        fault_reason: 异常原因。
        temperature_c: 引擎最近一次温度读数。
        last_elapsed_ms: 最近一次算法分析的耗时。
        last_flip_angle_deg: 最近一次翻转角度。
        signed_by: 签署人。
        conclusion: 试验结论；已完成的是入库的结论，其余是临时结论（待定）；
            结论功能关闭时为 None。
        conclusion_recorded: 结论是否已入库。
        conclusions_enabled: 结论功能是否打开，关闭时界面不显示结论卡和实测灵敏度。
        completed_at: 完成时间。
        can_resume: 可以恢复（异常且调度还在内存里）。
        interrupted: 异常且引擎没有在运行：软件重启过，调度已丢失，只能重试分析或停止。
        failed_rows: 本次运行里分析失败的排。
        retry_rows: 现在可以重试分析的排。
        unread_rows: 软件重启后不能再读数的排（没有抓帧，也没有结果）。
    """

    experiment_id: str
    experiment_type: ExperimentType
    template_name: str
    template_builtin: bool
    state: ExperimentState
    operator: str
    operator_name: str
    instrument_no: str
    created_at: datetime
    started_at: datetime | None
    reagent_batches: dict[ReagentKind, str]
    labeled_sensitivity: float | None
    measured_sensitivity: float | None
    tubes: tuple[TubeRow, ...]
    rows: tuple[RowInfo, ...]
    pending_review: tuple[int, ...]
    live: bool
    fault_reason: str | None
    temperature_c: float | None
    last_elapsed_ms: float | None
    last_flip_angle_deg: float | None
    signed_by: str | None
    conclusion: ExperimentConclusion | None
    conclusion_recorded: bool = False
    conclusions_enabled: bool = False
    completed_at: datetime | None = None
    can_resume: bool = False
    interrupted: bool = False
    failed_rows: tuple[int, ...] = ()
    retry_rows: tuple[int, ...] = ()
    unread_rows: tuple[int, ...] = ()

    @property
    def can_stop(self) -> bool:
        """试验是否处在可以停止的状态。"""
        return self.state in STOPPABLE_STATES

    def tube(self, tube_no: int) -> TubeRow | None:
        """按管号取一行。

        Args:
            tube_no: 管号。

        Returns:
            该行，不属于本试验时为 None。
        """
        return next((t for t in self.tubes if t.tube_no == tube_no), None)


_PHASE_STATUS = {
    RowPhase.WAITING: WellStatus.INCUBATING,
    RowPhase.READING: WellStatus.READING,
    RowPhase.ANALYZING: WellStatus.ANALYZING,
    RowPhase.READ_FAILED: WellStatus.ERROR,
    RowPhase.ANALYSIS_FAILED: WellStatus.ERROR,
    RowPhase.DONE: WellStatus.INCUBATING,
}


def result_status(result: TubeResult) -> WellStatus:
    """单管结果对应的孔状态；人工复核结论优先于算法结果。

    Args:
        result: 单管结果。

    Returns:
        孔的显示状态。
    """
    return {
        Interpretation.POSITIVE: WellStatus.POSITIVE,
        Interpretation.NEGATIVE: WellStatus.NEGATIVE,
        Interpretation.PENDING_REVIEW: WellStatus.PENDING_REVIEW,
    }[result.final_interpretation]


class MonitorService:
    """监控页的数据与操作。"""

    def __init__(
        self,
        auth: AuthService,
        storage: MonitorStorage,
        engine: MonitorEngine,
        pipeline: MonitorPipeline | None = None,
        *,
        conclusions_enabled: bool = False,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
            engine: 试验引擎。
            pipeline: 视频管线；为 None 时没有实时视频。
            conclusions_enabled: 是否显示试验结论，默认关闭；关闭时视图里没有结论和实测灵敏度。
        """
        self._conclusions_enabled = conclusions_enabled
        self._auth = auth
        self._storage = storage
        self._engine = engine
        self._pipeline = pipeline
        self._lock = threading.Lock()
        self._preview_listeners: list[Callable[[PreviewFrame], None]] = []
        if pipeline is not None:
            pipeline.set_preview_callback(self._fan_out_preview)

    # ------------------------------------------------------------------ 读

    def active_experiment_id(self) -> str | None:
        """引擎正在运行的试验。

        Returns:
            试验标识，没有时为 None。
        """
        return self._engine.active_experiment_id

    def default_experiment_id(self, session: Session | None) -> str | None:
        """“试验监控”页默认显示的试验：正在运行的，否则最近创建的。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            试验标识，没有任何试验时为 None。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        active = self._engine.active_experiment_id
        if active is not None:
            return active
        page = self._storage.experiments.query(ExperimentQuery(), PageRequest(limit=1))
        return page.items[0].experiment_id if page.items else None

    def view(self, session: Session | None, experiment_id: str) -> ExperimentView:
        """读取试验的完整视图（数据库加引擎快照）。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。

        Returns:
            试验视图。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 试验不存在（``EXPERIMENT_NOT_FOUND``）。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        snapshot = self._engine.snapshot()
        # 引擎线程在状态到达终点之后还要收尾（退出滑台、停视频），收尾期间界面已按只读显示。
        if snapshot is not None and snapshot.experiment_id != experiment_id:
            snapshot = None
        active = self._engine.active_experiment_id == experiment_id
        # 离线重试不碰设备和视频，没有实时画面可看，按只读显示。
        offline = snapshot is not None and snapshot.offline_retry
        live = active and not offline and experiment.state in LIVE_STATES
        view = self._build_view(experiment, snapshot if live else None, live)
        return self._with_recovery(view, snapshot if active else None, active)

    def _with_recovery(
        self, view: ExperimentView, snapshot: EngineSnapshot | None, active: bool
    ) -> ExperimentView:
        """补上恢复和重试的入口条件。"""
        failed = (
            tuple(r.row_index for r in snapshot.rows if r.phase is RowPhase.ANALYSIS_FAILED)
            if snapshot is not None
            else ()
        )
        if view.state is not ExperimentState.FAULTED:
            return replace(view, failed_rows=failed)
        if active:
            return replace(
                view,
                can_resume=snapshot is None or not snapshot.offline_retry,
                failed_rows=failed,
                retry_rows=failed,
            )
        retry = self._engine.retryable_rows(view.experiment_id)
        unread = tuple(
            row.row_index
            for row in view.rows
            if row.row_index not in retry
            and any(t.result is None for t in view.tubes if t.row_index == row.row_index)
        )
        return replace(view, interrupted=True, retry_rows=retry, unread_rows=unread)

    def _build_view(
        self, experiment: Experiment, snapshot: EngineSnapshot | None, live: bool
    ) -> ExperimentView:
        template = self._storage.templates.get(experiment.template_id)
        user = self._storage.users.get_by_username(experiment.operator)
        phases = {row.row_index: row for row in snapshot.rows} if snapshot is not None else {}
        tubes: list[TubeRow] = []
        by_row: dict[int, list[int]] = {}
        for assignment in sorted(experiment.assignments, key=lambda a: a.tube_no):
            no = assignment.tube_no
            row_index = tube_to_position(no).row_index
            by_row.setdefault(row_index, []).append(no)
            result = experiment.results.get(no)
            status: WellStatus | None
            if result is not None:
                status = result_status(result)
            elif row_index in phases:
                status = _PHASE_STATUS[phases[row_index].phase]
            else:
                status = None
            tubes.append(
                TubeRow(
                    tube_no=no,
                    row_index=row_index,
                    sample_name=assignment.sample.name,
                    serial_no=assignment.sample.serial_no,
                    batch_no=assignment.sample.batch_no,
                    dilution_factor=assignment.dilution_factor,
                    spiked_endotoxin=assignment.spiked_endotoxin,
                    role=assignment.role,
                    status=status,
                    result=result,
                )
            )
        rows = tuple(
            RowInfo(
                index,
                tuple(nos),
                phases[index].phase if index in phases else None,
                phases[index].remaining_s if index in phases else None,
            )
            for index, nos in sorted(by_row.items())
        )
        labeled = next(
            (
                r.labeled_sensitivity
                for r in experiment.reagents
                if r.kind is ReagentKind.LAL_REAGENT
            ),
            None,
        )
        analyzed = [r for r in experiment.results.values() if r.elapsed_ms is not None]
        newest = max(analyzed, key=lambda r: r.tube_no, default=None)
        angle = newest.features.get(FLIP_ANGLE_KEY) if newest is not None else None
        return ExperimentView(
            experiment_id=experiment.experiment_id,
            experiment_type=experiment.experiment_type,
            template_name=template.name if template is not None else "",
            template_builtin=template is not None
            and self._storage.templates.is_builtin(experiment.template_id),
            state=experiment.state,
            operator=experiment.operator,
            operator_name=user.display_name if user is not None else experiment.operator,
            instrument_no=experiment.instrument_no,
            created_at=experiment.created_at,
            started_at=self._started_at(experiment),
            reagent_batches={r.kind: r.batch_no for r in experiment.reagents},
            labeled_sensitivity=labeled,
            measured_sensitivity=(
                experiment.measured_sensitivity if self._conclusions_enabled else None
            ),
            tubes=tuple(tubes),
            rows=rows,
            pending_review=experiment.pending_review_tubes,
            live=live,
            fault_reason=snapshot.fault_reason if snapshot is not None else None,
            temperature_c=snapshot.temperature_c if snapshot is not None else None,
            last_elapsed_ms=newest.elapsed_ms if newest is not None else None,
            last_flip_angle_deg=float(angle) if isinstance(angle, float) else None,
            signed_by=experiment.signed_by,
            conclusion=(
                (experiment.conclusion or provisional_conclusion(experiment))
                if self._conclusions_enabled
                else None
            ),
            conclusion_recorded=self._conclusions_enabled and experiment.conclusion is not None,
            conclusions_enabled=self._conclusions_enabled,
            completed_at=experiment.completed_at,
        )

    def _started_at(self, experiment: Experiment) -> datetime | None:
        """开始孵育的时间：审计里第一条“就绪 → 孵育中”。"""
        query = AuditQuery(
            experiment_id=experiment.experiment_id,
            action=AuditAction.EXPERIMENT_STATE_CHANGED,
        )
        page = self._storage.audit.query(query, PageRequest(limit=1000))
        times = [
            r.event.occurred_at for r in page.items if r.event.detail.startswith(_STARTED_MARK)
        ]
        return min(times) if times else None

    def temperatures(
        self, session: Session | None, experiment_id: str, limit: int = 120
    ) -> list[float]:
        """试验期间的温度记录（最近 ``limit`` 条，按时间升序）。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            experiment_id: 试验标识。
            limit: 最多条数。

        Returns:
            摄氏度列表。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        page = self._storage.telemetry.query_temperatures(
            experiment_id=experiment_id, page=PageRequest(limit=1000)
        )
        return [s.celsius for s in page.items][-limit:]

    # ------------------------------------------------------------------ 停止

    def stop(self, session: Session | None, experiment_id: str, reason: str = "") -> None:
        """停止试验。运行中的交给引擎（立即返回），没有运行的异常试验直接转停止。

        Args:
            session: 调用者的会话，需要 ``experiment_stop`` 权限。
            experiment_id: 试验标识。
            reason: 原因，写入审计。

        Raises:
            PermissionDeniedError: 没有权限（已写 ``permission_denied`` 审计）。
            ServiceError: 试验不存在，或当前状态不能停止（``STOP_NOT_ALLOWED``）。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_STOP)
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        if experiment.state not in STOPPABLE_STATES:
            raise ServiceError(
                f"试验 {experiment_id} 的状态是 {experiment.state.value}，不能停止",
                ServiceErrorCode.STOP_NOT_ALLOWED,
                id=experiment_id,
            )
        if self._engine.active_experiment_id == experiment_id:
            self._engine.stop(user, reason)
        else:
            self._engine.stop_faulted(experiment_id, user, reason)

    # ------------------------------------------------------------------ 恢复

    def _require_faulted(self, experiment_id: str, code: ServiceErrorCode) -> None:
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        if experiment.state is not ExperimentState.FAULTED:
            raise ServiceError(f"试验 {experiment_id} 不是异常状态", code, id=experiment_id)

    def resume(self, session: Session | None, experiment_id: str) -> None:
        """恢复异常的试验。立即返回；结果通过状态变化或 ``RESUME_REJECTED`` 告警体现，都写审计。

        Args:
            session: 调用者的会话，需要 ``experiment_run`` 权限。
            experiment_id: 试验标识。

        Raises:
            PermissionDeniedError: 没有权限（已写 ``permission_denied`` 审计）。
            ServiceError: 试验不存在、不是异常状态，或软件重启后调度已丢失
                （``RESUME_NOT_ALLOWED``）。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_RUN)
        self._require_faulted(experiment_id, ServiceErrorCode.RESUME_NOT_ALLOWED)
        if self._engine.active_experiment_id != experiment_id:
            raise ServiceError(
                "软件重启后调度已经丢失，不能恢复", ServiceErrorCode.RESUME_NOT_ALLOWED
            )
        self._engine.resume(user)

    def retry_analysis(self, session: Session | None, experiment_id: str) -> None:
        """重试分析失败的排。从磁盘读回原图、核对哈希后重新分析，不翻转。

        运行中的试验交给引擎；重启后没有在运行的试验走离线重试。立即返回，结果和
        :meth:`resume` 一样通过状态变化或告警体现。

        Args:
            session: 调用者的会话，需要 ``experiment_run`` 权限。
            experiment_id: 试验标识。

        Raises:
            PermissionDeniedError: 没有权限（已写 ``permission_denied`` 审计）。
            ServiceError: 试验不存在、不是异常状态、没有可以重试的排，或别的试验正在运行
                （``RETRY_NOT_ALLOWED``）。
        """
        user = self._auth.authorize(session, Permission.EXPERIMENT_RUN)
        self._require_faulted(experiment_id, ServiceErrorCode.RETRY_NOT_ALLOWED)
        if self._engine.active_experiment_id == experiment_id:
            self._engine.retry_analysis(user)
            return
        try:
            self._engine.retry_interrupted(experiment_id, user)
        except EngineError as error:
            raise ServiceError(str(error), ServiceErrorCode.RETRY_NOT_ALLOWED) from error

    # ------------------------------------------------------------------ 视频

    def video_info(self) -> VideoInfo | None:
        """视频源的实际参数（分辨率、帧率），管线没有启动时为 None。

        Returns:
            视频信息。
        """
        return self._pipeline.info if self._pipeline is not None else None

    def recent_video_alerts(self) -> list[VideoEvent]:
        """打开监控页时仍然有效的视频告警（同一事件码最近一条且 ``active``）。

        Returns:
            视频事件，旧的在前。
        """
        if self._pipeline is None:
            return []
        latest: dict[str, VideoEvent] = {}
        for event in self._pipeline.recent_events():
            latest[event.code.value] = event
        return [e for e in latest.values() if e.active]

    def add_preview_listener(self, listener: Callable[[PreviewFrame], None]) -> None:
        """订阅预览帧（在预览线程上回调，必须尽快返回）。

        Args:
            listener: 回调。
        """
        with self._lock:
            if listener not in self._preview_listeners:
                self._preview_listeners = [*self._preview_listeners, listener]

    def remove_preview_listener(self, listener: Callable[[PreviewFrame], None]) -> None:
        """取消订阅预览帧。

        Args:
            listener: 回调。
        """
        with self._lock:
            self._preview_listeners = [x for x in self._preview_listeners if x != listener]

    def add_video_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """订阅视频管线事件（断流、黑屏、卡帧等）。

        Args:
            listener: 回调，在事件分发线程上调用。
        """
        if self._pipeline is not None:
            self._pipeline.add_event_listener(listener)

    def remove_video_listener(self, listener: Callable[[VideoEvent], None]) -> None:
        """取消订阅视频管线事件。

        Args:
            listener: 回调。
        """
        if self._pipeline is not None:
            self._pipeline.remove_event_listener(listener)

    def _fan_out_preview(self, frame: PreviewFrame) -> None:
        with self._lock:
            listeners = self._preview_listeners
        for listener in listeners:
            listener(frame)


def audit_event(
    user: User, action: AuditAction, detail: str, experiment_id: str, at: datetime
) -> AuditEvent:
    """生成一条审计事件（复核等服务共用）。

    Args:
        user: 操作者。
        action: 事件类型。
        detail: 操作记录。
        experiment_id: 关联试验。
        at: 发生时间（带时区）。

    Returns:
        审计事件。
    """
    return AuditEvent(
        occurred_at=at,
        actor=user.username,
        actor_role=user.role,
        action=action,
        detail=detail,
        experiment_id=experiment_id,
    )
