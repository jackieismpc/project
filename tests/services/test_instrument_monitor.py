"""仪器状态监视和引擎的设备探测。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from gelinspect.interfaces.device import DeviceStatus
from gelinspect.services.instrument_monitor import THREAD_NAME, InstrumentMonitor, InstrumentStatus
from tests.services.engine_helpers import (
    OPERATOR,
    Harness,
    eventually,
    fast_config,
    make_harness,
)


class FakeProbe:
    def __init__(self, *statuses: DeviceStatus | None) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def probe_device(self) -> DeviceStatus | None:
        self.calls += 1
        return self.statuses.pop(0) if self.statuses else None


def status(connected: bool, temperature: float | None = 37.0) -> DeviceStatus:
    return DeviceStatus(connected=connected, temperature_c=temperature, read_at=datetime.now(UTC))


def test_poll_once_notifies_listeners_and_remembers_the_last_status() -> None:
    probe = FakeProbe(status(True, 36.9), None)
    monitor = InstrumentMonitor(probe)
    seen: list[InstrumentStatus] = []
    monitor.add_listener(seen.append)
    first = monitor.poll_once()
    assert first is not None and first.connected and first.temperature_c == 36.9
    assert monitor.poll_once() is None  # 试验运行中：探测返回 None，不回调
    assert seen == [first] and monitor.last == first


def test_a_failing_listener_does_not_stop_the_others() -> None:
    monitor = InstrumentMonitor(FakeProbe(status(True)))
    seen: list[InstrumentStatus] = []

    def boom(_: InstrumentStatus) -> None:
        raise RuntimeError("监听者出错")

    monitor.add_listener(boom)
    monitor.add_listener(seen.append)
    monitor.poll_once()
    assert len(seen) == 1


def test_background_thread_polls_and_stops_cleanly() -> None:
    probe = FakeProbe(*[status(True) for _ in range(50)])
    monitor = InstrumentMonitor(probe, interval_s=0.01)
    monitor.start()
    monitor.start()  # 重复启动无操作
    assert eventually(lambda: probe.calls >= 3)
    assert [t.name for t in threading.enumerate()].count(THREAD_NAME) == 1
    assert monitor.stop(2.0)
    assert THREAD_NAME not in [t.name for t in threading.enumerate()]
    assert monitor.stop(2.0)


def test_engine_probe_reads_the_device_when_idle(tmp_path: Path) -> None:
    h = make_harness(tmp_path, config=fast_config())
    try:
        probed = h.engine.probe_device()
        assert probed is not None and probed.connected and probed.temperature_c is not None
    finally:
        h.close()


def test_engine_probe_is_skipped_while_a_run_is_alive(tmp_path: Path) -> None:
    h: Harness = make_harness(tmp_path, config=fast_config(time_scale=1.0))
    try:
        h.save_and_start()
        h.incubating()
        assert h.engine.probe_device() is None
        h.engine.stop(OPERATOR)
        h.finished()
        assert h.engine.probe_device() is not None
    finally:
        h.close()
