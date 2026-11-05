"""用户停止：引擎的每个阶段都要停得干净，并写入审计。"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.interfaces.device import TrayPosition
from gelinspect.services.engine_types import AlertCode, EngineBusyError
from tests.services.engine_helpers import (
    ANALYST,
    OPERATOR,
    Gate,
    Harness,
    eventually,
    fast_config,
    make_harness,
    ready_experiment,
    tubes_of_rows,
)

HarnessFactory = Callable[..., Harness]


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


def assert_stopped(h: Harness) -> None:
    h.finished()
    assert h.stored().state is S.STOPPED
    assert h.log.states()[-1] is S.STOPPED
    last = h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)[-1]
    assert "stopped" in last and "换批" in last
    actors = [r.event.actor for r in h.audit() if "stopped" in r.event.detail]
    assert actors == [OPERATOR.username]
    h.assert_clean()
    h.assert_media_intact()
    assert h.device.read_status().tray_position is TrayPosition.OUT
    assert h.analyzer.closed.is_set()


def test_stop_needs_permission_and_a_run(harness: HarnessFactory) -> None:
    h = harness()
    assert not h.engine.stop(OPERATOR)
    from gelinspect.services.engine_types import EnginePermissionError

    with pytest.raises(EnginePermissionError):
        h.engine.stop(ANALYST)


def test_stop_during_preflight_keeps_experiment_ready(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["connect"] = gate
    h.save_and_start()
    gate.wait_entered()
    assert h.engine.stop(OPERATOR, "换批")
    gate.release.set()
    h.finished()
    assert h.stored().state is S.READY  # 还没开始孵育，可以重新启动
    assert h.log.alerts(AlertCode.START_CANCELLED)
    assert any("换批" in d for d in h.audit_details(AuditAction.ENGINE_ALERT))
    assert h.device.flipped_rows() == []
    h.assert_clean()


def test_stop_while_tray_is_moving_in_returns_the_tray(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["move_tray"] = gate
    h.save_and_start()
    gate.wait_entered()
    h.engine.stop(OPERATOR, "换批")
    gate.release.set()
    del h.device.gates["move_tray"]
    h.finished()
    assert h.stored().state is S.READY
    assert h.device.read_status().tray_position is TrayPosition.OUT
    h.assert_clean()


def test_stop_while_incubating(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.engine.stop(OPERATOR, "换批")
    assert_stopped(h)
    assert h.device.flipped_rows() == []
    assert h.media_counts() == (0, 0)


def test_stop_before_flip_never_flips(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["select_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.engine.stop(OPERATOR, "换批")
    gate.release.set()
    assert_stopped(h)
    assert h.device.flipped_rows() == []
    assert h.media_counts() == (0, 0)
    assert h.analyzer.requests == []


def test_stop_during_flip_restores_and_seals_the_segment(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["flip_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.wait_more_frames()
    h.engine.stop(OPERATOR, "换批")
    gate.release.set()
    assert_stopped(h)
    names = [name for name, _ in h.device.calls]
    assert names.count("flip_row") == 1 and names.count("restore_row") == 1
    assert h.media_counts() == (1, 0)  # 录制段入库，没有抓帧
    assert h.analyzer.requests == []


def test_stop_during_grab_returns_without_waiting_for_the_timeout(
    harness: HarnessFactory,
) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["flip_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.source.inject_timeouts(10**6)  # 不再产帧，抓帧会一直等翻转后的帧
    gate.release.set()
    # 翻转前的 pin 加上翻转后的 pin。
    assert eventually(lambda: h.pipeline.buffer.stats().pins == 2, 10.0)
    started = time.monotonic()
    h.engine.stop(OPERATOR, "换批")
    h.finished()
    assert time.monotonic() - started < 15.0  # 抓帧超时是 30 秒
    assert_stopped(h)
    assert h.analyzer.requests == []


def test_stop_during_restore_keeps_the_captured_files(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.device.gates["restore_row"] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.engine.stop(OPERATOR, "换批")
    gate.release.set()
    assert_stopped(h)
    assert h.media_counts() == (1, 6)  # 已有的文件随停止事务入库
    assert h.analyzer.requests == []
    assert h.stored().results == {}


def test_stop_during_analysis_discards_the_result(harness: HarnessFactory) -> None:
    h = harness()
    gate = Gate()
    h.analyzer.gates[1] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.wait_state(S.ANALYZING)
    h.engine.stop(OPERATOR, "换批")
    h.wait_state(S.STOPPED)
    gate.release.set()
    assert_stopped(h)
    assert h.stored().results == {}
    assert h.media_counts() == (1, 6)
    assert "Row01" in h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)[-1]


def test_stop_with_a_stuck_analyzer_does_not_hang(tmp_path: Path, thread_baseline: None) -> None:
    h = make_harness(tmp_path, config=fast_config(worker_stop_timeout_s=0.05))
    gate = Gate()
    h.analyzer.gates[1] = gate
    try:
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        gate.wait_entered()
        h.engine.stop(OPERATOR, "换批")
        h.finished()  # 算法还卡着，引擎已经收尾
        assert h.stored().state is S.STOPPED
        assert h.log.alerts(AlertCode.WORKER_STUCK)
        assert not h.analyzer.closed.is_set()
        # 旧调用还卡着：新试验不能启动，否则算法会被并发调用，旧线程还会关掉新模型。
        h.database.experiments.save(ready_experiment(h.tubes, "EXP-T-2"))
        with pytest.raises(EngineBusyError, match="算法"):
            h.engine.start("EXP-T-2", OPERATOR)
        assert h.stored("EXP-T-2").state is S.READY
    finally:
        gate.release.set()
        assert h.analyzer.closed.wait(10.0)  # 算法返回后线程自己结束并关闭算法
    try:
        # 算法返回后可以正常启动新试验。
        assert eventually(lambda: not h.engine.snapshot().running)  # type: ignore[union-attr]
        assert h.engine.wait(5.0)
        started = False

        def try_start() -> bool:
            nonlocal started
            try:
                h.engine.start("EXP-T-2", OPERATOR)
            except EngineBusyError:
                return False
            started = True
            return True

        assert eventually(try_start)
        assert started
        h.engine.stop(OPERATOR, "收尾")
        h.finished()
    finally:
        h.close()


def test_stop_while_faulted(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.device.inject_fault("E1", "过流")
    h.wait_state(S.FAULTED)
    h.device.clear_faults()
    h.engine.stop(OPERATOR, "换批")
    assert_stopped(h)


def test_stop_between_two_rows(harness: HarnessFactory) -> None:
    """第 1 排读完、第 2 排还没翻转时停止：第 2 排不翻转，第 1 排的文件都入库。"""
    h = harness(tubes_of_rows(1, 2))
    first, second = Gate(), Gate()
    h.device.gates["select_row"] = first
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    first.wait_entered()
    h.device.gates["select_row"] = second
    first.release.set()
    second.wait_entered()
    h.engine.stop(OPERATOR, "换批")
    second.release.set()
    assert_stopped(h)
    assert h.device.flipped_rows() == [1]
    assert h.media_counts() == (1, 6)
    # 第 1 排的结果和审计同进退：要么都入库了，要么都被停止丢弃了。
    analyzed = h.audit_details(AuditAction.ROW_ANALYZED)
    assert (len(h.stored().results), len(analyzed)) in {(0, 0), (8, 1)}


def test_stop_after_the_run_finished_returns_false(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert not h.engine.stop(OPERATOR)
    assert h.stored().state is S.COMPLETED
