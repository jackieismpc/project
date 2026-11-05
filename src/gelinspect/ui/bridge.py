"""把工作线程里的引擎事件转到 Qt 主线程。

试验引擎的事件在 ``engine-events`` 线程上回调，仪器状态在 ``instrument-monitor`` 线程上回调，
界面对象只能在主线程使用。:class:`EngineBridge` 的回调可以在任意线程调用，
它发出一个内部信号，接收槽属于主线程，所以 Qt 用排队连接投递，
随后在主线程上把事件按类型分发成带类型的信号。界面只连接这些信号，不直接碰引擎线程。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, Signal, Slot

from gelinspect.services.engine_types import (
    EngineAlert,
    EngineEvent,
    EngineEventListener,
    RowCountdown,
    StateChanged,
    TemperatureChanged,
    WellUpdated,
)
from gelinspect.services.instrument_monitor import InstrumentStatus


class EngineEventSource(Protocol):
    """能订阅引擎事件的对象（``ExperimentEngine``）。"""

    def add_listener(self, listener: EngineEventListener) -> None:
        """添加监听者。"""
        ...

    def remove_listener(self, listener: EngineEventListener) -> None:
        """移除监听者。"""
        ...


class InstrumentSource(Protocol):
    """能订阅仪器状态的对象（``InstrumentMonitor``）。"""

    def add_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """添加监听者。"""
        ...

    def remove_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """移除监听者。"""
        ...


class EngineBridge(QObject):
    """引擎事件和仪器状态的 Qt 信号桥。必须在主线程创建。

    Signals:
        eventReceived: 任何引擎事件，参数为 :class:`EngineEvent`。
        stateChanged: 试验状态变化（:class:`StateChanged`）。
        alertRaised: 告警（:class:`EngineAlert`）。
        temperatureChanged: 温度读数（:class:`TemperatureChanged`）。
        wellUpdated: 孔状态变化（:class:`WellUpdated`）。
        rowCountdown: 一排倒计时（:class:`RowCountdown`）。
        instrumentChanged: 仪器状态（:class:`InstrumentStatus`）。
    """

    eventReceived = Signal(object)  # noqa: N815
    stateChanged = Signal(object)  # noqa: N815
    alertRaised = Signal(object)  # noqa: N815
    temperatureChanged = Signal(object)  # noqa: N815
    wellUpdated = Signal(object)  # noqa: N815
    rowCountdown = Signal(object)  # noqa: N815
    instrumentChanged = Signal(object)  # noqa: N815

    _incoming = Signal(object)
    _incoming_instrument = Signal(object)

    def __init__(
        self,
        engine: EngineEventSource | None = None,
        instrument: InstrumentSource | None = None,
        parent: QObject | None = None,
    ) -> None:
        """创建桥并订阅事件源。

        Args:
            engine: 引擎，``None`` 时只能手动调用 :meth:`handle_event`。
            instrument: 仪器状态监视器。
            parent: 父对象。
        """
        super().__init__(parent)
        self.main_thread_id = threading.get_ident()
        self._engine = engine
        self._instrument = instrument
        self._closed = False
        # 接收槽是本对象（主线程）的方法：从其他线程发出时 Qt 自动用排队连接。
        self._incoming.connect(self._dispatch)
        self._incoming_instrument.connect(self._dispatch_instrument)
        if engine is not None:
            engine.add_listener(self.handle_event)
        if instrument is not None:
            instrument.add_listener(self.handle_instrument)

    def handle_event(self, event: EngineEvent) -> None:
        """引擎事件回调，可在任意线程调用。

        Args:
            event: 引擎事件。
        """
        if not self._closed:
            self._incoming.emit(event)

    def handle_instrument(self, status: InstrumentStatus) -> None:
        """仪器状态回调，可在任意线程调用。

        Args:
            status: 仪器状态。
        """
        if not self._closed:
            self._incoming_instrument.emit(status)

    def close(self) -> None:
        """取消订阅并丢弃之后到达的事件，可重复调用。"""
        if self._closed:
            return
        self._closed = True
        if self._engine is not None:
            self._engine.remove_listener(self.handle_event)
        if self._instrument is not None:
            self._instrument.remove_listener(self.handle_instrument)

    @Slot(object)
    def _dispatch(self, event: object) -> None:
        """在主线程上按类型分发事件。"""
        if self._closed or not isinstance(event, EngineEvent):
            return
        self.eventReceived.emit(event)
        if isinstance(event, StateChanged):
            self.stateChanged.emit(event)
        elif isinstance(event, EngineAlert):
            self.alertRaised.emit(event)
        elif isinstance(event, TemperatureChanged):
            self.temperatureChanged.emit(event)
        elif isinstance(event, WellUpdated):
            self.wellUpdated.emit(event)
        elif isinstance(event, RowCountdown):
            self.rowCountdown.emit(event)

    @Slot(object)
    def _dispatch_instrument(self, status: object) -> None:
        """在主线程上转发仪器状态。"""
        if not self._closed and isinstance(status, InstrumentStatus):
            self.instrumentChanged.emit(status)
