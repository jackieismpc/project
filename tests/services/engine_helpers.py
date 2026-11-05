"""试验引擎测试的公共工具：真实的管线和 SQLite，可卡住的模拟设备和算法。

时序全部靠门闩和手动调度时钟控制，不靠真实长等待。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from gelinspect.domain.entities import (
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
    User,
)
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    GelState,
    ReagentKind,
    Role,
    TubeRole,
)
from gelinspect.domain.plate import TUBE_COUNT, tube_to_position
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.interfaces.analysis import AnalyzerInfo, RowAnalysisRequest, RowAnalysisResult
from gelinspect.interfaces.device import FlipResult, TrayPosition
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRecord,
    ExperimentRepository,
    MediaKind,
    PageRequest,
    StorageError,
    UnitOfWork,
)
from gelinspect.interfaces.video import VideoConfig
from gelinspect.services.engine_types import (
    AlertCode,
    EngineAlert,
    EngineConfig,
    EngineEvent,
    EngineSnapshot,
    RowSnapshot,
    StateChanged,
)
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.video import (
    QualityConfig,
    ReconnectConfig,
    SnapshotConfig,
    VideoPipeline,
    VideoPipelineConfig,
)
from gelinspect.services.video.paths import sha256_file
from simulation.clock import ManualClock
from simulation.mock_analyzer import MockAnalyzer
from simulation.scenario import Scenario
from simulation.sim_device import SimDevice
from simulation.sim_video import SimVideoSource
from tests.services.video.helpers import wait_until

OPERATOR = User("u1", "operator", "实验员", Role.EXPERIMENTER)
ANALYST = User("u2", "analyst", "分析员", Role.ANALYST)
EXP_ID = "EXP-T-1"
WAIT_S = 30.0
"""等待条件成立的上限。只在失败时才会等满，正常远小于它。"""


def eventually(predicate: Callable[[], object], timeout_s: float = WAIT_S) -> bool:
    """轮询直到条件为真或超时。"""
    return wait_until(lambda: bool(predicate()), timeout_s)


def fast_config(**overrides: object) -> EngineConfig:
    """轮询很密、用手动调度时钟的引擎配置。"""
    values: dict[str, object] = {
        "status_poll_s": 0.005,
        "countdown_interval_s": 0.01,
        "tick_s": 0.002,
        "temperature_hold_s": 30.0,
        "temperature_log_interval_s": 1.0,
        "worker_stop_timeout_s": 5.0,
        "preflip_frame_timeout_s": 10.0,
        "video_down_grace_s": 20.0,
        "analysis_timeout_s": 20.0,
    }
    values.update(overrides)
    return EngineConfig(**values)  # type: ignore[arg-type]


def ready_experiment(tube_nos: Iterable[int], experiment_id: str = EXP_ID) -> Experiment:
    """构造已通过试剂校验的试验。"""
    experiment = Experiment(
        experiment_id=experiment_id,
        experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
        template_id="builtin.sensitivity_confirmation_2lambda_1",
        instrument_no="GI-T",
        operator=OPERATOR.username,
        created_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        assignments=tuple(
            TubeAssignment(
                tube_no=no,
                sample=Sample(f"S{no}", f"样品{no}", f"SN{no}", "SB1"),
                role=TubeRole.TEST,
            )
            for no in tube_nos
        ),
        reagents=(
            ReagentBatch(ReagentKind.LAL_REAGENT, "LAL1", date(2030, 1, 1), True, 0.25),
            ReagentBatch(ReagentKind.ENDOTOXIN, "CSE1", date(2030, 1, 1), True),
            ReagentBatch(ReagentKind.WATER, "WAT1", date(2030, 1, 1), True),
        ),
    )
    experiment.apply(ExperimentEvent.SUBMIT_FOR_REAGENT_CHECK)
    experiment.apply(ExperimentEvent.REAGENT_CHECK_PASSED)
    return experiment


class Gate:
    """让某个动作停在门口，等测试放行。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def passing(self) -> None:
        self.entered.set()
        if not self.release.wait(WAIT_S):
            raise TimeoutError("测试没有放行")

    def wait_entered(self) -> None:
        assert self.entered.wait(WAIT_S), "动作没有开始"


class GatedDevice(SimDevice):
    """可以卡住动作、记录调用线程和注入一次性失败的模拟设备。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(time_scale=6000.0, **kwargs)  # type: ignore[arg-type]
        self.gates: dict[str, Gate] = {}
        self.fail_next: dict[str, Exception] = {}
        self.calls: list[tuple[str, object]] = []
        self.threads: set[str] = set()

    def _action(self, name: str, argument: object = None) -> None:
        self.calls.append((name, argument))
        self.threads.add(threading.current_thread().name)
        gate = self.gates.get(name)
        if gate is not None:
            gate.passing()
        failure = self.fail_next.pop(name, None)
        if failure is not None:
            raise failure

    def connect(self) -> None:
        self._action("connect")
        super().connect()

    def move_tray(self, position: TrayPosition) -> None:
        self._action("move_tray", position)
        super().move_tray(position)

    def select_row(self, row_index: int) -> None:
        self._action("select_row", row_index)
        super().select_row(row_index)

    def flip_row(self, row_index: int) -> FlipResult:
        self._action("flip_row", row_index)
        return super().flip_row(row_index)

    def restore_row(self, row_index: int) -> FlipResult:
        self._action("restore_row", row_index)
        return super().restore_row(row_index)

    def flipped_rows(self) -> list[object]:
        return [arg for name, arg in self.calls if name == "flip_row"]


class GatedAnalyzer(MockAnalyzer):
    """可以卡住分析、按排注入整体故障的模拟算法，脚本化模式。"""

    def __init__(self, scenario: Scenario, **kwargs: object) -> None:
        super().__init__(scenario=scenario, scripted=True, **kwargs)  # type: ignore[arg-type]
        self.gates: dict[int, Gate] = {}
        self.errors: dict[int, list[Exception]] = {}
        self.requests: list[RowAnalysisRequest] = []
        self.threads: set[str] = set()
        self.drop_tubes: set[int] = set()
        self.closed = threading.Event()

    def load(self, config_path: Path | None) -> AnalyzerInfo:
        self.threads.add(threading.current_thread().name)
        return super().load(config_path)

    def close(self) -> None:
        self.threads.add(threading.current_thread().name)
        self.closed.set()
        super().close()

    def analyze_row(self, request: RowAnalysisRequest) -> RowAnalysisResult:
        self.threads.add(threading.current_thread().name)
        self.requests.append(request)
        gate = self.gates.get(request.row_index)
        if gate is not None:
            gate.passing()
        pending = self.errors.get(request.row_index)
        if pending:
            raise pending.pop(0)
        result = super().analyze_row(request)
        if self.drop_tubes:
            kept = [t for t in result.tubes if t.tube_no not in self.drop_tubes]
            return result.model_copy(update={"tubes": kept})
        return result


def gelled_scenario(tube_nos: Iterable[int], state: GelState = GelState.FULLY_GELLED) -> Scenario:
    """指定管位为某一状态，其余管位没有试管。"""
    states: dict[int, GelState | None] = dict.fromkeys(range(1, TUBE_COUNT + 1))
    states.update(dict.fromkeys(tube_nos, state))
    return Scenario(tube_states=states)


class _EventBarrier:
    """走一遍分发队列的标记。标记被处理时，排在它前面的事件都已经投递。"""

    def __init__(self) -> None:
        self.reached = threading.Event()


class EventLog:
    """记录引擎事件。"""

    def __init__(self) -> None:
        self.events: list[EngineEvent] = []

    def __call__(self, event: EngineEvent) -> None:
        if isinstance(event, _EventBarrier):
            event.reached.set()
            return
        self.events.append(event)

    def of(self, kind: type) -> list[EngineEvent]:
        return [e for e in list(self.events) if isinstance(e, kind)]

    def alerts(self, code: AlertCode | None = None) -> list[EngineAlert]:
        return [
            e
            for e in list(self.events)
            if isinstance(e, EngineAlert) and (code is None or e.code is code)
        ]

    def states(self) -> list[ExperimentState]:
        return [e.current for e in list(self.events) if isinstance(e, StateChanged)]


class _WatchedExperiments:
    """记下本事务是否写了单管结果，其余调用原样转发。"""

    def __init__(self, inner: ExperimentRepository) -> None:
        self._inner = inner
        self.saved_results = False

    def save_results(self, experiment_id: str, results: Sequence[TubeResult]) -> None:
        self.saved_results = True
        self._inner.save_results(experiment_id, results)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _WatchedUnitOfWork:
    def __init__(self, inner: UnitOfWork) -> None:
        self.experiments = _WatchedExperiments(inner.experiments)
        self.audit = inner.audit
        self.media = inner.media
        self.telemetry = inner.telemetry


class FlakyStorage:
    """包一层数据库，注入提交前的失败。

    ``failures_left`` 让接下来任意的工作单元失败；``result_failures_left`` 只让写了单管结果的
    工作单元失败。后者不受温度采样等其他事务的时序影响，用来精确命中结果事务。
    """

    def __init__(self, database: Database) -> None:
        self._database = database
        self.failures_left = 0
        self.result_failures_left = 0

    @property
    def experiments(self) -> ExperimentRepository:
        return self._database.experiments

    @contextmanager
    def unit_of_work(self) -> Iterator[UnitOfWork]:
        with self._database.unit_of_work() as uow:
            watched = _WatchedUnitOfWork(uow)
            yield cast("UnitOfWork", watched)
            if self.failures_left > 0:
                self.failures_left -= 1
                raise StorageError("注入：写库失败")
            if self.result_failures_left > 0 and watched.experiments.saved_results:
                self.result_failures_left -= 1
                raise StorageError("注入：结果写库失败")


@dataclass
class Harness:
    """一套装配好的引擎和它的依赖。"""

    device: GatedDevice
    source: SimVideoSource
    pipeline: VideoPipeline
    analyzer: GatedAnalyzer
    database: Database
    engine: ExperimentEngine
    schedule: ManualClock
    log: EventLog
    tubes: tuple[int, ...]
    storage: object = None
    extra: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> EngineSnapshot:
        snapshot = self.engine.snapshot()
        assert snapshot is not None
        return snapshot

    def state(self) -> ExperimentState:
        return self.snapshot().state

    def save_and_start(self, experiment_id: str = EXP_ID) -> None:
        self.database.experiments.save(ready_experiment(self.tubes, experiment_id))
        self.engine.start(experiment_id, OPERATOR)

    def wait_state(self, state: ExperimentState) -> None:
        def reached() -> bool:
            snapshot = self.engine.snapshot()
            return snapshot is not None and snapshot.state is state

        snapshot = self.engine.snapshot
        assert wait_until(reached, WAIT_S), f"没有到达 {state}，当前 {snapshot()}"
        self.sync_events()

    def sync_events(self) -> None:
        """等分发线程投递完已入队的事件。

        引擎保证事件先入队、快照后更新，所以看到新状态后调用它，相关事件一定已经在 ``log`` 里。
        分发线程已退出时不用等：退出前会投递完队列。
        """
        dispatcher = self.engine._events  # noqa: SLF001
        barrier = _EventBarrier()
        dispatcher.emit(barrier)  # type: ignore[arg-type]

        def settled() -> bool:
            thread = dispatcher._thread  # noqa: SLF001
            return barrier.reached.is_set() or thread is None or not thread.is_alive()

        assert wait_until(settled, WAIT_S), "事件分发线程没有处理完队列"

    def wait_rows(self, predicate: Callable[[Sequence[RowSnapshot]], bool]) -> None:
        def reached() -> bool:
            snapshot = self.engine.snapshot()
            return snapshot is not None and predicate(snapshot.rows)

        assert wait_until(reached, WAIT_S), f"条件未满足，当前 {self.engine.snapshot()}"
        self.sync_events()

    def wait_more_frames(self, count: int = 3) -> None:
        """等视频源再产出几帧，保证进行中的录制段里已经有帧。"""
        seen = self.pipeline.buffer.stats().total_put
        assert eventually(lambda: self.pipeline.buffer.stats().total_put >= seen + count)

    def incubating(self) -> None:
        """等到孵育开始。"""
        self.wait_state(ExperimentState.INCUBATING)

    def fire_rows(self) -> None:
        """让全部排到时。"""
        self.schedule.advance(self.engine.config.incubation_s + 1.0)

    def finished(self) -> None:
        assert self.engine.wait(WAIT_S), f"运行没有结束，当前 {self.engine.snapshot()}"

    def stored(self, experiment_id: str = EXP_ID) -> Experiment:
        experiment = self.database.experiments.get(experiment_id)
        assert experiment is not None
        return experiment

    def audit(self, experiment_id: str = EXP_ID) -> list[AuditRecord]:
        page = self.database.audit.query(
            AuditQuery(experiment_id=experiment_id), PageRequest(limit=1000)
        )
        return sorted(page.items, key=lambda record: record.seq)

    def audit_details(self, action: AuditAction | None = None) -> list[str]:
        return [r.event.detail for r in self.audit() if action is None or r.event.action is action]

    def assert_clean(self) -> None:
        """不留悬挂的录制段和 pin，也不留翻转状态。"""
        stats = self.pipeline.buffer.stats()
        assert stats.pins == 0
        assert not self.pipeline.recorder.active
        flipped = [row for row in range(1, 11) if self.device.is_flipped(row)]
        assert flipped == []
        verification = verify_audit_chain(self.database)
        assert verification.ok, verification.message

    def assert_media_intact(self, experiment_id: str = EXP_ID) -> None:
        """入库的媒体文件都存在，哈希一致。"""
        for media in self.database.media.list_for_experiment(experiment_id):
            path = Path(media.path)
            assert path.is_file(), media.path
            assert sha256_file(path) == media.sha256

    def media_counts(self, experiment_id: str = EXP_ID) -> tuple[int, int]:
        media = self.database.media
        return (
            len(media.list_for_experiment(experiment_id, kind=MediaKind.VIDEO)),
            len(media.list_for_experiment(experiment_id, kind=MediaKind.IMAGE)),
        )

    def release_all(self) -> None:
        for gate in [*self.device.gates.values(), *self.analyzer.gates.values()]:
            gate.release.set()

    def close(self) -> None:
        self.release_all()
        self.engine.shutdown(WAIT_S)
        self.pipeline.stop()
        self.database.close()


def tubes_of_rows(*rows: int) -> tuple[int, ...]:
    return tuple(no for no in range(1, TUBE_COUNT + 1) if tube_to_position(no).row_index in rows)


def make_harness(
    tmp_path: Path,
    tubes: Sequence[int] = tuple(range(1, 9)),
    *,
    scenario: Scenario | None = None,
    config: EngineConfig | None = None,
    schedule: ManualClock | None = None,
    reconnect: ReconnectConfig | None = None,
    use_scaled_schedule: bool = False,
    storage_wrapper: Callable[[Database], object] | None = None,
    width: int = 640,
    height: int = 360,
) -> Harness:
    """装配一套引擎。默认用手动调度时钟，排只有在测试推进时钟后才到时。"""
    scene = scenario or gelled_scenario(tubes)
    device = GatedDevice()
    source = SimVideoSource(scene=device, scenario=scene)
    pipeline = VideoPipeline(
        source,
        VideoPipelineConfig(
            data_dir=tmp_path / "data",
            source=VideoConfig(
                device="sim", width=width, height=height, fps=60.0, read_timeout_ms=20
            ),
            snapshot=SnapshotConfig(settle_s=0.02, wait_timeout_s=WAIT_S),
            quality=QualityConfig(watchdog_timeout_s=20.0, tick_interval_s=0.05),
            reconnect=reconnect
            or ReconnectConfig(initial_delay_s=0.02, max_delay_s=0.05, stall_disconnect_s=None),
        ),
    )
    analyzer = GatedAnalyzer(scene)
    database = open_database(tmp_path / "data" / "gel.db")
    log = EventLog()
    clock = schedule or ManualClock()
    storage = storage_wrapper(database) if storage_wrapper is not None else database
    engine = ExperimentEngine(
        device,
        pipeline,
        analyzer,
        storage,  # type: ignore[arg-type]
        config=config or fast_config(),
        schedule_clock=None if use_scaled_schedule else clock,
        on_event=log,
    )
    return Harness(
        device=device,
        source=source,
        pipeline=pipeline,
        analyzer=analyzer,
        database=database,
        engine=engine,
        schedule=clock,
        log=log,
        tubes=tuple(tubes),
        storage=storage,
    )
