"""SimDevice：状态、翻转、温度、故障注入和时间加速。"""

import pytest

from gelinspect.interfaces.device import (
    DeviceConnectionError,
    DeviceController,
    DeviceFaultError,
    DeviceTimeoutError,
    TrayPosition,
)
from simulation.clock import ManualClock
from simulation.sim_device import SimDevice


def ready(
    clock: ManualClock | None = None,
    *,
    tray_duration_s: float = 3.0,
    flip_duration_s: float = 6.0,
    settle_duration_s: float = 20.0,
    time_scale: float = 60.0,
) -> tuple[SimDevice, ManualClock]:
    manual = clock or ManualClock()
    device = SimDevice(
        clock=manual,
        tray_duration_s=tray_duration_s,
        flip_duration_s=flip_duration_s,
        settle_duration_s=settle_duration_s,
        time_scale=time_scale,
    )
    device.connect()
    device.move_tray(TrayPosition.IN)
    return device, manual


def test_is_protocol_and_constructs_without_arguments() -> None:
    assert isinstance(SimDevice(), DeviceController)


def test_status_when_disconnected_and_connected() -> None:
    clock = ManualClock()
    device = SimDevice(clock=clock)
    status = device.read_status()
    assert status.connected is False and status.temperature_c is None
    device.connect()
    device.connect()
    status = device.read_status()
    assert status.connected and status.door_closed and status.faults == []
    assert status.tray_position is TrayPosition.OUT
    device.close()
    device.close()
    assert device.read_status().connected is False


def test_tray_moves_and_takes_accelerated_time() -> None:
    device, clock = ready(tray_duration_s=6.0, time_scale=60.0)
    assert device.read_status().tray_position is TrayPosition.IN
    assert clock.monotonic() == pytest.approx(0.1)  # 6 秒设备时间 / 60
    device.move_tray(TrayPosition.IN)  # 已在位，不再耗时
    assert clock.monotonic() == pytest.approx(0.1)
    with pytest.raises(ValueError, match="UNKNOWN"):
        device.move_tray(TrayPosition.UNKNOWN)


def test_flip_and_restore_report_angle_and_times() -> None:
    device, clock = ready(flip_duration_s=6.0)
    before = clock.monotonic()
    result = device.flip_row(3)
    assert result.row_index == 3
    assert result.actual_angle_deg is not None and abs(result.actual_angle_deg - 180) < 3
    assert (result.completed_at - result.started_at).total_seconds() == pytest.approx(0.1)
    assert clock.monotonic() - before == pytest.approx(0.1)
    assert device.is_flipped(3)
    restored = device.restore_row(3)
    assert restored.actual_angle_deg is not None and abs(restored.actual_angle_deg) < 3
    assert not device.is_flipped(3)


def test_invalid_row_and_preconditions() -> None:
    device = SimDevice(clock=ManualClock())
    with pytest.raises(DeviceConnectionError):
        device.move_tray(TrayPosition.IN)
    device.connect()
    with pytest.raises(DeviceFaultError, match="滑台"):
        device.flip_row(1)
    device.move_tray(TrayPosition.IN)
    with pytest.raises(ValueError, match="排号"):
        device.flip_row(0)
    with pytest.raises(ValueError, match="排号"):
        device.restore_row(11)
    device.set_door(False)
    assert device.read_status().door_closed is False
    with pytest.raises(DeviceFaultError, match="舱门"):
        device.flip_row(1)
    device.set_door(True)
    device.flip_row(1)


def test_temperature_is_near_37_and_excursion_can_be_injected() -> None:
    device, clock = ready()
    readings = []
    for _ in range(50):
        clock.advance(7)
        status = device.read_status()
        assert status.temperature_c is not None
        readings.append(status.temperature_c)
    assert all(abs(t - 37.0) < 0.3 for t in readings)
    assert max(readings) - min(readings) > 0.05
    device.inject_temperature_excursion(2.5)
    hot = device.read_status().temperature_c
    assert hot is not None and hot > 39
    device.clear_faults()
    cooled = device.read_status().temperature_c
    assert cooled is not None and abs(cooled - 37.0) < 0.3


def test_temperature_is_reproducible_with_seed() -> None:
    first, _ = ready()
    second, _ = ready()
    assert first.read_status().temperature_c == second.read_status().temperature_c


def test_fault_injection() -> None:
    device, _ = ready()
    device.inject_fault("E01", "电机过载")
    status = device.read_status()
    assert [f.code for f in status.faults] == ["E01"]
    with pytest.raises(DeviceFaultError, match="E01"):
        device.flip_row(1)
    with pytest.raises(DeviceFaultError):
        device.move_tray(TrayPosition.OUT)
    device.clear_faults()
    device.flip_row(1)


def test_timeout_and_disconnect_injection() -> None:
    device, _ = ready()
    device.inject_timeout()
    with pytest.raises(DeviceTimeoutError):
        device.flip_row(1)
    device.flip_row(1)  # 超时只影响一次
    device.inject_disconnect()
    assert device.read_status().connected is False
    with pytest.raises(DeviceConnectionError):
        device.flip_row(2)
    device.connect()
    device.flip_row(2)
    device.refuse_connection()
    with pytest.raises(DeviceConnectionError):
        device.connect()


def test_scene_follows_flip_progress_and_settling() -> None:
    clock = ManualClock()
    device, _ = ready(clock, flip_duration_s=6.0, settle_duration_s=20.0, time_scale=1.0)
    clock.advance(-0.0)
    assert device.scene_snapshot().flip_phase == 0.0
    device.select_row(2)
    device.flip_row(2)
    done = device.scene_snapshot()
    assert (done.row_index, done.flip_phase, done.settle) == (2, 1.0, 0.0)
    clock.advance(10)
    assert device.scene_snapshot().settle == pytest.approx(0.5)
    clock.advance(30)
    assert device.scene_snapshot().settle == 1.0


def test_scene_reports_intermediate_phase_during_flip() -> None:
    clock = ManualClock()
    device, _ = ready(clock, flip_duration_s=10.0, time_scale=1.0)
    device.begin_flip(4)
    clock.advance(2.5)
    snapshot = device.scene_snapshot()
    assert snapshot.row_index == 4 and snapshot.flip_phase == pytest.approx(0.25)
    clock.advance(10)
    assert device.scene_snapshot().flip_phase == 1.0
    assert device.is_flipped(4)


def test_latest_returns_most_recent_device() -> None:
    device = SimDevice(clock=ManualClock())
    assert SimDevice.latest() is device


def test_invalid_construction() -> None:
    with pytest.raises(ValueError):
        SimDevice(time_scale=0)
    with pytest.raises(ValueError):
        SimDevice(flip_duration_s=-1)
