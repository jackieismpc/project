"""软件重启后重试分析：抓帧时间入库，原图还在就能离线重新分析，不碰设备，不翻转。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.interfaces.analysis import AnalyzerError
from gelinspect.interfaces.storage import MediaKind
from gelinspect.services.engine_types import AlertCode, EngineStateError, RowPhase
from gelinspect.services.experiment_engine import ExperimentEngine
from gelinspect.services.mask_archive import MASK_PREFIX
from tests.services.engine_helpers import (
    EXP_ID,
    OPERATOR,
    WAIT_S,
    EventLog,
    Harness,
    fast_config,
    make_harness,
    ready_experiment,
    tubes_of_rows,
)


@pytest.fixture
def faulted(tmp_path: Path, thread_baseline: None) -> Iterator[Harness]:
    """两排都已抓帧，第 1 排分析失败，试验转异常，然后「进程退出」。"""
    h = make_harness(tmp_path, tubes_of_rows(1, 2))
    h.analyzer.errors[1] = [AnalyzerError("模型未加载")]
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    h.wait_state(S.FAULTED)
    assert h.engine.shutdown(WAIT_S)
    yield h
    h.close()


def restart(h: Harness, log: EventLog | None = None) -> ExperimentEngine:
    return ExperimentEngine(
        h.device,
        h.pipeline,
        h.analyzer,
        h.database,
        config=fast_config(),
        schedule_clock=h.schedule,
        on_event=log,
    )


def test_capture_time_and_angle_are_stored(faulted: Harness) -> None:
    media = faulted.database.media.list_for_experiment(EXP_ID, kind=MediaKind.IMAGE)
    raw = [m for m in media if not Path(m.path).name.startswith(MASK_PREFIX)]
    assert raw and all(m.captured_at is not None for m in raw)
    assert all(m.flip_angle_deg is not None for m in raw)


def test_retry_after_restart_finishes_from_disk_without_flipping(faulted: Harness) -> None:
    h = faulted
    flips = h.device.flipped_rows()
    requests = len(h.analyzer.requests)

    def no_device(*_args: object) -> None:
        raise AssertionError("离线重试不应该碰设备")

    h.device.connect = no_device  # type: ignore[method-assign]
    h.device.read_status = no_device  # type: ignore[method-assign,assignment]
    engine = restart(h)
    assert engine.retryable_rows(EXP_ID) == (1,)

    engine.retry_interrupted(EXP_ID, OPERATOR)
    assert engine.wait(WAIT_S)
    assert engine.shutdown(WAIT_S)

    stored = h.stored()
    assert stored.state is S.COMPLETED
    assert set(stored.results) == set(range(1, 17))
    assert h.device.flipped_rows() == flips  # 没有再翻转
    assert [r.row_index for r in h.analyzer.requests[requests:]] == [1]
    retried = h.analyzer.requests[-1]
    assert retried.flip_angle_deg is not None
    assert all(frame.captured_at is not None for frame in retried.frames_after)
    assert stored.conclusion is not None and stored.completed_at is not None
    details = h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)
    assert any("人工恢复" in d and "Row01" in d for d in details)
    assert any(
        r.event.actor == OPERATOR.username and "resume_analysis" in r.event.detail
        for r in h.audit()
    )


def test_retry_refuses_altered_images_and_stays_faulted(faulted: Harness) -> None:
    h = faulted
    # 第 1 排的一张原图被改过
    row1 = [
        Path(m.path)
        for m in h.database.media.list_for_experiment(EXP_ID, kind=MediaKind.IMAGE)
        if m.row_index == 1 and not Path(m.path).name.startswith(MASK_PREFIX)
    ]
    with row1[0].open("ab") as handle:
        handle.write(b"tampered")
    log = EventLog()
    engine = restart(h, log)
    engine.retry_interrupted(EXP_ID, OPERATOR)
    assert engine.wait(WAIT_S)
    engine.shutdown(WAIT_S)
    stored = h.stored()
    assert stored.state is S.FAULTED
    assert not set(range(1, 9)) & set(stored.results)
    assert log.alerts(AlertCode.ANALYSIS_FAILED)


def test_records_without_capture_time_cannot_be_retried(faulted: Harness) -> None:
    h = faulted
    with h.database.session() as session:
        session.execute(text("UPDATE media_files SET captured_at = NULL"))
    engine = restart(h)
    assert engine.retryable_rows(EXP_ID) == ()
    with pytest.raises(EngineStateError):
        engine.retry_interrupted(EXP_ID, OPERATOR)
    engine.shutdown(WAIT_S)


def test_only_faulted_experiments_can_be_retried(tmp_path: Path, thread_baseline: None) -> None:
    h = make_harness(tmp_path, tubes_of_rows(1))
    h.database.experiments.save(ready_experiment(h.tubes))
    assert h.engine.retryable_rows(EXP_ID) == ()
    with pytest.raises(EngineStateError):
        h.engine.retry_interrupted(EXP_ID, OPERATOR)
    h.close()


def test_unread_rows_send_the_experiment_back_to_faulted(
    tmp_path: Path, thread_baseline: None
) -> None:
    """第 1 排已抓帧、分析失败；第 2 排没有读数。重试后第 1 排有结果，试验回到异常，只能停止。"""
    h = make_harness(tmp_path, tubes_of_rows(1, 2))
    h.analyzer.errors[1] = [AnalyzerError("模型未加载")]
    h.database.experiments.save(ready_experiment(h.tubes))
    h.engine.start(EXP_ID, OPERATOR)
    h.incubating()
    run = h.engine._run  # noqa: SLF001
    assert run is not None
    # 第 2 排的到时往后推：只有第 1 排到时。
    run._rows[2].deadline = float("inf")  # noqa: SLF001
    h.fire_rows()
    h.wait_rows(lambda rows: rows[0].phase is RowPhase.ANALYSIS_FAILED)
    assert h.engine.shutdown(WAIT_S)
    assert h.stored().state is S.FAULTED

    engine = restart(h)
    assert engine.retryable_rows(EXP_ID) == (1,)
    engine.retry_interrupted(EXP_ID, OPERATOR)
    assert engine.wait(WAIT_S)
    stored = h.stored()
    assert stored.state is S.FAULTED
    assert set(stored.results) == set(range(1, 9))
    assert any("不能续读" in d for d in h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED))
    assert engine.retryable_rows(EXP_ID) == ()  # 没有还能分析的排
    engine.stop_faulted(EXP_ID, OPERATOR, "只能停止")
    assert h.stored().state is S.STOPPED
    assert h.device.flipped_rows() == [1]
    engine.shutdown(WAIT_S)
    h.close()


def test_retry_can_fail_again_and_be_retried(faulted: Harness) -> None:
    h = faulted
    engine = restart(h)
    h.analyzer.errors[1] = [AnalyzerError("又失败了")]
    engine.retry_interrupted(EXP_ID, OPERATOR)
    assert engine.wait(WAIT_S)
    assert h.stored().state is S.FAULTED
    assert engine.active_experiment_id is None
    engine.shutdown(WAIT_S)
    # 再次重试仍然可以（新的离线运行）
    engine2 = restart(h)
    assert engine2.retryable_rows(EXP_ID) == (1,)
    engine2.retry_interrupted(EXP_ID, OPERATOR)
    assert engine2.wait(WAIT_S)
    engine2.shutdown(WAIT_S)
    assert h.stored().state is S.COMPLETED
