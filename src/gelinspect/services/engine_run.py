"""一次试验运行：引擎线程上的主循环、读数流程、故障和恢复。

规则：

- ``Experiment`` 和设备只在引擎线程上访问。算法在分析线程上调用，结局经收件箱送回。
- 每次写库都走 :meth:`EngineRun._transact`：先改内存聚合，事务提交失败就撤销，
  提交成功后才发事件。内存、数据库和界面看到的状态不会分叉。
- 先入队事件，再发布快照：看到快照新状态的观察者，对应的事件都已经在分发队列里。
- 写锁被其他写入者占用是暂时的，引擎等到为止（有总时限），不因此转异常或中止运行。
- 翻转不可逆（翻动过的凝胶不能再读一次），所以翻转前做完所有能做的检查，
  算法故障也不打断其他排的按时读数，原图留在磁盘上可以重试。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from gelinspect.domain.conclusions import finalize_conclusion, labeled_sensitivity_of
from gelinspect.domain.entities import AuditEvent, Experiment, TubeResult, User
from gelinspect.domain.enums import (
    AuditAction,
    Interpretation,
    ReagentKind,
    TubeAnalysisStatus,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.plate import row_label, tube_to_position
from gelinspect.domain.state_machine import (
    ExperimentEvent,
    ExperimentState,
    allowed_events,
)
from gelinspect.interfaces.analysis import (
    AnalyzerInfo,
    CapturedFrame,
    GelAnalyzer,
    RowAnalysisRequest,
    RowAnalysisResult,
    TubeSlot,
)
from gelinspect.interfaces.device import (
    DeviceController,
    DeviceError,
    DeviceStatus,
    FlipResult,
    PlateAwareDevice,
    PlatePlan,
    PlateTube,
    TrayPosition,
)
from gelinspect.interfaces.storage import (
    MediaFile,
    StorageBusyError,
    StorageError,
    TemperatureSample,
    TransactionalStorage,
    UnitOfWork,
)
from gelinspect.interfaces.video import VideoError
from gelinspect.services.engine_recovery import RowRecovery
from gelinspect.services.engine_types import (
    AlertCode,
    AlertLevel,
    EngineAlert,
    EngineConfig,
    EngineEvent,
    EngineSnapshot,
    FaultKind,
    RowCaptured,
    RowCountdown,
    RowPhase,
    RowSnapshot,
    StateChanged,
    TemperatureChanged,
    WellStatus,
    WellUpdated,
)
from gelinspect.services.engine_workers import (
    AnalysisOutcome,
    AnalysisStarted,
    AnalysisWorker,
    EventDispatcher,
)
from gelinspect.services.mask_archive import archive_masks
from gelinspect.services.video.clock import Clock
from gelinspect.services.video.errors import InsufficientFramesError, RecorderError
from gelinspect.services.video.events import VideoEvent, VideoEventCode, VideoEventLevel
from gelinspect.services.video.media import segment_to_media_file, snapshot_to_media_files
from gelinspect.services.video.pipeline import PipelineState, VideoPipeline
from gelinspect.services.video.recorder import SegmentRecord
from gelinspect.services.video.snapshot import SavedImage, SnapshotResult, load_saved_frames

logger = logging.getLogger("gelinspect.services.engine")

FLIP_ANGLE_KEY = "flip_angle_deg"
"""软件在单管特征快照里补充的键：这一排翻转时编码器读到的角度（算法特征之外，不由算法给出）。"""

SYSTEM_ACTOR = "system"
"""系统行为在审计中的操作者。"""

ACTIVE_STATES = frozenset(
    {ExperimentState.INCUBATING, ExperimentState.READING, ExperimentState.ANALYZING}
)
"""运行中的状态。进程退出后还停留在这些状态，说明上次没有正常结束。"""

_FINISHED_STATES = frozenset({ExperimentState.COMPLETED, ExperimentState.PENDING_REVIEW})
_MAX_CONSECUTIVE_ERRORS = 3
_FUTURE_POLL_S = 0.02
_FRAME_POLL_S = 0.05
_AUDITED_VIDEO_EVENTS = {
    VideoEventCode.SEGMENT_INTERRUPTED: AlertCode.SEGMENT_INTERRUPTED,
    VideoEventCode.PIN_OVERFLOW: AlertCode.VIDEO_QUALITY,
    VideoEventCode.BLACK_FRAME: AlertCode.VIDEO_QUALITY,
    VideoEventCode.FROZEN_FRAME: AlertCode.VIDEO_QUALITY,
}

_SHUTDOWN = object()
"""收件箱里的退出命令。"""

T = TypeVar("T")


class _CancelledError(Exception):
    """收到了停止请求，当前步骤不再继续。"""


class _RunAbortError(Exception):
    """引擎无法继续运行，例如数据库已不可写。"""


@dataclass(frozen=True, slots=True)
class EngineDeps:
    """引擎的全部依赖。

    Attributes:
        device: 设备。
        pipeline: 视频管线。
        analyzer: 算法。
        storage: 存储。
        config: 引擎配置。
        mapping: 三态到阴阳性的映射。
        clock: 真实时钟，必须与设备、视频帧的时间同一基准。
        schedule_clock: 调度时钟，读数是设备时间，只用 ``monotonic``。
        analyzer_config_path: 传给 ``GelAnalyzer.load`` 的配置。
        events: 事件分发器。
    """

    device: DeviceController
    pipeline: VideoPipeline
    analyzer: GelAnalyzer
    storage: TransactionalStorage
    config: EngineConfig
    mapping: GelStateMapping
    clock: Clock
    schedule_clock: Clock
    analyzer_config_path: Path | None
    events: EventDispatcher


@dataclass(slots=True)
class _Row:
    """一排在本次运行中的上下文。"""

    row_index: int
    tubes: tuple[int, ...]
    phase: RowPhase = RowPhase.WAITING
    deadline: float = 0.0
    attempt: int = 0
    files: list[SavedImage] = field(default_factory=list)
    flip_angle_deg: float | None = None
    maybe_flipped: bool = False
    analysis_started_s: float | None = None
    timeout_reported: bool = False

    @property
    def label(self) -> str:
        """显示标签。"""
        return row_label(self.row_index)


@dataclass(slots=True)
class _Capture:
    """一次读数流程的产出。"""

    snapshot: SnapshotResult | None = None
    flip: FlipResult | None = None
    record: SegmentRecord | None = None
    failure: str | None = None
    failure_kind: FaultKind = FaultKind.DEVICE
    restore_failure: str | None = None
    segment_failure: str | None = None


@dataclass(frozen=True, slots=True)
class _Resume:
    user: User
    analysis_only: bool


class _Inbox:
    """引擎线程的收件箱。其他线程只通过它和引擎通信。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._items: deque[object] = deque()

    def post(self, item: object) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify_all()

    def take_all(self) -> list[object]:
        with self._cond:
            items = list(self._items)
            self._items.clear()
            return items

    def wait(self, timeout_s: float) -> None:
        with self._cond:
            if not self._items:
                self._cond.wait(timeout_s)


class EngineRun:
    """一次试验运行，从启动前检查到资源全部释放。"""

    def __init__(
        self,
        experiment: Experiment,
        user: User,
        deps: EngineDeps,
        *,
        recovery: Mapping[int, RowRecovery] | None = None,
    ) -> None:
        """准备运行，不启动线程。

        Args:
            experiment: 处于就绪状态的试验，所有权转移给引擎线程。离线重试时是异常状态的试验。
            user: 启动者。
            deps: 依赖。
            recovery: 不为 None 时是软件重启后的离线重试分析：只分析这些排，不碰设备和视频。
        """
        self._exp = experiment
        self._user = user
        self._deps = deps
        self._cfg = deps.config
        self._device = deps.device
        self._pipeline = deps.pipeline
        self._clock = deps.clock
        self._sched = deps.schedule_clock
        self._storage = deps.storage
        self._events = deps.events

        self._inbox = _Inbox()
        self._stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._stop_request: tuple[User, str] | None = None
        self._ended = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker = AnalysisWorker(
            deps.analyzer,
            deps.clock,
            self._inbox.post,
            self._inbox.post,
            archive=lambda request, result: archive_masks(request, result, self._clock.now()),
        )

        rows: dict[int, list[int]] = {}
        for assignment in experiment.assignments:
            rows.setdefault(tube_to_position(assignment.tube_no).row_index, []).append(
                assignment.tube_no
            )
        self._rows = {
            index: _Row(index, tuple(sorted(tubes))) for index, tubes in sorted(rows.items())
        }
        self._pending: set[int] = set()
        self._deferred: list[AnalysisOutcome] = []
        self._pending_media: list[MediaFile] = []
        self._analyzer_info: AnalyzerInfo | None = None

        self._finished = False
        self._owns_pipeline = False
        self._video_started = False
        self._device_connected = False
        self._tray_in = False
        self._listening_video = False

        self._fault_kinds: set[FaultKind] = set()
        self._fault_reason: str | None = None
        self._device_problem: str | None = None
        self._door_open = False
        self._video_down_since: float | None = None
        self._excursion_since: float | None = None
        self._temperature_faulted = False
        self._temperature_c: float | None = None
        self._last_status_poll = float("-inf")
        self._last_countdown = float("-inf")
        self._last_temperature_log = float("-inf")
        self._offline = recovery is not None
        if recovery is not None:
            self._apply_recovery(recovery)
        self._snapshot = self._build_snapshot(running=True)

    # ------------------------------------------------------------------ 任意线程

    @property
    def experiment_id(self) -> str:
        """试验标识。"""
        return self._exp.experiment_id

    @property
    def snapshot(self) -> EngineSnapshot:
        """最新的只读快照，引用赋值是原子的，可以在任意线程读取。"""
        return self._snapshot

    @property
    def alive(self) -> bool:
        """运行是否还没有结束。"""
        return not self._ended.is_set()

    @property
    def analyzer_busy(self) -> bool:
        """算法线程是否还没退出。运行结束后算法仍卡着时为 True。"""
        return self._worker.alive

    def start(self) -> None:
        """启动引擎线程。"""
        self._events.start()
        self._thread = threading.Thread(target=self._run, name="experiment-engine", daemon=True)
        self._thread.start()

    def join(self, timeout_s: float | None) -> bool:
        """等待运行结束。

        Args:
            timeout_s: 时限，None 表示一直等。

        Returns:
            运行是否已结束。
        """
        return self._ended.wait(timeout_s)

    def request_stop(self, user: User, reason: str) -> bool:
        """请求停止。非阻塞，引擎线程在下一个安全点执行。

        Args:
            user: 操作者。
            reason: 原因。

        Returns:
            运行已结束时为 False。
        """
        if self._ended.is_set():
            return False
        with self._stop_lock:
            if self._stop_request is None:
                self._stop_request = (user, reason)
        self._stop_event.set()
        self._inbox.post(None)
        return True

    def request_resume(self, user: User, *, analysis_only: bool) -> bool:
        """请求从异常恢复。结果通过状态变化或 ``RESUME_REJECTED`` 告警体现。

        Args:
            user: 操作者。
            analysis_only: 是否只重试分析。

        Returns:
            运行已结束时为 False。
        """
        if self._ended.is_set():
            return False
        self._inbox.post(_Resume(user, analysis_only))
        return True

    def request_shutdown(self) -> None:
        """请求结束运行。试验还在进行时转异常，不继续也不算停止。"""
        self._inbox.post(_SHUTDOWN)

    def _on_video_event(self, event: VideoEvent) -> None:
        self._inbox.post(event)

    # ------------------------------------------------------------------ 线程体

    def _apply_recovery(self, recovery: Mapping[int, RowRecovery]) -> None:
        """按库内结果和媒体记录恢复各排的阶段。"""
        for row in self._rows.values():
            if all(tube in self._exp.results for tube in row.tubes):
                row.phase = RowPhase.DONE
            elif row.row_index in recovery:
                row.phase = RowPhase.ANALYSIS_FAILED
                row.files = list(recovery[row.row_index].files)
                row.flip_angle_deg = recovery[row.row_index].flip_angle_deg
            else:
                row.phase = RowPhase.READ_FAILED
        self._fault_kinds.add(FaultKind.ANALYSIS)
        self._fault_reason = "软件重启后重试分析"

    def _start_offline(self) -> None:
        """离线重试的开始：加载算法，然后走和在线重试相同的恢复流程。"""
        try:
            self._analyzer_info = self._await(self._worker.load(self._deps.analyzer_config_path))
        except _CancelledError:
            self._finished = True
            return
        except Exception as exc:
            self._finished = True
            self._alert(
                AlertCode.RESUME_REJECTED,
                AlertLevel.ERROR,
                f"不能重试分析：算法加载失败：{exc}",
                user=self._user,
            )
            return
        self._resume(_Resume(self._user, analysis_only=True))

    def _run(self) -> None:
        try:
            self._worker.start()
            if self._offline:
                self._start_offline()
                self._loop()
            elif self._preflight():
                self._loop()
        except _RunAbortError as exc:
            logger.error("试验 %s 的运行已中止：%s", self.experiment_id, exc)
        except Exception:
            logger.exception("试验 %s 的引擎线程发生未预期的异常", self.experiment_id)
        finally:
            self._teardown()
            self._snapshot = self._build_snapshot(running=False)
            self._events.stop(self._cfg.worker_stop_timeout_s)
            self._ended.set()

    def _loop(self) -> None:
        errors = 0
        while not self._finished:
            try:
                self._step()
                errors = 0
            except _RunAbortError:
                raise
            except Exception as exc:
                errors += 1
                self._on_internal_error(exc, errors)

    def _offline_step(self) -> None:
        """离线重试的主循环：只等分析结局和停止，没有设备、视频和排程。"""
        self._drain_inbox()
        if self._finished:
            return
        if self._stop_event.is_set():
            self._perform_stop()
            return
        self._check_analysis_timeouts()
        state = self._exp.state
        if state in _FINISHED_STATES or (state is ExperimentState.FAULTED and not self._pending):
            self._finished = True
            return
        self._inbox.wait(self._cfg.tick_s)

    def _step(self) -> None:
        if self._offline:
            self._offline_step()
            return
        self._drain_inbox()
        if self._finished:
            return
        if self._stop_event.is_set():
            self._perform_stop()
            return
        self._poll_status()
        self._check_analysis_timeouts()
        self._check_video_grace()
        self._emit_countdowns()
        state = self._exp.state
        if state in _FINISHED_STATES:
            self._finished = True
            return
        if state in (ExperimentState.INCUBATING, ExperimentState.ANALYZING):
            row = self._next_due_row()
            if row is not None:
                self._read_row(row)
                return
        self._inbox.wait(self._cfg.tick_s)

    def _next_due_row(self) -> _Row | None:
        if self._video_down_since is not None or self._door_open:
            return None
        now = self._sched.monotonic()
        for row in self._rows.values():
            if row.phase is RowPhase.WAITING and row.deadline <= now:
                return row
        return None

    # ------------------------------------------------------------------ 启动前检查

    def _preflight(self) -> bool:
        """连接设备、加载算法、启动视频，都没问题才送入滑台并启动孵育。

        任何一步失败，试验都还是就绪状态，换好条件可以重新启动。

        Returns:
            是否已启动孵育。
        """
        try:
            self._prepare()
        except _CancelledError:
            user, reason = self._stop_request or (self._user, "")
            self._alert(
                AlertCode.START_CANCELLED,
                AlertLevel.INFO,
                f"启动前检查期间被取消，试验仍为就绪。{reason}".strip(),
                user=user,
            )
            return False
        except Exception as exc:
            logger.warning("试验 %s 启动失败：%s", self.experiment_id, exc)
            self._alert(
                AlertCode.START_FAILED,
                AlertLevel.ERROR,
                f"启动失败，试验仍为就绪：{exc}",
                user=self._user,
            )
            return False
        info = self._analyzer_info
        # 截止时间先于状态：看到 incubating 的观察者读到的倒计时就是对的。
        deadline = self._sched.monotonic() + self._cfg.incubation_s
        for row in self._rows.values():
            row.deadline = deadline
        self._transact(
            events=[ExperimentEvent.START],
            user=self._user,
            reason=(
                f"孵育 {self._cfg.incubation_minutes:g} 分钟，"
                f"时间加速 {self._cfg.time_scale:g} 倍，"
                f"排 {sorted(self._rows)}，模型 {info.model_version if info else '?'}，"
                f"配置 {info.config_version if info else '?'}，"
                f"映射 {self._deps.mapping.to_config()}"
            ),
            then=[
                e for r in self._rows.values() for e in self._well_events(r, WellStatus.INCUBATING)
            ],
        )
        return True

    def _prepare(self) -> None:
        self._check_cancelled()
        self._device.connect()
        self._device_connected = True
        status = self._read_status()
        problem = self._health_problem(status)
        if problem is not None:
            raise DeviceError(problem)
        if isinstance(self._device, PlateAwareDevice):
            self._device.load_plate_plan(
                PlatePlan(
                    experiment_id=self.experiment_id,
                    labeled_sensitivity=labeled_sensitivity_of(self._exp),
                    tubes=tuple(
                        PlateTube(a.tube_no, a.role, a.spiked_endotoxin)
                        for a in self._exp.assignments
                    ),
                )
            )
        self._check_cancelled()
        self._analyzer_info = self._await(self._worker.load(self._deps.analyzer_config_path))
        self._check_cancelled()
        self._owns_pipeline = not self._pipeline.is_running
        self._pipeline.add_event_listener(self._on_video_event)
        self._listening_video = True
        self._pipeline.start()
        self._video_started = True
        if not self._wait_fresh_frames(self._cfg.video_ready_timeout_s):
            raise VideoError("视频源没有产出画面")
        self._check_cancelled()
        self._device.move_tray(TrayPosition.IN)
        self._tray_in = True
        self._check_cancelled()

    def _check_cancelled(self) -> None:
        if self._stop_event.is_set():
            raise _CancelledError

    def _await(self, future: Future[T]) -> T:
        while not future.done():
            wait([future], _FUTURE_POLL_S)
            self._check_cancelled()
        return future.result()

    def _health_problem(self, status: DeviceStatus) -> str | None:
        """检查是否具备运行条件：通信、故障、舱门、温度。"""
        problem = self._device_fault_text(status)
        if problem is not None:
            return problem
        if status.door_closed is False:
            return "舱门未关"
        if not self._temperature_ok(status.temperature_c):
            return (
                f"温度 {status.temperature_c} ℃ 不在 "
                f"{self._cfg.temperature_target_c:g}±{self._cfg.temperature_tolerance_c:g} ℃ 内"
            )
        return None

    @staticmethod
    def _device_fault_text(status: DeviceStatus) -> str | None:
        if not status.connected:
            return "设备通信断开"
        if status.faults:
            return "设备故障 " + "；".join(f"{f.code}：{f.message}" for f in status.faults)
        return None

    def _temperature_ok(self, celsius: float | None) -> bool:
        if celsius is None:
            return False
        return abs(celsius - self._cfg.temperature_target_c) <= self._cfg.temperature_tolerance_c

    def _read_status(self) -> DeviceStatus:
        try:
            return self._device.read_status()
        except Exception as exc:  # 契约要求不抛异常，实现违约时按通信断开处理
            logger.warning("读取设备状态抛出异常：%r", exc)
            return DeviceStatus(connected=False, read_at=self._clock.now())

    def _wait_fresh_frames(self, timeout_s: float) -> bool:
        """等待调用之后产出的新帧。比需要的多等一帧：跨过调用时刻的那一帧可能拍的是旧画面。

        Args:
            timeout_s: 时限，真实时间。

        Returns:
            凑够时为 True，超时为 False。

        Raises:
            _CancelledError: 等待期间收到停止请求。
        """
        since = self._clock.now()
        need = self._cfg.frames_per_side + 1
        deadline = self._clock.monotonic() + timeout_s
        while True:
            frames, _ = self._pipeline.buffer.wait_for_frames_from(since, need, _FRAME_POLL_S)
            if len(frames) >= need:
                return True
            self._check_cancelled()
            if self._clock.monotonic() >= deadline:
                return False

    # ------------------------------------------------------------------ 读数

    def _read_row(self, row: _Row) -> None:
        late_s = self._sched.monotonic() - row.deadline
        row.phase = RowPhase.READING
        try:
            self._transact(
                events=[ExperimentEvent.ROW_DUE],
                reason=f"{row.label} 到时，孵育 {self._cfg.incubation_s + late_s:.1f} 秒",
                then=self._well_events(row, WellStatus.READING),
            )
        except Exception:
            row.phase = RowPhase.WAITING
            raise
        if late_s > self._cfg.row_late_warn_s:
            self._alert(
                AlertCode.ROW_LATE,
                AlertLevel.WARNING,
                f"{row.label} 晚于到时 {late_s:.0f} 秒才开始读数",
                row_index=row.row_index,
            )
        self._finish_capture(row, self._capture_row(row))

    def _capture_row(self, row: _Row) -> _Capture:
        """执行一排的硬件流程，无论结果如何都收拾干净：不留录制段、pin 和翻转状态。

        Args:
            row: 要读数的排。

        Returns:
            读数产出。预期内的设备和视频故障写在 ``failure`` 里，不抛异常。
        """
        capture = _Capture()
        experiment_id = self.experiment_id
        index = row.row_index
        segment_started = False
        marked = False
        try:
            self._check_cancelled()
            self._device.select_row(index)
            self._check_cancelled()
            if not self._wait_fresh_frames(self._cfg.preflip_frame_timeout_s):
                capture.failure = f"{row.label} 翻转前等不到画面，未翻转"
                capture.failure_kind = FaultKind.VIDEO
                return capture
            self._pipeline.start_segment(experiment_id, index)
            segment_started = True
            self._pipeline.mark_flip_start(self._clock.now(), index, n=self._cfg.frames_per_side)
            marked = True
            self._check_cancelled()
            row.maybe_flipped = True
            capture.flip = self._device.flip_row(index)
            self._check_cancelled()
            capture.snapshot = self._grab(row, capture.flip)
        except _CancelledError:
            logger.info("%s 的读数被停止请求中断", row.label)
        except InsufficientFramesError as exc:
            if not self._stop_event.is_set():
                capture.failure = f"{row.label} 抓帧失败：{exc}"
                capture.failure_kind = FaultKind.VIDEO
        except VideoError as exc:
            capture.failure = f"{row.label} 视频失败：{exc}"
            capture.failure_kind = FaultKind.VIDEO
        except DeviceError as exc:
            capture.failure = f"{row.label} 设备动作失败：{exc}"
            capture.failure_kind = FaultKind.DEVICE
        except Exception as exc:
            logger.exception("%s 读数时发生未预期的异常", row.label)
            capture.failure = f"{row.label} 读数失败：{exc!r}"
            capture.failure_kind = FaultKind.INTERNAL
        finally:
            if marked:
                self._pipeline.snapshots.cancel_mark(index)
            if row.maybe_flipped:
                capture.restore_failure = self._restore(row)
            if segment_started:
                self._stop_segment(capture)
        return capture

    def _grab(self, row: _Row, flip: FlipResult) -> SnapshotResult:
        """抓翻转前后各 n 帧。用异步接口，等待期间能响应停止请求和视频源失败。"""
        future = self._pipeline.grab_async(
            row.row_index,
            self._cfg.frames_per_side,
            experiment_id=self.experiment_id,
            flip_completed_at=flip.completed_at,
        )
        while not future.done():
            wait([future], _FUTURE_POLL_S)
            hopeless = self._pipeline.state in (PipelineState.FAILED, PipelineState.ENDED)
            if self._stop_event.is_set() or hopeless:
                # 取消是一次性的，必须重复调用：等待方可能还没进入等待。
                self._pipeline.buffer.cancel_waits()
        return future.result()

    def _restore(self, row: _Row) -> str | None:
        """复位一排。

        Returns:
            失败原因，成功时为 None。``maybe_flipped`` 保持为真，恢复或停止时会再试。
        """
        try:
            self._device.restore_row(row.row_index)
        except Exception as exc:
            logger.warning("%s 复位失败：%r", row.label, exc)
            return f"{row.label} 复位失败，可能仍处于翻转状态，需要人工检查：{exc}"
        row.maybe_flipped = False
        return None

    def _stop_segment(self, capture: _Capture) -> None:
        try:
            capture.record = self._pipeline.stop_segment()
        except RecorderError as exc:
            capture.segment_failure = str(exc)
            # 收尾超时时段还挂着，这里摘掉，保证不留悬挂的录制段。
            self._pipeline.recorder.abort_segment("stop_failed")

    def _finish_capture(self, row: _Row, capture: _Capture) -> None:
        now = self._clock.now()
        if capture.record is not None:
            self._pending_media.append(segment_to_media_file(capture.record, created_at=now))
        if capture.snapshot is not None:
            self._pending_media.extend(
                snapshot_to_media_files(
                    capture.snapshot,
                    created_at=now,
                    flip_angle_deg=None if capture.flip is None else capture.flip.actual_angle_deg,
                )
            )
        if capture.segment_failure is not None:
            self._alert(
                AlertCode.SEGMENT_INTERRUPTED,
                AlertLevel.ERROR,
                f"{row.label} 录制段异常：{capture.segment_failure}",
                row_index=row.row_index,
            )
        if self._stop_event.is_set():
            # 已产生的文件随 STOP 事务入库，不再分析。
            if capture.snapshot is None:
                row.phase = RowPhase.READ_FAILED
            self._alert_restore(row, capture)
            return
        snapshot = capture.snapshot
        flip = capture.flip
        if snapshot is None or flip is None:
            row.phase = RowPhase.READ_FAILED
            self._publish_wells(row, WellStatus.ERROR)
            self._alert_restore(row, capture)
            self._fault(
                capture.failure_kind,
                capture.failure or f"{row.label} 读数失败",
                row_index=row.row_index,
                code=AlertCode.READ_FAILED,
            )
            return

        row.phase = RowPhase.ANALYZING
        row.attempt += 1
        row.files = list(snapshot.files)
        row.flip_angle_deg = flip.actual_angle_deg
        row.analysis_started_s = None
        row.timeout_reported = False
        self._pending.add(row.row_index)
        record = capture.record
        note = "；录像不完整" if record is not None and record.interrupted else ""
        try:
            self._transact(
                events=[ExperimentEvent.ROW_CAPTURED],
                reason=(
                    f"{row.label} 抓帧完成，{len(snapshot.files)} 张原图，"
                    f"角度 {flip.actual_angle_deg}{note}"
                ),
                then=[
                    RowCaptured(
                        experiment_id=self.experiment_id,
                        at=self._clock.now(),
                        row_index=row.row_index,
                        flip_angle_deg=flip.actual_angle_deg,
                        image_count=len(snapshot.files),
                    ),
                    *self._well_events(row, WellStatus.ANALYZING),
                ],
            )
        except Exception:
            row.phase = RowPhase.READ_FAILED
            self._pending.discard(row.row_index)
            raise
        before, after = snapshot.frames_before, snapshot.frames_after
        self._worker.submit(
            row.row_index,
            row.attempt,
            lambda: self._build_request(row, before, after),
        )
        if capture.restore_failure is not None:
            self._fault(
                FaultKind.DEVICE,
                capture.restore_failure,
                row_index=row.row_index,
                code=AlertCode.RESTORE_FAILED,
            )

    def _alert_restore(self, row: _Row, capture: _Capture) -> None:
        if capture.restore_failure is not None:
            self._alert(
                AlertCode.RESTORE_FAILED,
                AlertLevel.ERROR,
                capture.restore_failure,
                row_index=row.row_index,
            )

    def _build_request(
        self, row: _Row, before: Sequence[CapturedFrame], after: Sequence[CapturedFrame]
    ) -> RowAnalysisRequest:
        lal = next(
            (r.batch_no for r in self._exp.reagents if r.kind is ReagentKind.LAL_REAGENT), None
        )
        return RowAnalysisRequest(
            experiment_id=self.experiment_id,
            row_index=row.row_index,
            tubes=[TubeSlot.from_tube_no(no) for no in row.tubes],
            frames_before=list(before),
            frames_after=list(after),
            flip_angle_deg=row.flip_angle_deg,
            reagent_batch=lal,
        )

    # ------------------------------------------------------------------ 分析结局

    def _drain_inbox(self) -> None:
        for item in self._inbox.take_all():
            if isinstance(item, AnalysisStarted):
                row = self._rows[item.row_index]
                if item.attempt == row.attempt:
                    row.analysis_started_s = item.started_monotonic_s
            elif isinstance(item, AnalysisOutcome):
                self._apply_outcome(item)
            elif isinstance(item, VideoEvent):
                self._handle_video_event(item)
            elif isinstance(item, _Resume):
                self._resume(item)
            elif item is _SHUTDOWN:
                self._perform_shutdown()
                return

    def _apply_outcome(self, outcome: AnalysisOutcome) -> None:
        row = self._rows[outcome.row_index]
        if outcome.attempt != row.attempt or row.phase is not RowPhase.ANALYZING:
            logger.info("丢弃 %s 过期的分析结局（第 %d 次）", row.label, outcome.attempt)
            return
        if self._exp.state is not ExperimentState.ANALYZING:
            # 异常期间不能写结果（领域规则），恢复后再处理，结果不会丢。
            self._deferred.append(outcome)
            return
        self._report_timeout(row, outcome.wall_s)
        self._pending.discard(row.row_index)
        if outcome.result is None:
            self._apply_analysis_failure(row, outcome.error)
            return
        row.phase = RowPhase.DONE
        media_before = len(self._pending_media)
        try:
            results = self._map_results(row, outcome.result)
            # 掩码存档记录和结果同一事务入库。
            self._pending_media.extend(outcome.media)
            self._transact(
                results=results,
                audit=[self._row_audit(row, outcome.result, results)],
                events=self._settle_events,
                reason=f"{row.label} 分析完成",
                then=[self._result_event(row, result) for result in results],
            )
        except Exception:
            # 算法结果没能入库：按分析失败处理，原图还在，可以重试。掩码文件成了孤儿，不入库。
            del self._pending_media[media_before:]
            row.phase = RowPhase.ANALYSIS_FAILED
            raise

    def _apply_analysis_failure(self, row: _Row, error: Exception | None) -> None:
        message = f"{row.label} 分析失败，原图已保留，可以重试：{error}"
        row.phase = RowPhase.ANALYSIS_FAILED
        self._publish_wells(row, WellStatus.ERROR)
        self._alert(AlertCode.ANALYSIS_FAILED, AlertLevel.ERROR, message, row_index=row.row_index)
        self._transact(events=self._settle_events, reason=f"{row.label} 分析失败")

    def _settle_events(self) -> Sequence[ExperimentEvent]:
        """全部结局到齐后，决定试验的下一步。算法结果写入内存之后才调用。"""
        if self._pending:
            return ()
        phases = {row.phase for row in self._rows.values()}
        if RowPhase.WAITING in phases:
            return (ExperimentEvent.ROW_ANALYZED,)
        if RowPhase.ANALYSIS_FAILED in phases:
            failed = [r.label for r in self._rows.values() if r.phase is RowPhase.ANALYSIS_FAILED]
            self._fault_kinds.add(FaultKind.ANALYSIS)
            self._fault_reason = f"{'、'.join(failed)} 分析失败，等待重试"
            return (ExperimentEvent.FAULT,)
        if self._offline and RowPhase.READ_FAILED in phases:
            unread = [r.label for r in self._rows.values() if r.phase is RowPhase.READ_FAILED]
            self._fault_reason = (
                f"{'、'.join(unread)} 没有读数，上次运行中断后错过了读数时间，不能续读；"
                "已抓帧的排已经重新分析。这个试验只能停止"
            )
            return (ExperimentEvent.FAULT,)
        if self._exp.pending_review_tubes:
            return (ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW,)
        return (ExperimentEvent.ALL_ANALYZED_CLEAN,)

    def _map_results(self, row: _Row, result: RowAnalysisResult) -> list[TubeResult]:
        """把算法结果映射成单管结果。缺的、重复的管号转待复核，绝不默认判阴性。"""
        info = result.analyzer_info
        analyses: dict[int, list[TubeResult]] = {}
        for tube in result.tubes:
            analyses.setdefault(tube.tube_no, []).append(
                TubeResult.from_analysis(
                    tube_no=tube.tube_no,
                    status=tube.status,
                    state=tube.state,
                    mapping=self._deps.mapping,
                    score=tube.score,
                    confidence=tube.confidence,
                    features={**tube.features.model_dump(), FLIP_ANGLE_KEY: row.flip_angle_deg},
                    model_version=info.model_version,
                    config_version=info.config_version,
                    message=tube.message,
                    elapsed_ms=result.elapsed_ms,
                )
            )

        def failed(tube_no: int, message: str) -> TubeResult:
            return TubeResult.from_analysis(
                tube_no=tube_no,
                status=TubeAnalysisStatus.FAILED,
                state=None,
                mapping=self._deps.mapping,
                model_version=info.model_version,
                config_version=info.config_version,
                message=message,
                elapsed_ms=result.elapsed_ms,
            )

        mapped: list[TubeResult] = []
        problems: list[str] = []
        for tube_no in row.tubes:
            entries = analyses.get(tube_no, [])
            if len(entries) == 1:
                mapped.append(entries[0])
            elif entries:
                problems.append(f"管号 {tube_no} 重复")
                mapped.append(failed(tube_no, "算法对该管返回了多条结果"))
            else:
                problems.append(f"缺少管号 {tube_no}")
                mapped.append(failed(tube_no, "算法没有返回该管的结果"))
        extra = sorted(set(analyses) - set(row.tubes))
        if extra:
            problems.append(f"多余管号 {extra}")
        if problems:
            self._alert(
                AlertCode.ANALYSIS_CONTRACT,
                AlertLevel.ERROR,
                f"{row.label} 的算法结果违反契约：{'；'.join(problems)}",
                row_index=row.row_index,
            )
        return mapped

    def _row_audit(
        self, row: _Row, result: RowAnalysisResult, mapped: Sequence[TubeResult]
    ) -> AuditEvent:
        tubes = "；".join(
            f"{r.tube_no}号 {r.analysis_status.value}/"
            f"{r.gel_state.value if r.gel_state else '-'}→{r.auto_interpretation.value}"
            f"（score={'-' if r.score is None else format(r.score, '.3f')}）"
            for r in mapped
        )
        info = result.analyzer_info
        return self._audit_event(
            AuditAction.ROW_ANALYZED,
            f"{row.label} 第 {row.attempt} 次分析完成：模型 {info.model_version}，"
            f"配置 {info.config_version}，耗时 {result.elapsed_ms:.0f} ms；{tubes}",
        )

    def _result_event(self, row: _Row, result: TubeResult) -> WellUpdated:
        interpretation = result.auto_interpretation
        status = {
            Interpretation.POSITIVE: WellStatus.POSITIVE,
            Interpretation.NEGATIVE: WellStatus.NEGATIVE,
            Interpretation.PENDING_REVIEW: WellStatus.PENDING_REVIEW,
        }[interpretation]
        return WellUpdated(
            experiment_id=self.experiment_id,
            at=self._clock.now(),
            row_index=row.row_index,
            tube_no=result.tube_no,
            status=status,
            interpretation=interpretation,
        )

    def _check_analysis_timeouts(self) -> None:
        now = self._clock.monotonic()
        for index in self._pending:
            row = self._rows[index]
            if row.analysis_started_s is not None:
                self._report_timeout(row, now - row.analysis_started_s)

    def _report_timeout(self, row: _Row, elapsed_s: float) -> None:
        limit = self._cfg.analysis_timeout_s
        if row.timeout_reported or elapsed_s <= limit:
            return
        row.timeout_reported = True
        self._alert(
            AlertCode.ANALYSIS_TIMEOUT,
            AlertLevel.WARNING,
            f"{row.label} 分析超过 {limit:g} 秒（已用 {elapsed_s:.2f} 秒），继续等待结果",
            row_index=row.row_index,
            action=AuditAction.ANALYSIS_TIMEOUT,
        )

    # ------------------------------------------------------------------ 监测

    def _poll_status(self) -> None:
        now = self._clock.monotonic()
        if now - self._last_status_poll < self._cfg.status_poll_s:
            return
        self._last_status_poll = now
        status = self._read_status()
        self._check_device(status)
        self._check_door(status)
        self._check_temperature(status)
        self._refresh_snapshot()

    def _check_device(self, status: DeviceStatus) -> None:
        problem = self._device_fault_text(status)
        if problem == self._device_problem:
            return
        self._device_problem = problem
        if problem is not None:
            self._fault(FaultKind.DEVICE, problem, code=AlertCode.DEVICE_FAULT)

    def _check_door(self, status: DeviceStatus) -> None:
        door_open = status.door_closed is False
        if door_open == self._door_open:
            return
        self._door_open = door_open
        self._alert(
            AlertCode.DOOR_OPEN,
            AlertLevel.WARNING if door_open else AlertLevel.INFO,
            "舱门被打开，到时的排等舱门关闭后才读数" if door_open else "舱门已关闭",
        )

    def _check_temperature(self, status: DeviceStatus) -> None:
        if not status.connected:
            return  # 通信断开已按设备故障处理，这里没有温度可判断
        celsius = status.temperature_c
        in_range = self._temperature_ok(celsius)
        self._temperature_c = celsius
        self._events.emit(
            TemperatureChanged(
                experiment_id=self.experiment_id,
                at=status.read_at,
                celsius=celsius,
                in_range=in_range,
            )
        )
        self._log_temperature(status)
        now = self._sched.monotonic()
        if in_range:
            if self._excursion_since is not None:
                self._excursion_since = None
                self._temperature_faulted = False
                self._alert(
                    AlertCode.TEMPERATURE_RECOVERED, AlertLevel.INFO, f"温度回到范围内：{celsius} ℃"
                )
            return
        limits = f"{self._cfg.temperature_target_c:g}±{self._cfg.temperature_tolerance_c:g} ℃"
        if self._excursion_since is None:
            self._excursion_since = now
            self._alert(
                AlertCode.TEMPERATURE_EXCURSION,
                AlertLevel.WARNING,
                f"温度 {celsius} ℃ 超出 {limits}",
            )
        duration = now - self._excursion_since
        if not self._temperature_faulted and duration >= self._cfg.temperature_hold_s:
            self._temperature_faulted = True
            self._fault(
                FaultKind.TEMPERATURE,
                f"温度持续 {duration:.0f} 秒超出 {limits}，最近读数 {celsius} ℃",
                code=AlertCode.TEMPERATURE_EXCURSION,
            )

    def _log_temperature(self, status: DeviceStatus) -> None:
        now = self._sched.monotonic()
        if status.temperature_c is None:
            return
        if now - self._last_temperature_log < self._cfg.temperature_log_interval_s:
            return
        self._last_temperature_log = now
        sample = TemperatureSample(
            recorded_at=status.read_at,
            channel="incubator",
            celsius=status.temperature_c,
            experiment_id=self.experiment_id,
        )
        try:
            self._transact(temperatures=[sample])
        except Exception as exc:  # 丢一条温度采样不值得中断试验，下一次写失败还会报告
            logger.warning("温度写库失败：%r", exc)
            self._emit_alert(AlertCode.STORAGE_FAILED, AlertLevel.WARNING, f"温度写库失败：{exc}")

    def _handle_video_event(self, event: VideoEvent) -> None:
        if event.code is VideoEventCode.DISCONNECTED:
            if self._video_down_since is None:
                self._video_down_since = self._clock.monotonic()
            self._alert(
                AlertCode.VIDEO_DISCONNECTED, AlertLevel.ERROR, f"视频断流：{event.message}"
            )
        elif event.code is VideoEventCode.RECONNECTED:
            self._video_down_since = None
            self._alert(AlertCode.VIDEO_RECONNECTED, AlertLevel.INFO, event.message)
        elif event.code is VideoEventCode.RECONNECT_FAILED:
            self._fault(
                FaultKind.VIDEO,
                f"视频无法恢复：{event.message}",
                code=AlertCode.VIDEO_DISCONNECTED,
            )
        elif event.code in _AUDITED_VIDEO_EVENTS:
            self._alert(
                _AUDITED_VIDEO_EVENTS[event.code],
                AlertLevel.WARNING if event.level is VideoEventLevel.WARNING else AlertLevel.ERROR,
                event.message,
            )
        elif event.level is not VideoEventLevel.INFO:
            self._emit_alert(
                AlertCode.VIDEO_QUALITY,
                AlertLevel.WARNING if event.level is VideoEventLevel.WARNING else AlertLevel.ERROR,
                event.message,
            )

    def _check_video_grace(self) -> None:
        since = self._video_down_since
        if since is None or FaultKind.VIDEO in self._fault_kinds:
            return
        down_s = self._clock.monotonic() - since
        if down_s >= self._cfg.video_down_grace_s:
            self._fault(
                FaultKind.VIDEO,
                f"视频断流 {down_s:.0f} 秒仍未恢复",
                code=AlertCode.VIDEO_DISCONNECTED,
            )

    def _emit_countdowns(self) -> None:
        now = self._clock.monotonic()
        if now - self._last_countdown < self._cfg.countdown_interval_s:
            return
        self._last_countdown = now
        at = self._clock.now()
        for row in self._rows.values():
            self._events.emit(
                RowCountdown(
                    experiment_id=self.experiment_id,
                    at=at,
                    row_index=row.row_index,
                    phase=row.phase,
                    remaining_s=self._remaining(row),
                )
            )
        self._refresh_snapshot()

    def _remaining(self, row: _Row) -> float:
        return max(0.0, row.deadline - self._sched.monotonic())

    # ------------------------------------------------------------------ 故障

    def _fault(
        self,
        kind: FaultKind,
        reason: str,
        *,
        row_index: int | None = None,
        code: AlertCode = AlertCode.INTERNAL_ERROR,
    ) -> None:
        """转入异常。已经是异常时只追加原因和审计。

        Args:
            kind: 原因分类。
            reason: 说明。
            row_index: 相关的排。
            code: 告警码。
        """
        self._fault_kinds.add(kind)
        self._fault_reason = reason
        if ExperimentEvent.FAULT in allowed_events(self._exp.state):
            self._transact(
                events=[ExperimentEvent.FAULT],
                reason=reason,
                then=[self._alert_event(code, AlertLevel.ERROR, reason, row_index)],
            )
        else:
            self._alert(code, AlertLevel.ERROR, reason, row_index=row_index)

    def _on_internal_error(self, exc: Exception, errors: int) -> None:
        logger.error("引擎内部错误（第 %d 次）：%r", errors, exc, exc_info=exc)
        for row in self._rows.values():
            if row.phase is RowPhase.READING:
                row.phase = RowPhase.READ_FAILED
        storage = isinstance(exc, StorageError)
        code = AlertCode.STORAGE_FAILED if storage else AlertCode.INTERNAL_ERROR
        self._emit_alert(code, AlertLevel.ERROR, f"引擎内部错误：{exc}")
        if errors >= _MAX_CONSECUTIVE_ERRORS:
            raise _RunAbortError(f"连续 {errors} 次内部错误，最后一次：{exc!r}") from exc
        try:
            self._fault(FaultKind.STORAGE if storage else FaultKind.INTERNAL, f"{exc}", code=code)
        except Exception as second:
            # 连异常状态都写不进去：不能再做无审计的操作。库内仍是提交过的旧状态，
            # 下次启动由恢复流程标记为异常。
            raise _RunAbortError(f"无法写入异常状态：{second!r}") from second

    # ------------------------------------------------------------------ 恢复

    def _resume(self, command: _Resume) -> None:
        def reject(why: str) -> None:
            self._alert(AlertCode.RESUME_REJECTED, AlertLevel.WARNING, why, user=command.user)

        if self._exp.state is not ExperimentState.FAULTED:
            reject(f"试验状态是 {self._exp.state.value}，不是异常，无需恢复")
            return
        if self._offline and not command.analysis_only:
            reject("软件重启后调度已经丢失，不能恢复，只能重试分析或停止")
            return
        if command.analysis_only:
            others = self._fault_kinds - {FaultKind.ANALYSIS}
            if others:
                reject(f"异常原因还有 {sorted(k.value for k in others)}，应先排除并使用恢复")
                return
            if not any(r.phase is RowPhase.ANALYSIS_FAILED for r in self._rows.values()):
                reject("没有需要重试分析的排")
                return
        if self._fault_kinds - {FaultKind.ANALYSIS}:
            blocker = self._resume_blocker()
            if blocker is not None:
                reject(f"不能恢复：{blocker}")
                return

        retry = [r for r in self._rows.values() if r.phase is RowPhase.ANALYSIS_FAILED]
        # 离线重试不翻转：没有读数的排留在「读数失败」，全部分析完后试验回到异常。
        reread = (
            []
            if self._offline
            else [r for r in self._rows.values() if r.phase is RowPhase.READ_FAILED]
        )
        event = (
            ExperimentEvent.RESUME_ANALYSIS
            if retry or self._pending or self._deferred
            else ExperimentEvent.RESUME_INCUBATION
        )
        labels = "、".join(r.label for r in retry) or "无"
        again = "、".join(r.label for r in reread) or "无"
        self._transact(
            events=[event],
            user=command.user,
            reason=f"人工恢复（原因：{self._fault_reason}）；重试分析：{labels}；重新读数：{again}",
        )
        self._fault_kinds.clear()
        self._fault_reason = None
        self._device_problem = None
        self._video_down_since = None
        self._excursion_since = None
        self._temperature_faulted = False
        self._last_status_poll = float("-inf")
        for row in reread:
            row.phase = RowPhase.WAITING
            self._publish_wells(row, WellStatus.INCUBATING)
        for row in retry:
            self._retry_analysis(row)
        deferred, self._deferred = self._deferred, []
        for outcome in deferred:
            self._apply_outcome(outcome)
        self._refresh_snapshot()

    def _resume_blocker(self) -> str | None:
        try:
            self._device.connect()
        except DeviceError as exc:
            return f"设备连接失败：{exc}"
        problem = self._health_problem(self._read_status())
        if problem is not None:
            return problem
        for row in self._rows.values():
            if row.maybe_flipped:
                failure = self._restore(row)
                if failure is not None:
                    return failure
        return self._ensure_video()

    def _ensure_video(self) -> str | None:
        state = self._pipeline.state
        if state is PipelineState.RECONNECTING:
            return "视频正在重连"
        try:
            if state is not PipelineState.RUNNING:
                self._pipeline.stop()
                self._pipeline.start()
            if not self._wait_fresh_frames(self._cfg.video_ready_timeout_s):
                return "视频源没有产出画面"
        except VideoError as exc:
            return f"视频无法启动：{exc}"
        except _CancelledError:
            return "收到停止请求"
        return None

    def _retry_analysis(self, row: _Row) -> None:
        row.phase = RowPhase.ANALYZING
        row.attempt += 1
        row.analysis_started_s = None
        row.timeout_reported = False
        self._pending.add(row.row_index)
        self._publish_wells(row, WellStatus.ANALYZING)
        files = list(row.files)

        def build() -> RowAnalysisRequest:
            before, after = load_saved_frames(files)
            return self._build_request(row, before, after)

        self._worker.submit(row.row_index, row.attempt, build)

    # ------------------------------------------------------------------ 停止

    def _perform_stop(self) -> None:
        request = self._stop_request
        assert request is not None
        user, reason = request
        for row in self._rows.values():
            if row.maybe_flipped:
                failure = self._restore(row)
                if failure is not None:
                    self._alert(
                        AlertCode.RESTORE_FAILED,
                        AlertLevel.ERROR,
                        failure,
                        row_index=row.row_index,
                    )
        self._finished = True
        if ExperimentEvent.STOP not in allowed_events(self._exp.state):
            self._alert(
                AlertCode.STOP_IGNORED,
                AlertLevel.INFO,
                f"停止请求到达时试验已是 {self._exp.state.value}，状态不变",
                user=user,
            )
            return
        discarded = [self._rows[i].label for i in sorted(self._pending)]
        unread = [r.label for r in self._rows.values() if r.phase is RowPhase.WAITING]
        self._transact(
            events=[ExperimentEvent.STOP],
            user=user,
            reason=(
                f"用户停止。{reason}".strip()
                + f"；丢弃分析中的排：{discarded or '无'}；未读的排：{unread or '无'}"
            ),
        )

    def _perform_shutdown(self) -> None:
        self._finished = True
        if self._exp.state in ACTIVE_STATES:
            self._fault(FaultKind.SHUTDOWN, "软件退出时试验仍在进行", code=AlertCode.INTERNAL_ERROR)

    def _teardown(self) -> None:
        """释放全部资源。每一步都兜底，前一步失败不影响后一步。"""
        for row in self._rows.values():
            if row.maybe_flipped and self._device_connected:
                self._restore(row)
        if (
            self._cfg.tray_out_on_finish
            and self._tray_in
            and (
                self._exp.state in _FINISHED_STATES
                or self._exp.state in (ExperimentState.STOPPED, ExperimentState.READY)
            )
        ):
            try:
                self._device.move_tray(TrayPosition.OUT)
            except Exception as exc:
                message = f"退出滑台失败：{exc}"
                self._guard(lambda: self._alert(AlertCode.TRAY_FAILED, AlertLevel.WARNING, message))
        if not self._worker.shutdown(self._cfg.worker_stop_timeout_s):
            self._guard(
                lambda: self._alert(
                    AlertCode.WORKER_STUCK,
                    AlertLevel.ERROR,
                    "算法线程未在时限内退出，它会在算法返回后自行结束",
                )
            )
        if self._owns_pipeline:
            self._guard(self._pipeline.stop)
        if self._listening_video:
            self._guard(lambda: self._pipeline.remove_event_listener(self._on_video_event))
        if self._device_connected:
            self._guard(self._device.close)
        if self._pending_media:
            logger.error(
                "试验 %s 有 %d 条媒体记录未能入库", self.experiment_id, len(self._pending_media)
            )

    @staticmethod
    def _guard(action: Callable[[], object]) -> None:
        try:
            action()
        except Exception:
            logger.exception("释放资源时抛出异常（已忽略）")

    # ------------------------------------------------------------------ 事务

    def _transact(
        self,
        *,
        events: Sequence[ExperimentEvent] | Callable[[], Sequence[ExperimentEvent]] = (),
        results: Sequence[TubeResult] = (),
        audit: Sequence[AuditEvent] = (),
        temperatures: Sequence[TemperatureSample] = (),
        reason: str = "",
        user: User | None = None,
        then: Sequence[EngineEvent] = (),
    ) -> None:
        """在一个事务里提交状态、结果、媒体、审计和温度。

        先改内存聚合（走领域规则），提交失败时撤销，提交成功后才发事件，事件入队后才发布快照。

        Args:
            events: 要应用的状态机事件；是函数时在结果写入内存之后才求值。
            results: 单管结果。
            audit: 附加的审计事件，排在状态变化之前。
            temperatures: 温度采样。
            reason: 状态变化的说明。
            user: 操作者，None 表示系统。
            then: 提交成功后跟在状态变化之后发出的事件，同样先于快照。

        Raises:
            IllegalTransitionError: 状态机拒绝事件。
            TransitionGuardError: 前置条件不满足。
            StorageBusyError: 等写锁超过时限。温度采样不值得等，只试一次。
            StorageError: 写库失败。
        """
        exp = self._exp
        state_before = exp.state
        results_before = dict(exp.results)
        completion_before = (exp.conclusion, exp.completed_at, exp.measured_sensitivity)
        media = list(self._pending_media)
        actor = user.username if user is not None else SYSTEM_ACTOR
        changes: list[StateChanged] = []
        records = list(audit)
        try:
            for result in results:
                exp.record_result(result)
            for event in events() if callable(events) else events:
                previous = exp.state
                current = exp.apply(event)
                at = self._clock.now()
                text = reason
                if event is ExperimentEvent.FAULT and self._fault_reason:
                    text = self._fault_reason
                changes.append(
                    StateChanged(
                        experiment_id=exp.experiment_id,
                        at=at,
                        previous=previous,
                        current=current,
                        trigger=event,
                        actor=actor,
                        reason=text,
                    )
                )
                records.append(
                    self._audit_event(
                        AuditAction.EXPERIMENT_STATE_CHANGED,
                        f"{previous.value} → {current.value}（{event.value}）：{text}",
                        user=user,
                    )
                )

            completed = (
                exp.state is ExperimentState.COMPLETED
                and state_before is not ExperimentState.COMPLETED
            )
            if completed:
                # 结论和「已完成」状态同一事务：看到已完成的人一定也能读到结论。
                records.append(
                    self._audit_event(
                        AuditAction.CONCLUSION_RECORDED,
                        finalize_conclusion(exp, self._clock.now()).summary_text(),
                        user=user,
                    )
                )

            def write(uow: UnitOfWork) -> None:
                if exp.state is not state_before:
                    uow.experiments.update_state(
                        exp.experiment_id, exp.state, expected=state_before
                    )
                if results:
                    uow.experiments.save_results(exp.experiment_id, results)
                if completed:
                    assert exp.conclusion is not None and exp.completed_at is not None
                    uow.experiments.record_completion(
                        exp.experiment_id,
                        completed_at=exp.completed_at,
                        measured_sensitivity=exp.measured_sensitivity,
                        conclusion=exp.conclusion,
                    )
                for item in media:
                    uow.media.add(item)
                if records:
                    uow.audit.append_many(records)
                if temperatures:
                    uow.telemetry.add_temperatures(temperatures)

            self._commit(write, patient=not temperatures)
        except Exception:
            exp.state = state_before
            exp.results = results_before
            exp.conclusion, exp.completed_at, exp.measured_sensitivity = completion_before
            raise
        del self._pending_media[: len(media)]
        for change in changes:
            self._events.emit(change)
        for extra in then:
            self._events.emit(extra)
        if changes or results or then:
            self._refresh_snapshot()

    def _commit(self, write: Callable[[UnitOfWork], None], *, patient: bool) -> None:
        """执行一个写事务。写锁被占用时重试，事务没开始过，``write`` 不会执行两次。

        SQLite 的忙等待不排队，短事务的写入者可能连续抢到锁，所以单次 ``busy_timeout`` 超时不代表
        数据库坏了。试验不能因为别人在写库就中止。

        Args:
            write: 事务内容。
            patient: 是否等到 ``storage_busy_timeout_s``，否则只试一次。

        Raises:
            StorageBusyError: 等写锁超过时限。
            StorageError: 写库失败。
        """
        deadline: float | None = None
        while True:
            try:
                with self._storage.unit_of_work() as uow:
                    write(uow)
                return
            except StorageBusyError as exc:
                now = self._clock.monotonic()
                if not patient or (deadline is not None and now >= deadline):
                    raise
                if deadline is None:
                    deadline = now + self._cfg.storage_busy_timeout_s
                    logger.warning("数据库写锁被占用，继续等待：%s", exc)
                    self._emit_alert(
                        AlertCode.STORAGE_BUSY,
                        AlertLevel.WARNING,
                        "数据库被其他操作占用，正在等待写入",
                    )

    def _audit_event(
        self, action: AuditAction, detail: str, *, user: User | None = None
    ) -> AuditEvent:
        return AuditEvent(
            occurred_at=self._clock.now(),
            actor=user.username if user is not None else SYSTEM_ACTOR,
            actor_role=user.role if user is not None else None,
            action=action,
            detail=detail,
            experiment_id=self.experiment_id,
        )

    def _alert(
        self,
        code: AlertCode,
        level: AlertLevel,
        message: str,
        *,
        row_index: int | None = None,
        action: AuditAction = AuditAction.ENGINE_ALERT,
        user: User | None = None,
    ) -> None:
        self._transact(audit=[self._audit_event(action, f"[{code.value}] {message}", user=user)])
        self._emit_alert(code, level, message, row_index)

    def _emit_alert(
        self, code: AlertCode, level: AlertLevel, message: str, row_index: int | None = None
    ) -> None:
        self._events.emit(self._alert_event(code, level, message, row_index))

    def _alert_event(
        self, code: AlertCode, level: AlertLevel, message: str, row_index: int | None = None
    ) -> EngineAlert:
        return EngineAlert(
            experiment_id=self.experiment_id,
            at=self._clock.now(),
            level=level,
            code=code,
            message=message,
            row_index=row_index,
        )

    def _well_events(self, row: _Row, status: WellStatus) -> list[WellUpdated]:
        at = self._clock.now()
        return [
            WellUpdated(
                experiment_id=self.experiment_id,
                at=at,
                row_index=row.row_index,
                tube_no=tube_no,
                status=status,
            )
            for tube_no in row.tubes
        ]

    def _publish_wells(self, row: _Row, status: WellStatus) -> None:
        for event in self._well_events(row, status):
            self._events.emit(event)
        self._refresh_snapshot()

    def _refresh_snapshot(self) -> None:
        self._snapshot = self._build_snapshot(running=True)

    def _build_snapshot(self, *, running: bool) -> EngineSnapshot:
        return EngineSnapshot(
            experiment_id=self._exp.experiment_id,
            state=self._exp.state,
            running=running,
            rows=tuple(
                RowSnapshot(row.row_index, row.tubes, row.phase, self._remaining(row))
                for row in self._rows.values()
            ),
            fault_kinds=frozenset(self._fault_kinds),
            fault_reason=self._fault_reason,
            temperature_c=self._temperature_c,
            offline_retry=self._offline,
        )
