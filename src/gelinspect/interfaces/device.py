"""设备控制接口。

只定义软件和设备的交互边界，不涉及硬件设计和通信协议。全部方法都是同步阻塞的，
由试验引擎的工作线程调用，不得在 Qt 主线程调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, Field

from gelinspect.domain.enums import TubeRole
from gelinspect.domain.plate import ROW_COUNT
from gelinspect.interfaces._base import ContractModel


class DeviceError(Exception):
    """设备异常基类。"""


class DeviceConnectionError(DeviceError):
    """无法连接设备，或通信已断开。"""


class DeviceTimeoutError(DeviceError):
    """动作在规定时间内没有完成。"""


class DeviceFaultError(DeviceError):
    """设备报告故障，或拒绝执行动作，例如舱门未关。"""


class TrayPosition(StrEnum):
    """滑台位置。"""

    IN = "in"
    """滑台进入设备，工作位。"""
    OUT = "out"
    """滑台移出设备，放取试管位。"""
    UNKNOWN = "unknown"
    """位置未知，只出现在状态中，不能作为移动目标。"""


class FlipResult(ContractModel):
    """一次翻转或复位的结果。

    Attributes:
        row_index: 排号，1–10。
        actual_angle_deg: 编码器读到的实际角度，没有编码器时为 None。
        started_at: 动作开始时间，必须带时区。
        completed_at: 动作完成且到位静止的时间，必须带时区，用于定位翻转前后的视频帧。
    """

    row_index: int = Field(ge=1, le=ROW_COUNT)
    actual_angle_deg: float | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime


class DeviceFault(ContractModel):
    """设备上报的一条故障。

    Attributes:
        code: 故障码。
        message: 故障描述。
    """

    code: str = Field(min_length=1)
    message: str = ""


class DeviceStatus(ContractModel):
    """设备状态快照。

    Attributes:
        connected: 通信是否正常。
        temperature_c: 孵育温度，单位 ℃，无法读取时为 None。
        door_closed: 舱门是否关闭，无法读取时为 None。
        tray_position: 滑台位置。
        faults: 当前存在的故障。
        read_at: 读取时间，必须带时区。
    """

    connected: bool
    temperature_c: float | None = None
    door_closed: bool | None = None
    tray_position: TrayPosition = TrayPosition.UNKNOWN
    faults: list[DeviceFault] = Field(default_factory=list)
    read_at: AwareDatetime


@runtime_checkable
class DeviceController(Protocol):
    """设备控制器。"""

    def connect(self) -> None:
        """建立与设备的连接，可重复调用。

        Raises:
            DeviceConnectionError: 无法连接。
        """
        ...

    def move_tray(self, position: TrayPosition) -> None:
        """移动滑台，到位后返回。

        Args:
            position: 目标位置，不能是 ``UNKNOWN``。

        Raises:
            ValueError: 目标位置是 ``UNKNOWN``。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 未在规定时间内到位。
            DeviceFaultError: 设备故障或拒绝执行。
        """
        ...

    def select_row(self, row_index: int) -> None:
        """让相机对准一排，到位后返回。翻转前的帧必须拍到这一排，所以引擎在抓帧前调用。

        相机固定、一次能看到整板的设备可以实现为空操作。

        Args:
            row_index: 排号，1–10。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 未在规定时间内到位。
            DeviceFaultError: 设备故障或拒绝执行。
        """
        ...

    def flip_row(self, row_index: int) -> FlipResult:
        """翻转一排并保持翻转状态，到位静止后返回，软件随后抓取翻转后的帧。

        Args:
            row_index: 排号，1–10。

        Returns:
            翻转结果，含实际角度。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 未在规定时间内到位。
            DeviceFaultError: 设备故障或拒绝执行。
        """
        ...

    def restore_row(self, row_index: int) -> FlipResult:
        """把一排复位到翻转前的姿态，到位静止后返回。

        必须幂等：翻转失败时引擎不知道动作走到哪一步，会尝试复位，已在原位时应直接成功。

        Args:
            row_index: 排号，1–10。

        Returns:
            复位结果。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 未在规定时间内到位。
            DeviceFaultError: 设备故障或拒绝执行。
        """
        ...

    def read_status(self) -> DeviceStatus:
        """读取设备状态。通信断开时不抛异常，返回 ``connected=False``。

        Returns:
            状态快照。
        """
        ...

    def close(self) -> None:
        """断开连接并释放资源，可重复调用。"""
        ...


@dataclass(frozen=True, slots=True)
class PlateTube:
    """孔板上的一支试管。

    Attributes:
        tube_no: 管号。
        role: 用途。
        spiked_endotoxin: 外加内毒素，EU/mL，未知时为 None。
    """

    tube_no: int
    role: TubeRole
    spiked_endotoxin: float | None = None


@dataclass(frozen=True, slots=True)
class PlatePlan:
    """一次试验的孔板内容。

    Attributes:
        experiment_id: 试验标识。
        labeled_sensitivity: 鲎试剂标示灵敏度 λ，未知时为 None。
        tubes: 有样品的试管。
    """

    experiment_id: str
    labeled_sensitivity: float | None
    tubes: tuple[PlateTube, ...]


@runtime_checkable
class PlateAwareDevice(Protocol):
    """可选能力：启动试验前想知道孔板内容的设备。

    不是 :class:`DeviceController` 的一部分，真实设备不需要实现。模拟设备用它按剂量关系生成画面。
    """

    def load_plate_plan(self, plan: PlatePlan) -> None:
        """接收孔板内容。

        Args:
            plan: 孔板内容。
        """
        ...
