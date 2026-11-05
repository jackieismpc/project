"""模拟设备：``DeviceController`` 的实现。

模拟滑台、翻转、温度、舱门、连接和故障。设备动作的耗时按设备时间配置（秒），
实际等待时间为 ``耗时 / time_scale``，默认加速 60 倍。时钟可以注入，测试用
:class:`~simulation.clock.ManualClock`，等待只推进读数，不真实 ``sleep``。

没有配置项的后端用 ``simulation.sim_device:SimDevice`` 无参构造。同一进程中最近创建的设备
可以通过 :meth:`SimDevice.latest` 取得，模拟视频源据此跟随设备的翻转状态。
"""

from __future__ import annotations

import math
import threading
import weakref
from datetime import datetime

import numpy as np

from gelinspect.domain.plate import ROW_COUNT
from gelinspect.interfaces.device import (
    DeviceConnectionError,
    DeviceFault,
    DeviceFaultError,
    DeviceStatus,
    DeviceTimeoutError,
    FlipResult,
    TrayPosition,
)
from simulation.clock import Clock, SystemClock
from simulation.scene import SceneSnapshot

BASE_TEMPERATURE_C = 37.0


class SimDevice:
    """模拟设备。

    公共方法都是线程安全的：试验引擎线程调用阻塞的动作，采集线程可以同时读取场景。
    """

    _latest: weakref.ReferenceType[SimDevice] | None = None

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        time_scale: float = 60.0,
        tray_duration_s: float = 3.0,
        flip_duration_s: float = 6.0,
        settle_duration_s: float = 20.0,
        temperature_c: float = BASE_TEMPERATURE_C,
        seed: int = 0,
    ) -> None:
        """创建设备，不做耗时操作。

        Args:
            clock: 时钟，默认系统时钟。
            time_scale: 时间加速倍数，设备时间 / 实际时间。
            tray_duration_s: 滑台进出一次的设备时间，秒。
            flip_duration_s: 翻转或复位一次的设备时间，秒。
            settle_duration_s: 翻转后样品流动到稳定的设备时间，秒。
            temperature_c: 孵育温度的中心值，℃。
            seed: 温度微扰的随机种子。

        Raises:
            ValueError: ``time_scale`` 不是正数，或耗时为负。
        """
        if time_scale <= 0:
            raise ValueError("time_scale 必须为正")
        if min(tray_duration_s, flip_duration_s, settle_duration_s) < 0:
            raise ValueError("耗时不能为负")
        self.clock: Clock = clock or SystemClock()
        self.time_scale = time_scale
        self.tray_duration_s = tray_duration_s
        self.flip_duration_s = flip_duration_s
        self.settle_duration_s = settle_duration_s
        self.temperature_c = temperature_c
        self._seed = seed
        self._lock = threading.RLock()
        self._connected = False
        self._refuse_connection = False
        self._tray = TrayPosition.OUT
        self._door_closed = True
        self._faults: dict[str, str] = {}
        self._timeout_next = False
        self._temp_offset = 0.0
        self._flipped: set[int] = set()
        self._active_row = 1
        self._motion: tuple[int, bool, float] | None = None  # 排号、是否翻转、开始的单调时间
        self._last_done: tuple[int, float] | None = None  # 排号、完成的单调时间
        self._reads = 0
        self._start = self.clock.monotonic()
        SimDevice._latest = weakref.ref(self)

    @classmethod
    def latest(cls) -> SimDevice | None:
        """本进程中最近创建且仍存活的设备。

        Returns:
            设备，没有时为 None。
        """
        return cls._latest() if cls._latest is not None else None

    # ----- 故障和状态注入（非协议方法，供测试和演示使用） -----

    def inject_fault(self, code: str, message: str = "") -> None:
        """注入一条设备故障，直到 :meth:`clear_faults`；故障期间动作被拒绝。

        Args:
            code: 故障码。
            message: 故障描述。
        """
        with self._lock:
            self._faults[code] = message

    def clear_faults(self) -> None:
        """清除全部注入：故障、温度越限、超时、拒绝连接。"""
        with self._lock:
            self._faults.clear()
            self._temp_offset = 0.0
            self._timeout_next = False
            self._refuse_connection = False

    def inject_timeout(self) -> None:
        """让下一次动作抛 ``DeviceTimeoutError``。"""
        with self._lock:
            self._timeout_next = True

    def inject_disconnect(self) -> None:
        """模拟通信中断，需要重新 :meth:`connect` 才能恢复。"""
        with self._lock:
            self._connected = False

    def refuse_connection(self, refuse: bool = True) -> None:
        """让 :meth:`connect` 失败。

        Args:
            refuse: 是否拒绝连接。
        """
        with self._lock:
            self._refuse_connection = refuse

    def inject_temperature_excursion(self, delta_c: float) -> None:
        """让温度偏离中心值，例如 ``+2.0`` 表示超限。

        Args:
            delta_c: 偏移量，℃。
        """
        with self._lock:
            self._temp_offset = delta_c

    def set_door(self, closed: bool) -> None:
        """设置舱门状态。

        Args:
            closed: True 为关闭。
        """
        with self._lock:
            self._door_closed = closed

    def select_row(self, row_index: int) -> None:
        """设置相机对着的排，不触发动作。

        Args:
            row_index: 排号，1–10。

        Raises:
            ValueError: 排号超出范围。
        """
        self._check_row(row_index)
        with self._lock:
            self._active_row = row_index

    def is_flipped(self, row_index: int) -> bool:
        """某排当前是否处于翻转状态。

        Args:
            row_index: 排号，1–10。

        Returns:
            已翻转（含正在完成的翻转）时为 True。
        """
        with self._lock:
            self._commit_motion()
            return row_index in self._flipped

    # ----- DeviceController -----

    def connect(self) -> None:
        """建立连接，可重复调用。

        Raises:
            DeviceConnectionError: 注入了拒绝连接。
        """
        with self._lock:
            if self._refuse_connection:
                raise DeviceConnectionError("模拟设备拒绝连接")
            self._connected = True

    def move_tray(self, position: TrayPosition) -> None:
        """移动滑台，到位后返回。

        Args:
            position: 目标位置。

        Raises:
            ValueError: 目标位置是 ``UNKNOWN``。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 注入了超时。
            DeviceFaultError: 存在故障，或舱门未关。
        """
        if position is TrayPosition.UNKNOWN:
            raise ValueError("目标位置不能是 UNKNOWN")
        with self._lock:
            self._require_ready(need_door=position is TrayPosition.IN)
            changed = position is not self._tray
        if changed:
            self._wait(self.tray_duration_s)
        with self._lock:
            self._tray = position

    def flip_row(self, row_index: int) -> FlipResult:
        """翻转一排并保持翻转状态。

        Args:
            row_index: 排号，1–10。

        Returns:
            翻转结果，角度为 180° 附近。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 注入了超时。
            DeviceFaultError: 存在故障、舱门未关或滑台不在工作位。
        """
        return self._move_row(row_index, flip=True)

    def restore_row(self, row_index: int) -> FlipResult:
        """把一排复位到翻转前的姿态。

        Args:
            row_index: 排号，1–10。

        Returns:
            复位结果，角度为 0° 附近。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 注入了超时。
            DeviceFaultError: 存在故障、舱门未关或滑台不在工作位。
        """
        return self._move_row(row_index, flip=False)

    def read_status(self) -> DeviceStatus:
        """读取状态，通信断开时返回 ``connected=False``。

        Returns:
            状态快照。
        """
        with self._lock:
            now = self.clock.now()
            if not self._connected:
                return DeviceStatus(connected=False, tray_position=self._tray, read_at=now)
            self._commit_motion()
            return DeviceStatus(
                connected=True,
                temperature_c=self._temperature(),
                door_closed=self._door_closed,
                tray_position=self._tray,
                faults=[DeviceFault(code=c, message=m) for c, m in self._faults.items()],
                read_at=now,
            )

    def close(self) -> None:
        """断开连接，可重复调用。"""
        with self._lock:
            self._connected = False

    # ----- 场景（供模拟视频源读取） -----

    def scene_snapshot(self) -> SceneSnapshot:
        """相机当前看到的场景。

        Returns:
            当前排、翻转进度和流动进度。
        """
        with self._lock:
            # 持锁读场景和时间：与翻转的开始、结束时间同一把锁，帧时间和画面不会错位。
            self._commit_motion()
            now = self.clock.monotonic()
            at = self.clock.now()
            if self._motion is not None:
                row, flip, started = self._motion
                progress = self._ratio((now - started) * self.time_scale, self.flip_duration_s)
                return SceneSnapshot(row, progress if flip else 1 - progress, 0.0, at)
            row = self._active_row
            if row not in self._flipped:
                return SceneSnapshot(row, 0.0, 0.0, at)
            settle = 1.0
            if self._last_done is not None and self._last_done[0] == row:
                elapsed = (now - self._last_done[1]) * self.time_scale
                settle = self._ratio(elapsed, self.settle_duration_s)
            return SceneSnapshot(row, 1.0, settle, at)

    # ----- 内部 -----

    @staticmethod
    def _check_row(row_index: int) -> None:
        if not 1 <= row_index <= ROW_COUNT:
            raise ValueError(f"排号应在 1–{ROW_COUNT} 内：{row_index}")

    @staticmethod
    def _ratio(elapsed: float, duration: float) -> float:
        return 1.0 if duration <= 0 else min(max(elapsed / duration, 0.0), 1.0)

    def _require_ready(self, *, need_door: bool) -> None:
        """检查连接、故障、超时和舱门，调用时须持锁。"""
        if not self._connected:
            raise DeviceConnectionError("设备未连接")
        if self._faults:
            code, message = next(iter(self._faults.items()))
            raise DeviceFaultError(f"设备故障 {code}：{message}")
        if self._timeout_next:
            self._timeout_next = False
            raise DeviceTimeoutError("模拟的动作超时")
        if need_door and not self._door_closed:
            raise DeviceFaultError("舱门未关")

    def _wait(self, device_seconds: float) -> None:
        """按加速倍数等待设备时间。"""
        self.clock.sleep(device_seconds / self.time_scale)

    def _commit_motion(self) -> None:
        """动作已经走完时落定状态，调用时须持锁。"""
        if self._motion is None:
            return
        row, flip, started = self._motion
        done_at = started + self.flip_duration_s / self.time_scale
        if self.clock.monotonic() >= done_at:
            (self._flipped.add if flip else self._flipped.discard)(row)
            self._last_done = (row, done_at)
            self._motion = None

    def begin_flip(self, row_index: int, *, flip: bool = True) -> None:
        """开始翻转或复位但不等待，动作按时钟自行完成，供演示和测试观察过渡画面。

        Args:
            row_index: 排号，1–10。
            flip: True 为翻转，False 为复位。

        Raises:
            ValueError: 排号超出范围。
            DeviceConnectionError: 通信断开。
            DeviceTimeoutError: 注入了超时。
            DeviceFaultError: 存在故障、舱门未关或滑台不在工作位。
        """
        self._begin_motion(row_index, flip=flip)

    def _begin_motion(self, row_index: int, *, flip: bool) -> datetime:
        self._check_row(row_index)
        with self._lock:
            self._require_ready(need_door=True)
            if self._tray is not TrayPosition.IN:
                raise DeviceFaultError("滑台不在工作位，不能翻转")
            self._commit_motion()
            started_at = self.clock.now()
            self._active_row = row_index
            self._motion = (row_index, flip, self.clock.monotonic())
            return started_at

    def _move_row(self, row_index: int, *, flip: bool) -> FlipResult:
        started_at = self._begin_motion(row_index, flip=flip)
        self._wait(self.flip_duration_s)
        with self._lock:
            self._commit_motion()
            completed_at = self.clock.now()
            jitter = float(np.random.default_rng([self._seed, row_index, int(flip)]).normal(0, 0.3))
        return FlipResult(
            row_index=row_index,
            actual_angle_deg=(180.0 if flip else 0.0) + jitter,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _temperature(self) -> float:
        """中心值加缓慢波动和微小噪声，约 ±0.2 ℃，调用时须持锁。"""
        virtual = (self.clock.monotonic() - self._start) * self.time_scale
        self._reads += 1
        noise = float(np.random.default_rng([self._seed, self._reads]).normal(0, 0.03))
        wobble = 0.15 * math.sin(2 * math.pi * virtual / 600.0)
        return self.temperature_c + wobble + noise + self._temp_offset
