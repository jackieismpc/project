"""桥接器：事件来自工作线程，槽在主线程。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.services.engine_types import (
    AlertCode,
    AlertLevel,
    EngineAlert,
    EngineEvent,
    EngineEventListener,
    StateChanged,
    TemperatureChanged,
)
from gelinspect.services.instrument_monitor import InstrumentStatus
from gelinspect.ui.bridge import EngineBridge
from gelinspect.ui.main_window import MainWindow
from tests.services.engine_helpers import ready_experiment
from tests.ui.conftest import sign_in, wait_for

NOW = datetime(2026, 11, 5, tzinfo=UTC)


def state_event(experiment_id: str = "E1") -> StateChanged:
    return StateChanged(
        experiment_id=experiment_id,
        at=NOW,
        previous=ExperimentState.READY,
        current=ExperimentState.INCUBATING,
        trigger=ExperimentEvent.START,
        actor="u",
    )


class FakeSource:
    def __init__(self) -> None:
        self.listeners: list[EngineEventListener] = []

    def add_listener(self, listener: EngineEventListener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener: EngineEventListener) -> None:
        self.listeners.remove(listener)


def test_events_from_a_worker_thread_are_delivered_on_the_main_thread(
    qapp: QApplication,
) -> None:
    bridge = EngineBridge()
    delivered: list[tuple[int, EngineEvent]] = []
    bridge.stateChanged.connect(lambda e: delivered.append((threading.get_ident(), e)))
    bridge.eventReceived.connect(lambda e: delivered.append((threading.get_ident(), e)))
    worker_ids: list[int] = []

    def work() -> None:
        worker_ids.append(threading.get_ident())
        for i in range(20):
            bridge.handle_event(state_event(f"E{i}"))

    thread = threading.Thread(target=work, name="engine-events")
    thread.start()
    thread.join()
    # 工作线程只是把事件排进主线程的队列，槽此刻还没有执行
    assert delivered == []
    assert wait_for(qapp, lambda: len(delivered) == 40)
    main = threading.get_ident()
    assert worker_ids[0] != main
    assert {tid for tid, _ in delivered} == {main}
    ids = [e.experiment_id for _, e in delivered if isinstance(e, StateChanged)][:40:2]
    assert ids == [f"E{i}" for i in range(20)]  # 顺序保持
    bridge.close()


def test_typed_signals_and_instrument_status(qapp: QApplication) -> None:
    bridge = EngineBridge()
    got: dict[str, list[object]] = {"alert": [], "temp": [], "instrument": []}
    bridge.alertRaised.connect(got["alert"].append)
    bridge.temperatureChanged.connect(got["temp"].append)
    bridge.instrumentChanged.connect(got["instrument"].append)
    done = threading.Event()

    def work() -> None:
        bridge.handle_event(EngineAlert("E1", NOW, AlertLevel.WARNING, AlertCode.DOOR_OPEN, "开"))
        bridge.handle_event(TemperatureChanged("E1", NOW, 37.0, True))
        bridge.handle_instrument(InstrumentStatus(True, 36.9, NOW))
        done.set()

    threading.Thread(target=work, name="instrument-monitor").start()
    assert done.wait(5.0)
    assert wait_for(qapp, lambda: all(got.values()))
    assert len(got["alert"]) == len(got["temp"]) == len(got["instrument"]) == 1
    bridge.close()


def test_close_unsubscribes_and_drops_late_events(qapp: QApplication) -> None:
    source = FakeSource()
    bridge = EngineBridge(source)
    assert len(source.listeners) == 1
    seen: list[object] = []
    bridge.eventReceived.connect(seen.append)
    thread = threading.Thread(target=bridge.handle_event, args=(state_event(),))
    thread.start()
    thread.join()  # 事件已排队，主线程还没处理
    bridge.close()
    bridge.close()
    assert source.listeners == []
    qapp.processEvents()
    assert seen == []  # 已排队但桥已关闭：丢弃，不再触碰界面
    bridge.handle_event(state_event())
    qapp.processEvents()
    assert seen == []


def test_real_engine_events_reach_the_window_thread(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    states: list[tuple[int, ExperimentState]] = []
    window.bridge.stateChanged.connect(lambda e: states.append((threading.get_ident(), e.current)))
    session = window.session
    assert session is not None
    app_context.database.experiments.save(ready_experiment(range(1, 9), "EXP-BRIDGE"))
    app_context.engine.start("EXP-BRIDGE", session.user)
    assert wait_for(qapp, lambda: ExperimentState.INCUBATING in [s for _, s in states], 30.0)
    assert {tid for tid, _ in states} == {threading.get_ident()}
    app_context.engine.stop(session.user)
    assert wait_for(qapp, lambda: ExperimentState.STOPPED in [s for _, s in states], 30.0)
    assert app_context.engine.wait(30.0)
