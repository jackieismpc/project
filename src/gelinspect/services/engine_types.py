"""试验引擎的配置、事件、快照和异常。

只依赖标准库、Pydantic 和 domain，不依赖 Qt。P9 用 Qt 信号桥接 :class:`EngineEvent`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from gelinspect.domain.enums import Interpretation
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.interfaces.analysis import MIN_FRAMES_PER_SIDE


class EngineConfig(BaseModel):
    """试验引擎配置。

    时长分两种：标注「设备时间」的按 ``time_scale`` 加速，标注「真实时间」的不加速。
    算法超时、视频重连这些衡量真实软硬件的时长不能加速。

    Attributes:
        incubation_minutes: 每排孵育时长，设备时间，分钟。
        time_scale: 时间加速倍数，设备时间 / 真实时间。真实设备必须为 1。
        frames_per_side: 翻转前后各取多少帧。
        analysis_timeout_s: ``analyze_row`` 的时限，真实时间，超时只告警。
        temperature_target_c: 孵育温度的目标值。
        temperature_tolerance_c: 允许的偏差，默认 ±1 ℃。
        temperature_hold_s: 越限持续多久转异常，设备时间。
        temperature_log_interval_s: 温度写库的间隔，设备时间。
        row_late_warn_s: 读数晚于到时超过这么久时告警，设备时间。
        status_poll_s: 读取设备状态的周期，真实时间。
        countdown_interval_s: 发出倒计时事件的周期，真实时间。
        tick_s: 主循环空闲时的最长等待，真实时间。
        video_ready_timeout_s: 启动前等待视频源出画的时限，真实时间。
        preflip_frame_timeout_s: 翻转前等待对准后的新帧的时限，真实时间，超时不翻转。
        video_down_grace_s: 断流后等待重连的时限，真实时间，超时转异常。
        tray_out_on_finish: 跑完或停止后是否退出滑台。
        worker_stop_timeout_s: 结束时等待工作线程退出的时限，真实时间。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    incubation_minutes: float = Field(default=60.0, ge=0)
    time_scale: float = Field(default=1.0, gt=0)
    frames_per_side: int = Field(default=3, ge=MIN_FRAMES_PER_SIDE)
    analysis_timeout_s: float = Field(default=2.0, gt=0)
    temperature_target_c: float = 37.0
    temperature_tolerance_c: float = Field(default=1.0, gt=0)
    temperature_hold_s: float = Field(default=60.0, ge=0)
    temperature_log_interval_s: float = Field(default=30.0, gt=0)
    row_late_warn_s: float = Field(default=120.0, ge=0)
    status_poll_s: float = Field(default=1.0, gt=0)
    countdown_interval_s: float = Field(default=1.0, gt=0)
    tick_s: float = Field(default=0.05, gt=0)
    video_ready_timeout_s: float = Field(default=30.0, gt=0)
    preflip_frame_timeout_s: float = Field(default=5.0, gt=0)
    video_down_grace_s: float = Field(default=30.0, ge=0)
    tray_out_on_finish: bool = True
    worker_stop_timeout_s: float = Field(default=5.0, gt=0)

    @property
    def incubation_s(self) -> float:
        """每排孵育时长，设备时间，秒。"""
        return self.incubation_minutes * 60.0


class EngineError(Exception):
    """试验引擎异常基类。"""


class EngineBusyError(EngineError):
    """已有试验在运行。设备只有一台，同时只能运行一个试验。"""


class EngineStateError(EngineError):
    """当前状态不允许该操作，例如试验尚未通过试剂校验。"""


class EnginePermissionError(EngineError):
    """操作者没有所需权限。"""


class RowPhase(StrEnum):
    """一排在本次运行中的阶段。"""

    WAITING = "waiting"
    """孵育中，等待到时。"""
    READING = "reading"
    """翻转并抓帧。"""
    ANALYZING = "analyzing"
    """已抓帧，等待算法结果。"""
    DONE = "done"
    """结果已入库。"""
    READ_FAILED = "read_failed"
    """读数失败，恢复后重新读数。"""
    ANALYSIS_FAILED = "analysis_failed"
    """算法整体故障，原图还在，可以重试分析。"""


class WellStatus(StrEnum):
    """孔板上一个孔的显示状态。"""

    INCUBATING = "incubating"
    READING = "reading"
    ANALYZING = "analyzing"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    PENDING_REVIEW = "pending_review"
    ERROR = "error"


class AlertLevel(StrEnum):
    """告警级别。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AlertCode(StrEnum):
    """告警码。"""

    START_FAILED = "start_failed"
    """启动前检查失败，试验仍为就绪。"""
    START_CANCELLED = "start_cancelled"
    """用户在启动前检查期间取消，试验仍为就绪。"""
    STOP_IGNORED = "stop_ignored"
    """停止请求到达时试验已跑完，状态不再改变。"""
    DEVICE_FAULT = "device_fault"
    """设备通信断开、上报故障或动作失败。"""
    DOOR_OPEN = "door_open"
    """舱门被打开。"""
    RESTORE_FAILED = "restore_failed"
    """翻转复位失败，该排可能仍处于翻转状态，需要人工检查。"""
    TRAY_FAILED = "tray_failed"
    """退出滑台失败。"""
    VIDEO_DISCONNECTED = "video_disconnected"
    """视频断流。"""
    VIDEO_RECONNECTED = "video_reconnected"
    """视频已重连。"""
    VIDEO_QUALITY = "video_quality"
    """视频管线的其他告警。"""
    SEGMENT_INTERRUPTED = "segment_interrupted"
    """录制段不完整。"""
    READ_FAILED = "read_failed"
    """一排读数失败。"""
    ROW_LATE = "row_late"
    """读数晚于到时过久。"""
    TEMPERATURE_EXCURSION = "temperature_excursion"
    """温度越限。"""
    TEMPERATURE_RECOVERED = "temperature_recovered"
    """温度回到范围内。"""
    ANALYSIS_TIMEOUT = "analysis_timeout"
    """算法超时，继续等待结果。"""
    ANALYSIS_FAILED = "analysis_failed"
    """算法整体故障。"""
    ANALYSIS_CONTRACT = "analysis_contract"
    """算法结果违反契约，例如缺管号、多管号。"""
    RESUME_REJECTED = "resume_rejected"
    """恢复请求被拒绝，试验保持异常。"""
    WORKER_STUCK = "worker_stuck"
    """工作线程未在时限内退出。"""
    STORAGE_FAILED = "storage_failed"
    """写库失败。"""
    INTERNAL_ERROR = "internal_error"
    """引擎内部错误。"""


class FaultKind(StrEnum):
    """试验转异常的原因分类，决定用哪个命令恢复。"""

    DEVICE = "device"
    VIDEO = "video"
    TEMPERATURE = "temperature"
    ANALYSIS = "analysis"
    STORAGE = "storage"
    INTERNAL = "internal"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class EngineEvent:
    """引擎事件基类，事件都在事务提交之后发出。

    Attributes:
        experiment_id: 试验标识。
        at: 发生时间。
    """

    experiment_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class StateChanged(EngineEvent):
    """试验状态变化。

    Attributes:
        previous: 原状态。
        current: 新状态。
        trigger: 触发转换的状态机事件。
        actor: 操作者，系统行为为 ``system``。
        reason: 原因说明。
    """

    previous: ExperimentState
    current: ExperimentState
    trigger: ExperimentEvent
    actor: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WellUpdated(EngineEvent):
    """孔状态变化。

    Attributes:
        row_index: 排号。
        tube_no: 管号。
        status: 孔的显示状态。
        interpretation: 自动判读，尚无结果时为 None。
    """

    row_index: int
    tube_no: int
    status: WellStatus
    interpretation: Interpretation | None = None


@dataclass(frozen=True, slots=True)
class RowCountdown(EngineEvent):
    """一排的倒计时。

    Attributes:
        row_index: 排号。
        phase: 该排的阶段。
        remaining_s: 距到时的设备时间，已到时为 0。
    """

    row_index: int
    phase: RowPhase
    remaining_s: float


@dataclass(frozen=True, slots=True)
class TemperatureChanged(EngineEvent):
    """一次温度读数。

    Attributes:
        celsius: 温度，无法读取时为 None。
        in_range: 是否在允许范围内。
    """

    celsius: float | None
    in_range: bool


@dataclass(frozen=True, slots=True)
class EngineAlert(EngineEvent):
    """告警。

    Attributes:
        level: 级别。
        code: 告警码。
        message: 说明。
        row_index: 相关的排，没有时为 None。
    """

    level: AlertLevel
    code: AlertCode
    message: str
    row_index: int | None = None


EngineEventListener = Callable[[EngineEvent], None]
"""事件监听者，在事件分发线程上调用，抛出的异常只记录日志。"""


@dataclass(frozen=True, slots=True)
class RowSnapshot:
    """一排的只读快照。

    Attributes:
        row_index: 排号。
        tubes: 本排有样品的管号。
        phase: 阶段。
        remaining_s: 距到时的设备时间。
    """

    row_index: int
    tubes: tuple[int, ...]
    phase: RowPhase
    remaining_s: float


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    """引擎运行状态的只读快照，可以在任意线程读取。

    Attributes:
        experiment_id: 试验标识。
        state: 试验状态。
        running: 引擎线程是否还在运行。
        rows: 各排的快照。
        fault_kinds: 当前的异常原因分类。
        fault_reason: 最近一次异常的说明。
        temperature_c: 最近一次温度读数。
    """

    experiment_id: str
    state: ExperimentState
    running: bool
    rows: tuple[RowSnapshot, ...]
    fault_kinds: frozenset[FaultKind] = frozenset()
    fault_reason: str | None = None
    temperature_c: float | None = None


@dataclass(frozen=True, slots=True)
class RecoveredExperiment:
    """启动恢复时被标记为异常的试验。

    Attributes:
        experiment_id: 试验标识。
        previous: 库内原状态。
        reason: 写入审计的原因。
    """

    experiment_id: str
    previous: ExperimentState
    reason: str
