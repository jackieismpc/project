"""试验引擎：把设备、视频管线、算法和存储串成一次完整的试验。

纯 Python + ``threading``，不依赖 Qt，P9 再用信号桥接 :class:`~.engine_types.EngineEvent`。
本模块是门面：校验权限和前置条件，转交给 :class:`~.engine_run.EngineRun`；
恢复上次未正常结束的试验也在这里。行为说明见 ``docs/features/experiment-engine.md``。

所有命令都不阻塞调用线程，可以从 Qt 主线程直接调用。
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from gelinspect.domain.entities import AuditEvent, Experiment, User
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.permissions import Permission, has_permission
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.interfaces.analysis import GelAnalyzer
from gelinspect.interfaces.device import DeviceController, DeviceError, DeviceStatus
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest, TransactionalStorage
from gelinspect.services.engine_recovery import recovery_plan
from gelinspect.services.engine_run import ACTIVE_STATES, SYSTEM_ACTOR, EngineDeps, EngineRun
from gelinspect.services.engine_types import (
    EngineBusyError,
    EngineConfig,
    EngineEvent,
    EngineEventListener,
    EnginePermissionError,
    EngineSnapshot,
    EngineStateError,
    RecoveredExperiment,
    StateChanged,
)
from gelinspect.services.engine_workers import EventDispatcher
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.paths import validate_experiment_id
from gelinspect.services.video.pipeline import VideoPipeline


class ScaledClock:
    """按倍数加速的调度时钟，只提供单调读数。

    设备时间的时长（孵育、温度持续时间）都用它计量。真实时间的事情（帧时间戳、算法耗时、
    阻塞等待）不能用它：翻转标记和帧时间必须同一基准。
    """

    def __init__(self, base: Clock, scale: float) -> None:
        """创建时钟。

        Args:
            base: 真实时钟。
            scale: 加速倍数。
        """
        self._base = base
        self._scale = scale
        self._origin = base.monotonic()

    def monotonic(self) -> float:
        """返回加速后的单调读数。

        Returns:
            设备时间，单位秒。
        """
        return (self._base.monotonic() - self._origin) * self._scale

    def now(self) -> datetime:
        """返回真实墙钟时间，不加速。

        Returns:
            带时区的当前时间。
        """
        return self._base.now()


class ExperimentEngine:
    """试验引擎门面。同一时间只运行一个试验。"""

    def __init__(
        self,
        device: DeviceController,
        pipeline: VideoPipeline,
        analyzer: GelAnalyzer,
        storage: TransactionalStorage,
        *,
        config: EngineConfig | None = None,
        mapping: GelStateMapping | None = None,
        clock: Clock | None = None,
        schedule_clock: Clock | None = None,
        analyzer_config_path: Path | None = None,
        on_event: EngineEventListener | None = None,
    ) -> None:
        """创建引擎，不启动线程，也不连接硬件。

        Args:
            device: 设备。
            pipeline: 视频管线。未启动时由引擎启动，并在运行结束时停止。
            analyzer: 算法，只在分析线程上调用。
            storage: 存储。
            config: 配置，默认值就是真实设备的参数（不加速）。
            mapping: 三态到阴阳性的映射。
            clock: 真实时钟，必须与设备和视频帧同一基准，默认系统时钟。
            schedule_clock: 调度时钟，默认按 ``config.time_scale`` 加速 ``clock``。
            analyzer_config_path: 传给 ``GelAnalyzer.load`` 的配置。
            on_event: 事件监听者，在事件分发线程上调用。
        """
        self._config = config or EngineConfig()
        real_clock: Clock = clock or SystemClock()
        self._events = EventDispatcher()
        if on_event is not None:
            self._events.subscribe(on_event)
        self._storage = storage
        self._deps = EngineDeps(
            device=device,
            pipeline=pipeline,
            analyzer=analyzer,
            storage=storage,
            config=self._config,
            mapping=mapping or GelStateMapping(),
            clock=real_clock,
            schedule_clock=schedule_clock or ScaledClock(real_clock, self._config.time_scale),
            analyzer_config_path=analyzer_config_path,
            events=self._events,
        )
        self._clock = real_clock
        self._lock = threading.Lock()
        self._run: EngineRun | None = None

    @property
    def config(self) -> EngineConfig:
        """引擎配置。"""
        return self._config

    def update_config(self, config: EngineConfig) -> None:
        """更换引擎配置（仪器设置保存后调用），下一次启动试验起生效。

        Args:
            config: 新配置。

        Raises:
            EngineBusyError: 有试验正在运行：运行中不能改孵育时长、读数参数和温度范围。
        """
        with self._lock:
            if self._run is not None and self._run.alive:
                raise EngineBusyError(
                    f"试验 {self._run.experiment_id} 正在运行，现在不能改引擎设置"
                )
            self._config = config
            self._deps = replace(self._deps, config=config)

    def add_listener(self, listener: EngineEventListener) -> None:
        """添加事件监听者。

        Args:
            listener: 事件处理函数，抛出的异常只记录日志。
        """
        self._events.subscribe(listener)

    def remove_listener(self, listener: EngineEventListener) -> None:
        """移除事件监听者。

        Args:
            listener: 事件处理函数。
        """
        self._events.unsubscribe(listener)

    @property
    def active_experiment_id(self) -> str | None:
        """正在运行的试验，包括运行中转异常、等待恢复的试验。"""
        run = self._run
        return run.experiment_id if run is not None and run.alive else None

    def snapshot(self) -> EngineSnapshot | None:
        """最近一次运行的只读快照。

        Returns:
            快照，从未运行过时为 None。
        """
        run = self._run
        return run.snapshot if run is not None else None

    # ------------------------------------------------------------------ 命令

    def start(self, experiment_id: str, user: User) -> None:
        """启动一个已通过试剂校验的试验。

        立即返回。启动前检查（连接设备、加载算法、启动视频、送入滑台）在引擎线程上进行，
        失败时试验仍为就绪，并发出 ``START_FAILED`` 告警。

        Args:
            experiment_id: 试验标识。
            user: 操作者，需要 ``experiment_run`` 权限。

        Raises:
            EnginePermissionError: 没有权限。
            EngineBusyError: 已有试验在运行，或上一次的算法调用还没有返回。
            EngineStateError: 试验不存在、不是就绪状态，或没有安排试管。
            InvalidIdentifierError: 试验标识不能用于文件路径。
        """
        self._require(user, Permission.EXPERIMENT_RUN)
        validate_experiment_id(experiment_id)
        with self._lock:
            if self._run is not None and self._run.alive:
                raise EngineBusyError(f"试验 {self._run.experiment_id} 正在运行")
            if self._run is not None and self._run.analyzer_busy:
                # 算法实现不需要处理并发。上一次的调用还卡着时再 load，就破坏了这个约定，
                # 而且旧线程返回后的 close 会把新一轮的模型关掉。
                raise EngineBusyError("上一次的算法调用还没有返回，暂时不能启动新试验")
            experiment = self._load(experiment_id)
            if experiment.state is not ExperimentState.READY:
                raise EngineStateError(
                    f"试验 {experiment_id} 的状态是 {experiment.state.value}，"
                    "通过试剂校验后才能启动"
                )
            if not experiment.assignments:
                raise EngineStateError(f"试验 {experiment_id} 没有安排任何试管")
            self._run = EngineRun(experiment, user, self._deps)
            self._run.start()

    def stop(self, user: User, reason: str = "") -> bool:
        """停止正在运行的试验。立即返回，用 :meth:`wait` 等待收尾完成。

        Args:
            user: 操作者，需要 ``experiment_stop`` 权限。
            reason: 原因，写入审计。

        Returns:
            请求已送达时为 True；没有运行中的试验时为 False。

        Raises:
            EnginePermissionError: 没有权限。
        """
        self._require(user, Permission.EXPERIMENT_STOP)
        run = self._run
        return run.request_stop(user, reason) if run is not None else False

    def resume(self, user: User) -> bool:
        """从异常恢复：未读的排继续，读数失败的排重新读数，分析失败的排重试。

        引擎先核对设备、温度和视频，条件不满足时发 ``RESUME_REJECTED`` 告警，试验保持异常。

        Args:
            user: 操作者，需要 ``experiment_run`` 权限。

        Returns:
            请求已送达时为 True；没有运行中的试验时为 False。

        Raises:
            EnginePermissionError: 没有权限。
        """
        self._require(user, Permission.EXPERIMENT_RUN)
        run = self._run
        return run.request_resume(user, analysis_only=False) if run is not None else False

    def retry_analysis(self, user: User) -> bool:
        """重试分析失败的排。从磁盘读回原图并核对哈希，不再翻转。

        Args:
            user: 操作者，需要 ``experiment_run`` 权限。

        Returns:
            请求已送达时为 True；没有运行中的试验时为 False。

        Raises:
            EnginePermissionError: 没有权限。
        """
        self._require(user, Permission.EXPERIMENT_RUN)
        run = self._run
        return run.request_resume(user, analysis_only=True) if run is not None else False

    def retryable_rows(self, experiment_id: str) -> tuple[int, ...]:
        """重启后的异常试验里，还能离线重新分析的排。

        Args:
            experiment_id: 试验标识。

        Returns:
            排号，升序；试验不存在、不是异常状态时为空。
        """
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None or experiment.state is not ExperimentState.FAULTED:
            return ()
        media = self._storage.media.list_for_experiment(experiment_id)
        return tuple(sorted(recovery_plan(experiment, media)))

    def retry_interrupted(self, experiment_id: str, user: User) -> None:
        """软件重启后重试分析：从磁盘读回原图、核对哈希后重新分析，不碰设备，也不翻转。

        立即返回。还有排没有读数时，分析完回到异常，这个试验只能停止。

        Args:
            experiment_id: 试验标识。
            user: 操作者，需要 ``experiment_run`` 权限。

        Raises:
            EnginePermissionError: 没有权限。
            EngineBusyError: 已有试验在运行，或上一次的算法调用还没有返回。
            EngineStateError: 试验不存在、不是异常状态，或没有可以重试的排。
        """
        self._require(user, Permission.EXPERIMENT_RUN)
        with self._lock:
            if self._run is not None and self._run.alive:
                raise EngineBusyError(f"试验 {self._run.experiment_id} 正在运行")
            if self._run is not None and self._run.analyzer_busy:
                raise EngineBusyError("上一次的算法调用还没有返回，暂时不能重试")
            experiment = self._load(experiment_id)
            if experiment.state is not ExperimentState.FAULTED:
                raise EngineStateError(f"试验 {experiment_id} 的状态不是异常")
            plan = recovery_plan(experiment, self._storage.media.list_for_experiment(experiment_id))
            if not plan:
                raise EngineStateError(f"试验 {experiment_id} 没有可以重试分析的排")
            self._run = EngineRun(experiment, user, self._deps, recovery=plan)
            self._run.start()

    def wait(self, timeout_s: float | None = None) -> bool:
        """等待当前运行结束。

        Args:
            timeout_s: 时限，None 表示一直等。

        Returns:
            已结束或没有运行时为 True。
        """
        run = self._run
        return run.join(timeout_s) if run is not None else True

    def shutdown(self, timeout_s: float | None = None) -> bool:
        """结束运行并释放全部线程，软件退出时调用。

        还在进行的试验转异常（不继续，也不算用户停止）；已是异常的保持异常。

        Args:
            timeout_s: 等待的时限。

        Returns:
            全部线程是否已结束。
        """
        run = self._run
        finished = True
        if run is not None and run.alive:
            run.request_shutdown()
            finished = run.join(timeout_s)
        return self._events.stop(5.0 if timeout_s is None else timeout_s) and finished

    def probe_device(self) -> DeviceStatus | None:
        """空闲时探测设备状态，供界面显示连接和温度。

        与运行中的试验互斥：拿不到引擎锁或试验在运行时直接返回 None，不会与引擎线程同时访问设备。

        Returns:
            设备状态；试验运行中、或正在启动时为 None。
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self._run is not None and self._run.alive:
                return None
            device = self._deps.device
            with contextlib.suppress(DeviceError):  # 连不上时 read_status 返回 connected=False
                device.connect()
            return device.read_status()
        finally:
            self._lock.release()

    # ------------------------------------------------------------------ 恢复

    def recover_interrupted(self) -> list[RecoveredExperiment]:
        """启动时调用：把停留在运行中状态的试验标记为异常，并写明原因。

        进程已经不在，设备的实际状态不可知，所以**不自动继续**。状态和审计在同一事务提交。

        Returns:
            被标记的试验。

        Raises:
            EngineBusyError: 有试验正在运行。此时库内的运行中状态是真实的，不能标记。
        """
        with self._lock:
            if self._run is not None and self._run.alive:
                raise EngineBusyError("有试验正在运行，不能执行启动恢复")
            recovered: list[RecoveredExperiment] = []
            for state in sorted(ACTIVE_STATES):
                recovered.extend(self._recover_state(state))
        if recovered:
            self._events.stop(5.0)
        return recovered

    def _recover_state(self, state: ExperimentState) -> list[RecoveredExperiment]:
        recovered: list[RecoveredExperiment] = []
        query = ExperimentQuery(state=state)
        while True:
            # 每标记一个，它就不再匹配查询，所以总是取第一页。
            page = self._storage.experiments.query(query, PageRequest(limit=100))
            if not page.items:
                return recovered
            for summary in page.items:
                recovered.append(self._mark_interrupted(summary.experiment_id))

    def _mark_interrupted(self, experiment_id: str) -> RecoveredExperiment:
        experiment = self._load(experiment_id)
        previous = experiment.state
        reason = (
            f"软件启动时发现试验停留在 {previous.value}，上次运行没有正常结束（崩溃或断电）。"
            "设备实际状态未知，某排可能仍处于翻转状态，请人工检查；未读的排错过了读数时间，不会自动继续"
        )
        current = experiment.apply(ExperimentEvent.FAULT)
        now = self._clock.now()
        with self._storage.unit_of_work() as uow:
            uow.experiments.update_state(experiment_id, current, expected=previous)
            uow.audit.append(
                AuditEvent(
                    occurred_at=now,
                    actor=SYSTEM_ACTOR,
                    actor_role=None,
                    action=AuditAction.EXPERIMENT_STATE_CHANGED,
                    detail=f"{previous.value} → {current.value}（fault）：{reason}",
                    experiment_id=experiment_id,
                )
            )
        self._publish(
            StateChanged(
                experiment_id=experiment_id,
                at=now,
                previous=previous,
                current=current,
                trigger=ExperimentEvent.FAULT,
                actor=SYSTEM_ACTOR,
                reason=reason,
            )
        )
        return RecoveredExperiment(experiment_id, previous, reason)

    def stop_faulted(self, experiment_id: str, user: User, reason: str = "") -> None:
        """停止一个没有在运行的异常试验，例如启动恢复标记的试验。

        Args:
            experiment_id: 试验标识。
            user: 操作者，需要 ``experiment_stop`` 权限。
            reason: 原因，写入审计。

        Raises:
            EnginePermissionError: 没有权限。
            EngineBusyError: 该试验正在运行，应使用 :meth:`stop`。
            EngineStateError: 试验不存在或不是异常状态。
        """
        self._require(user, Permission.EXPERIMENT_STOP)
        with self._lock:
            if self.active_experiment_id == experiment_id:
                raise EngineBusyError(f"试验 {experiment_id} 正在运行，应使用 stop")
            experiment = self._load(experiment_id)
            if experiment.state is not ExperimentState.FAULTED:
                raise EngineStateError(f"试验 {experiment_id} 的状态不是异常")
            current = experiment.apply(ExperimentEvent.STOP)
            now = self._clock.now()
            detail = f"faulted → {current.value}（stop）：用户停止。{reason}".strip()
            with self._storage.unit_of_work() as uow:
                uow.experiments.update_state(
                    experiment_id, current, expected=ExperimentState.FAULTED
                )
                uow.audit.append(
                    AuditEvent(
                        occurred_at=now,
                        actor=user.username,
                        actor_role=user.role,
                        action=AuditAction.EXPERIMENT_STATE_CHANGED,
                        detail=detail,
                        experiment_id=experiment_id,
                    )
                )
        self._publish(
            StateChanged(
                experiment_id=experiment_id,
                at=now,
                previous=ExperimentState.FAULTED,
                current=current,
                trigger=ExperimentEvent.STOP,
                actor=user.username,
                reason=reason,
            )
        )
        self._events.stop(5.0)

    # ------------------------------------------------------------------ 内部

    def _publish(self, event: EngineEvent) -> None:
        self._events.start()
        self._events.emit(event)

    def _load(self, experiment_id: str) -> Experiment:
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise EngineStateError(f"试验不存在：{experiment_id}")
        return experiment

    @staticmethod
    def _require(user: User, permission: Permission) -> None:
        if not user.active or not has_permission(user.role, permission):
            raise EnginePermissionError(f"用户 {user.username} 没有 {permission.value} 权限")
