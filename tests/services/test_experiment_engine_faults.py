"""试验引擎的故障路径：算法、视频、设备、温度、回调、存储和启动恢复。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentEvent
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.infrastructure.db import Database, open_database, verify_audit_chain
from gelinspect.interfaces.analysis import AnalyzerError
from gelinspect.interfaces.device import DeviceFaultError, DeviceTimeoutError
from gelinspect.interfaces.storage import MediaKind
from gelinspect.services.engine_types import (
    AlertCode,
    EngineAlert,
    EngineBusyError,
    EngineEvent,
    EnginePermissionError,
    EngineStateError,
    FaultKind,
    RowPhase,
    StateChanged,
)
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.video import ReconnectConfig
from simulation.clock import ManualClock
from tests.services.engine_helpers import (
    ANALYST,
    EXP_ID,
    OPERATOR,
    FlakyStorage,
    Gate,
    Harness,
    eventually,
    fast_config,
    make_harness,
    ready_experiment,
    tubes_of_rows,
)

HarnessFactory = Callable[..., Harness]
NO_RECONNECT = ReconnectConfig(enabled=False, stall_disconnect_s=None)


@pytest.fixture
def harness(tmp_path: Path, thread_baseline: None) -> Iterator[HarnessFactory]:
    built: list[Harness] = []

    def build(*args: object, **kwargs: object) -> Harness:
        item = make_harness(tmp_path, *args, **kwargs)  # type: ignore[arg-type]
        built.append(item)
        return item

    yield build
    for item in built:
        item.close()


def fault_kinds(h: Harness) -> frozenset[FaultKind]:
    snapshot = h.engine.snapshot()
    assert snapshot is not None
    return snapshot.fault_kinds


# ---------------------------------------------------------------------- 算法


def test_analyzer_error_keeps_other_rows_on_time_then_retry_succeeds(
    harness: HarnessFactory,
) -> None:
    """第 1 排算法整体故障：第 2 排照常读数；全部读完后转异常；重试不再翻转。"""
    h = harness(tubes_of_rows(1, 2))
    h.analyzer.errors[1] = [AnalyzerError("模型未加载")]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)

    stored = h.stored()
    assert stored.state is S.FAULTED
    assert set(stored.results) == set(range(9, 17))  # 第 2 排的结果已入库
    assert h.device.flipped_rows() == [1, 2]
    assert fault_kinds(h) == {FaultKind.ANALYSIS}
    assert h.log.alerts(AlertCode.ANALYSIS_FAILED)
    assert "Row01" in (h.snapshot().fault_reason or "")
    h.assert_clean()

    assert h.engine.retry_analysis(OPERATOR)
    h.finished()
    stored = h.stored()
    assert stored.state is S.COMPLETED
    assert set(stored.results) == set(range(1, 17))
    assert h.device.flipped_rows() == [1, 2]  # 没有再次翻转
    assert [r.row_index for r in h.analyzer.requests] == [1, 2, 1]
    # 重试用的是磁盘上的原图，路径和第一次一致。
    first, retry = h.analyzer.requests[0], h.analyzer.requests[2]
    assert [f.source_path for f in first.frames_before] == [
        f.source_path for f in retry.frames_before
    ]
    assert (first.frames_after[0].image == retry.frames_after[0].image).all()
    assert h.media_counts() == (2, 12)
    assert any("第 2 次分析" in d for d in h.audit_details(AuditAction.ROW_ANALYZED))
    h.assert_clean()


def test_retry_can_fail_again_and_be_retried(harness: HarnessFactory) -> None:
    h = harness()
    h.analyzer.errors[1] = [AnalyzerError("一"), AnalyzerError("二")]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    h.engine.retry_analysis(OPERATOR)
    assert eventually(lambda: len(h.analyzer.requests) == 2, 10.0)
    assert eventually(lambda: len(h.log.alerts(AlertCode.ANALYSIS_FAILED)) == 2, 10.0)
    h.wait_state(S.FAULTED)
    h.engine.retry_analysis(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_retry_refuses_altered_original_images(harness: HarnessFactory) -> None:
    h = harness()
    h.analyzer.errors[1] = [AnalyzerError("一")]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    image = h.database.media.list_for_experiment(EXP_ID, kind=MediaKind.IMAGE)[0]
    original = Path(image.path).read_bytes()
    Path(image.path).write_bytes(original + b"tampered")

    h.engine.retry_analysis(OPERATOR)
    assert eventually(lambda: len(h.log.alerts(AlertCode.ANALYSIS_FAILED)) == 2, 10.0)
    h.wait_state(S.FAULTED)
    assert len(h.analyzer.requests) == 1  # 算法没有看到被改动的图
    assert h.stored().results == {}
    assert "SHA-256" in h.log.alerts(AlertCode.ANALYSIS_FAILED)[-1].message

    Path(image.path).write_bytes(original)
    h.engine.retry_analysis(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_unexpected_analyzer_exception_is_treated_as_row_failure(harness: HarnessFactory) -> None:
    h = harness()
    h.analyzer.errors[1] = [RuntimeError("算法内部的 bug")]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.stored().results == {}  # 绝不默认出结果
    h.engine.retry_analysis(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_retry_analysis_rejected_when_other_faults_remain(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_fault("E1", "电机过流")
    h.wait_state(S.FAULTED)
    h.engine.retry_analysis(OPERATOR)
    assert eventually(lambda: h.log.alerts(AlertCode.RESUME_REJECTED), 10.0)
    assert h.state() is S.FAULTED


def test_commands_require_permission(harness: HarnessFactory) -> None:
    h = harness()
    for command in (h.engine.resume, h.engine.retry_analysis, h.engine.stop):
        with pytest.raises(EnginePermissionError):
            command(ANALYST)


# ---------------------------------------------------------------------- 视频


def test_disconnect_while_incubating_with_reconnect_only_alerts(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.source.inject_disconnect()
    assert eventually(lambda: h.log.alerts(AlertCode.VIDEO_RECONNECTED), 10.0)
    assert h.state() is S.INCUBATING
    assert h.log.alerts(AlertCode.VIDEO_DISCONNECTED)
    h.fire_rows()
    h.finished()
    assert h.stored().state is S.COMPLETED
    details = h.audit_details(AuditAction.ENGINE_ALERT)
    assert any("video_disconnected" in d for d in details)
    assert any("video_reconnected" in d for d in details)


def test_disconnect_without_reconnect_faults_and_never_flips(harness: HarnessFactory) -> None:
    h = harness(reconnect=NO_RECONNECT)
    h.save_and_start()
    h.incubating()
    h.source.inject_disconnect()
    h.wait_state(S.FAULTED)
    h.fire_rows()
    assert fault_kinds(h) == {FaultKind.VIDEO}
    assert h.device.flipped_rows() == []
    h.assert_clean()
    # 恢复时重启视频，再读数。
    h.engine.resume(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_video_down_past_grace_period_faults(harness: HarnessFactory) -> None:
    h = harness(
        config=fast_config(video_down_grace_s=0.05),
        reconnect=ReconnectConfig(initial_delay_s=30.0, max_delay_s=30.0, stall_disconnect_s=None),
    )
    h.save_and_start()
    h.incubating()
    h.source.inject_disconnect()
    h.wait_state(S.FAULTED)
    assert fault_kinds(h) == {FaultKind.VIDEO}
    h.engine.stop(OPERATOR)
    h.finished()


def test_row_waits_while_video_is_reconnecting(harness: HarnessFactory) -> None:
    """断流期间到时的排不翻转：翻转不可逆，没有画面就不能动。"""
    h = harness(
        reconnect=ReconnectConfig(initial_delay_s=0.2, max_delay_s=0.2, stall_disconnect_s=None)
    )
    h.save_and_start()
    h.incubating()
    h.source.inject_disconnect()
    assert eventually(lambda: h.log.alerts(AlertCode.VIDEO_DISCONNECTED), 10.0)
    h.fire_rows()
    assert eventually(lambda: h.log.alerts(AlertCode.VIDEO_RECONNECTED), 10.0)
    disconnected_at = h.log.alerts(AlertCode.VIDEO_DISCONNECTED)[0].at
    reconnected_at = h.log.alerts(AlertCode.VIDEO_RECONNECTED)[0].at
    assert reconnected_at > disconnected_at
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert h.device.flipped_rows() == [1]


def test_disconnect_during_reading_faults_and_cleans_up(harness: HarnessFactory) -> None:
    """翻转中断流且不重连：该排读数失败，录制段封存入库，无 pin，已复位；恢复后重读。"""
    h = harness(reconnect=NO_RECONNECT)
    gate = Gate()
    h.device.gates["flip_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.wait_more_frames()
    h.source.inject_disconnect()
    assert eventually(lambda: not h.pipeline.recorder.active, 10.0)
    gate.release.set()
    h.wait_state(S.FAULTED)
    snapshot = h.engine.snapshot()
    assert snapshot is not None and snapshot.rows[0].phase is RowPhase.READ_FAILED
    assert FaultKind.VIDEO in snapshot.fault_kinds
    h.assert_clean()
    assert [name for name, _ in h.device.calls].count("restore_row") == 1
    video = h.database.media.list_for_experiment(EXP_ID, kind=MediaKind.VIDEO)
    assert len(video) == 1  # 中断的录制段也入库
    h.assert_media_intact()

    h.engine.resume(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert h.device.flipped_rows() == [1, 1]
    assert h.media_counts() == (2, 6)
    h.assert_media_intact()


def test_disconnect_during_reading_with_reconnect_keeps_the_capture(
    harness: HarnessFactory,
) -> None:
    """翻转中断流但很快重连：图像齐全就照常分析，录像标记不完整。"""
    h = harness()
    gate = Gate()
    h.device.gates["flip_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.wait_more_frames()
    h.source.inject_disconnect()
    # 引擎线程卡在翻转上，告警要等它回到主循环才发，所以这里看管线自己的计数。
    assert eventually(lambda: h.pipeline.stats().reconnects == 1, 10.0)
    gate.release.set()
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert h.log.alerts(AlertCode.VIDEO_RECONNECTED)
    assert any("录像不完整" in d for d in h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED))
    h.assert_clean()
    h.assert_media_intact()


def test_no_frames_before_flip_means_no_flip(harness: HarnessFactory) -> None:
    h = harness(config=fast_config(preflip_frame_timeout_s=0.02))
    h.save_and_start()
    h.incubating()
    h.source.inject_timeouts(10**6)  # 不再产帧，但不算断流
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.device.flipped_rows() == []
    assert fault_kinds(h) == {FaultKind.VIDEO}
    h.assert_clean()
    h.source.inject_timeouts(0)
    h.engine.stop(OPERATOR)
    h.finished()
    assert h.stored().state is S.STOPPED


# ---------------------------------------------------------------------- 设备


@pytest.mark.parametrize(
    "error", [DeviceFaultError("电机过流"), DeviceTimeoutError("未到位")], ids=["fault", "timeout"]
)
def test_flip_failure_faults_cleans_up_and_resume_rereads(
    harness: HarnessFactory, error: Exception
) -> None:
    h = harness()
    h.device.fail_next["flip_row"] = error
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    snapshot = h.engine.snapshot()
    assert snapshot is not None
    assert snapshot.fault_kinds == {FaultKind.DEVICE}
    assert snapshot.rows[0].phase is RowPhase.READ_FAILED
    assert "设备动作失败" in (snapshot.fault_reason or "")
    assert h.log.alerts(AlertCode.READ_FAILED)
    # 翻转失败也复位（可能走了一半），录制段结束，pin 解除。
    assert [name for name, _ in h.device.calls].count("restore_row") == 1
    h.assert_clean()
    assert h.stored().results == {}
    assert h.media_counts()[1] == 0

    h.engine.resume(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert h.device.flipped_rows() == [1, 1]
    h.assert_clean()


def test_fault_alert_is_queued_before_the_snapshot_shows_faulted(
    harness: HarnessFactory,
) -> None:
    """看到 faulted 快照的观察者，原因告警一定已经在分发队列里。"""
    h = harness()
    h.device.fail_next["flip_row"] = DeviceFaultError("电机过流")
    dispatcher = h.engine._events  # noqa: SLF001
    real_emit = dispatcher.emit
    state_at_alert: list[S] = []

    def emit(event: EngineEvent) -> None:
        if isinstance(event, EngineAlert) and event.code is AlertCode.READ_FAILED:
            state_at_alert.append(h.state())
        real_emit(event)

    dispatcher.emit = emit  # type: ignore[method-assign]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert len(state_at_alert) == 1 and state_at_alert[0] is not S.FAULTED
    h.engine.stop(OPERATOR)
    h.finished()


def test_row_deadline_is_set_before_incubating_becomes_visible(harness: HarnessFactory) -> None:
    """观察者一看到孵育开始就推进时钟：截止时间必须已经定好，否则排永远等不到时。"""
    h = harness()
    dispatcher = h.engine._events  # noqa: SLF001
    real_emit = dispatcher.emit

    def emit(event: EngineEvent) -> None:
        real_emit(event)
        if isinstance(event, StateChanged) and event.current is S.INCUBATING:
            h.fire_rows()

    dispatcher.emit = emit  # type: ignore[method-assign]
    h.save_and_start()
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_device_fault_during_incubation_blocks_resume_until_cleared(
    harness: HarnessFactory,
) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_fault("E7", "电机过流")
    h.wait_state(S.FAULTED)
    assert "E7" in (h.snapshot().fault_reason or "")
    h.engine.resume(OPERATOR)
    assert eventually(lambda: h.log.alerts(AlertCode.RESUME_REJECTED), 10.0)
    assert h.state() is S.FAULTED

    h.device.clear_faults()
    h.engine.resume(OPERATOR)
    h.incubating()
    h.fire_rows()
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_device_disconnect_faults_and_resume_reconnects(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_disconnect()
    h.wait_state(S.FAULTED)
    assert "通信断开" in (h.snapshot().fault_reason or "")
    h.engine.resume(OPERATOR)
    h.incubating()
    h.engine.stop(OPERATOR)
    h.finished()
    assert h.stored().state is S.STOPPED


def test_restore_failure_keeps_the_capture_and_retries_restore_on_resume(
    harness: HarnessFactory,
) -> None:
    """图像已经抓到，复位失败不浪费这次读数：分析结果在恢复后入库，复位再试一次。"""
    h = harness()
    h.device.fail_next["restore_row"] = DeviceFaultError("复位卡住")
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.log.alerts(AlertCode.RESTORE_FAILED)
    assert h.device.is_flipped(1)
    assert h.media_counts() == (1, 6)
    assert h.pipeline.buffer.stats().pins == 0
    assert not h.pipeline.recorder.active

    assert eventually(lambda: len(h.analyzer.requests) == 1, 10.0)
    h.engine.resume(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert h.device.flipped_rows() == [1]  # 没有重新翻转
    assert [name for name, _ in h.device.calls].count("restore_row") == 2
    h.assert_clean()


def test_stop_retries_restore_of_a_row_left_flipped(harness: HarnessFactory) -> None:
    h = harness()
    h.device.fail_next["restore_row"] = DeviceFaultError("复位卡住")
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.device.is_flipped(1)
    h.engine.stop(OPERATOR)
    h.finished()
    assert h.stored().state is S.STOPPED
    h.assert_clean()


def test_door_open_delays_reading_without_fault(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.set_door(False)
    assert eventually(lambda: h.log.alerts(AlertCode.DOOR_OPEN), 10.0)
    h.fire_rows()
    assert not eventually(lambda: h.device.flipped_rows() != [], 0.1)
    assert h.state() is S.INCUBATING
    h.device.set_door(True)
    h.finished()
    assert h.stored().state is S.COMPLETED


# ---------------------------------------------------------------------- 温度


def test_brief_temperature_excursion_only_warns(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_temperature_excursion(2.0)
    assert eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_EXCURSION), 10.0)
    h.schedule.advance(10.0)  # 持续时间 30 秒，还没到
    h.device.clear_faults()
    assert eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_RECOVERED), 10.0)
    assert h.state() is S.INCUBATING
    # 回到范围后计时清零：新的越限重新计时。
    h.device.inject_temperature_excursion(-2.0)
    assert eventually(lambda: len(h.log.alerts(AlertCode.TEMPERATURE_EXCURSION)) == 2, 10.0)
    h.schedule.advance(29.0)
    assert not eventually(lambda: h.state() is S.FAULTED, 0.05)
    h.device.clear_faults()
    h.engine.stop(OPERATOR)
    h.finished()


def test_tolerance_boundary_is_inclusive(harness: HarnessFactory) -> None:
    h = harness(config=fast_config(temperature_tolerance_c=5.0))
    h.save_and_start()
    h.incubating()
    h.device.inject_temperature_excursion(4.0)
    assert not eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_EXCURSION), 0.05)
    h.engine.stop(OPERATOR)
    h.finished()


def test_sustained_temperature_excursion_faults_and_audits(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_temperature_excursion(2.0)
    assert eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_EXCURSION), 10.0)
    h.schedule.advance(31.0)
    h.wait_state(S.FAULTED)
    assert fault_kinds(h) == {FaultKind.TEMPERATURE}
    assert any(
        "fault" in d and "温度" in d for d in h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)
    )
    h.assert_clean()

    h.engine.resume(OPERATOR)  # 还在越限，拒绝
    assert eventually(lambda: h.log.alerts(AlertCode.RESUME_REJECTED), 10.0)
    h.device.clear_faults()
    h.engine.resume(OPERATOR)
    h.incubating()
    h.fire_rows()
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_unreadable_temperature_counts_as_excursion(harness: HarnessFactory) -> None:
    h = harness()
    original = h.device.read_status

    def no_temperature() -> object:
        return original().model_copy(update={"temperature_c": None})

    h.save_and_start()
    h.incubating()
    h.device.read_status = no_temperature  # type: ignore[method-assign, assignment]
    assert eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_EXCURSION), 10.0)
    h.schedule.advance(31.0)
    h.wait_state(S.FAULTED)
    h.device.read_status = original  # type: ignore[method-assign]


def test_excursion_during_analysis_defers_the_result_until_resume(
    harness: HarnessFactory,
) -> None:
    """异常期间结果不能写库（领域规则），恢复后写入，不丢失。"""
    h = harness()
    gate = Gate()
    h.analyzer.gates[1] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.device.inject_temperature_excursion(2.0)
    assert eventually(lambda: h.log.alerts(AlertCode.TEMPERATURE_EXCURSION), 10.0)
    h.schedule.advance(31.0)
    h.wait_state(S.FAULTED)
    gate.release.set()
    assert h.analyzer.closed.is_set() is False
    h.device.clear_faults()
    h.engine.resume(OPERATOR)
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert len(h.stored().results) == 8
    assert len(h.analyzer.requests) == 1


# ---------------------------------------------------------------------- 回调


def test_listener_exceptions_do_not_affect_the_engine(harness: HarnessFactory) -> None:
    h = harness(config=fast_config(time_scale=36000.0), use_scaled_schedule=True)
    calls: list[EngineEvent] = []

    def bad(event: EngineEvent) -> None:
        calls.append(event)
        raise ValueError("回调的 bug")

    h.engine.add_listener(bad)
    h.save_and_start()
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert calls
    assert h.log.states()[-1] is S.COMPLETED  # 其他监听者不受影响


def test_hung_listener_does_not_stall_the_engine(tmp_path: Path, thread_baseline: None) -> None:
    h = make_harness(
        tmp_path,
        config=fast_config(time_scale=36000.0, worker_stop_timeout_s=0.05),
        use_scaled_schedule=True,
    )
    release = threading.Event()

    def hung(_event: EngineEvent) -> None:
        release.wait(30.0)

    h.engine.add_listener(hung)
    h.save_and_start()
    h.finished()
    assert h.stored().state is S.COMPLETED
    release.set()
    h.close()


# ---------------------------------------------------------------------- 存储


def test_results_and_audit_stay_consistent_when_commit_fails(tmp_path: Path) -> None:
    storage: list[FlakyStorage] = []

    def wrap(database: Database) -> FlakyStorage:
        storage.append(FlakyStorage(database))
        return storage[0]

    h = make_harness(tmp_path, storage_wrapper=wrap)
    gate = Gate()
    h.analyzer.gates[1] = gate
    try:
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        gate.wait_entered()
        h.wait_state(S.ANALYZING)
        storage[0].result_failures_left = 1
        gate.release.set()
        h.wait_state(S.FAULTED)

        stored = h.stored()
        assert stored.results == {}
        assert h.audit_details(AuditAction.ROW_ANALYZED) == []
        assert FaultKind.STORAGE in fault_kinds(h)
        assert h.log.alerts(AlertCode.STORAGE_FAILED)
        assert verify_audit_chain(h.database).ok

        h.engine.retry_analysis(OPERATOR)  # 只有存储原因时用恢复
        assert eventually(lambda: h.log.alerts(AlertCode.RESUME_REJECTED), 10.0)
        h.engine.resume(OPERATOR)
        h.finished()
        stored = h.stored()
        assert stored.state is S.COMPLETED
        assert len(stored.results) == 8
        assert len(h.audit_details(AuditAction.ROW_ANALYZED)) == 1
        h.assert_clean()
    finally:
        h.close()


def test_database_gone_for_good_aborts_run_and_recovery_marks_it(
    tmp_path: Path, thread_baseline: None
) -> None:
    """库彻底写不进去时不再做任何无审计的动作：运行中止，下次启动标记为异常。"""
    storage: list[FlakyStorage] = []

    def wrap(database: Database) -> FlakyStorage:
        storage.append(FlakyStorage(database))
        return storage[0]

    h = make_harness(tmp_path, storage_wrapper=wrap)
    try:
        h.save_and_start()
        h.incubating()
        storage[0].failures_left = 10**6
        h.fire_rows()
        h.finished()
        assert h.device.flipped_rows() == []  # 到时事务都提交不了，不能翻转
        assert h.stored().state is S.INCUBATING
        assert h.log.alerts(AlertCode.STORAGE_FAILED)
        assert h.pipeline.buffer.stats().pins == 0
        storage[0].failures_left = 0
        recovered = h.engine.recover_interrupted()
        assert [r.experiment_id for r in recovered] == [EXP_ID]
        assert h.stored().state is S.FAULTED
        assert verify_audit_chain(h.database).ok
    finally:
        h.close()


# ---------------------------------------------------------------------- 启动恢复


@pytest.mark.parametrize("state", [S.INCUBATING, S.READING, S.ANALYZING])
def test_recovery_marks_in_progress_experiments_as_faulted(
    harness: HarnessFactory, state: S
) -> None:
    h = harness()
    experiment = ready_experiment(h.tubes, "CRASHED")
    experiment.apply(ExperimentEvent.START)
    if state is not S.INCUBATING:
        experiment.apply(ExperimentEvent.ROW_DUE)
    if state is S.ANALYZING:
        experiment.apply(ExperimentEvent.ROW_CAPTURED)
    assert experiment.state is state
    h.database.experiments.save(experiment)
    h.database.experiments.save(ready_experiment(h.tubes, "UNTOUCHED"))

    recovered = h.engine.recover_interrupted()

    assert [r.experiment_id for r in recovered] == ["CRASHED"]
    assert recovered[0].previous is state
    assert "没有正常结束" in recovered[0].reason
    crashed = h.stored("CRASHED")
    assert crashed.state is S.FAULTED
    assert h.stored("UNTOUCHED").state is S.READY
    details = [r.event.detail for r in h.audit("CRASHED")]
    assert len(details) == 1 and recovered[0].reason in details[0]
    assert h.device.calls == []  # 不自动继续，也不碰设备
    assert h.engine.active_experiment_id is None
    assert eventually(lambda: len(h.log.of(StateChanged)) == 1, 5.0)
    assert h.engine.recover_interrupted() == []  # 幂等


def test_recovered_experiment_can_only_be_stopped(harness: HarnessFactory) -> None:
    h = harness()
    experiment = ready_experiment(h.tubes, "CRASHED")
    experiment.apply(ExperimentEvent.START)
    h.database.experiments.save(experiment)
    h.engine.recover_interrupted()
    with pytest.raises(EngineStateError):
        h.engine.start("CRASHED", OPERATOR)
    with pytest.raises(EnginePermissionError):
        h.engine.stop_faulted("CRASHED", ANALYST)
    h.engine.stop_faulted("CRASHED", OPERATOR, "人工确认后废弃")
    assert h.stored("CRASHED").state is S.STOPPED
    assert "人工确认后废弃" in h.audit("CRASHED")[-1].event.detail
    with pytest.raises(EngineStateError):
        h.engine.stop_faulted("CRASHED", OPERATOR)


def test_recovery_is_refused_while_an_experiment_is_running(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    with pytest.raises(EngineBusyError):
        h.engine.recover_interrupted()
    with pytest.raises(EngineBusyError):
        h.engine.stop_faulted(EXP_ID, OPERATOR)
    h.engine.stop(OPERATOR)
    h.finished()


def test_shutdown_during_a_run_marks_faulted_and_releases_everything(
    harness: HarnessFactory,
) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    assert h.engine.shutdown(30.0)
    assert h.stored().state is S.FAULTED
    assert any("软件退出" in d for d in h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED))
    assert h.analyzer.closed.is_set()
    h.assert_clean()


def test_restart_recovery_uses_a_fresh_engine_on_the_same_database(tmp_path: Path) -> None:
    path = tmp_path / "gel.db"
    database = open_database(path)
    experiment = ready_experiment((1,), "CRASHED")
    experiment.apply(ExperimentEvent.START)
    database.experiments.save(experiment)
    h = make_harness(tmp_path / "other")
    engine = ExperimentEngine(
        h.device, h.pipeline, h.analyzer, database, schedule_clock=ManualClock()
    )
    assert len(engine.recover_interrupted()) == 1
    stored = database.experiments.get("CRASHED")
    assert stored is not None and stored.state is S.FAULTED
    assert verify_audit_chain(database).ok
    assert stored.results == {}
    database.close()
    h.close()
