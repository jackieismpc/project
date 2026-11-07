"""试验引擎端到端：SimDevice + SimVideoSource + MockAnalyzer + 真实 SQLite。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import pytest

from gelinspect.domain.enums import (
    AuditAction,
    GelState,
    Interpretation,
    ReviewStatus,
    TubeAnalysisStatus,
)
from gelinspect.domain.plate import ROW_COUNT, TUBE_COUNT
from gelinspect.domain.state_machine import ExperimentState as S
from gelinspect.infrastructure.db import verify_audit_chain
from gelinspect.interfaces.device import TrayPosition
from gelinspect.interfaces.storage import MediaKind, PageRequest
from gelinspect.services.engine_types import (
    AlertCode,
    EngineBusyError,
    EngineConfig,
    EnginePermissionError,
    EngineStateError,
    RowCountdown,
    RowPhase,
    TemperatureChanged,
    WellStatus,
    WellUpdated,
)
from gelinspect.services.video.errors import InvalidIdentifierError
from simulation.sample_data import (
    SENSITIVITY_EXPECTED,
    SENSITIVITY_STATES,
    sensitivity_scenario,
)
from simulation.scenario import Scenario
from tests.services.engine_helpers import (
    ANALYST,
    EXP_ID,
    OPERATOR,
    Gate,
    Harness,
    eventually,
    fast_config,
    gelled_scenario,
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


def accelerated(**overrides: object) -> EngineConfig:
    """60 分钟孵育压缩到约 0.1 秒。"""
    return fast_config(time_scale=36000.0, **overrides)


def test_sensitivity_17_tubes_runs_to_pending_review(harness: HarnessFactory) -> None:
    tubes = tuple(range(1, 18))
    h = harness(
        tubes, scenario=sensitivity_scenario(), config=accelerated(), use_scaled_schedule=True
    )
    h.save_and_start()
    h.finished()

    stored = h.stored()
    assert stored.state is S.PENDING_REVIEW
    assert stored.pending_review_tubes == (7,)
    assert {no: r.auto_interpretation for no, r in stored.results.items()} == SENSITIVITY_EXPECTED
    assert {no: r.gel_state for no, r in stored.results.items()} == SENSITIVITY_STATES
    assert stored.results[7].review_status is ReviewStatus.PENDING
    first = stored.results[1]
    assert first.model_version == "mock-1.0"
    assert first.elapsed_ms is not None and first.features["delta_s"] is not None

    # 只处理有样品的排，第 3 排只有 17 号管。
    assert h.device.flipped_rows() == [1, 2, 3]
    by_row = {r.row_index: [t.tube_no for t in r.tubes] for r in h.analyzer.requests}
    assert by_row == {1: list(range(1, 9)), 2: list(range(9, 17)), 3: [17]}
    assert {r.reagent_batch for r in h.analyzer.requests} == {"LAL1"}
    assert all(r.flip_angle_deg is not None for r in h.analyzer.requests)

    # 录制和抓拍文件都存在，哈希入库。
    assert h.media_counts() == (3, 18)
    h.assert_media_intact()
    for media in h.database.media.list_for_experiment(EXP_ID, kind=MediaKind.VIDEO):
        capture = cv2.VideoCapture(media.path)
        assert capture.read()[0]
        capture.release()

    # 状态路径合法，审计与状态一一对应，哈希链完好。
    assert h.log.states()[0] is S.INCUBATING
    assert h.log.states()[-1] is S.PENDING_REVIEW
    changes = h.audit_details(AuditAction.EXPERIMENT_STATE_CHANGED)
    assert len(changes) == len(h.log.states())
    assert len(h.audit_details(AuditAction.ROW_ANALYZED)) == 3
    assert "operator" in {r.event.actor for r in h.audit()}
    h.assert_clean()
    assert h.device.read_status().tray_position is TrayPosition.OUT

    # 孔状态：17 支管都走完了，7 号待复核。
    wells = [e for e in h.log.of(WellUpdated) if isinstance(e, WellUpdated)]
    final = {e.tube_no: e.status for e in wells}
    assert final[7] is WellStatus.PENDING_REVIEW
    assert final[1] is WellStatus.POSITIVE and final[17] is WellStatus.NEGATIVE
    assert len(final) == 17
    assert h.log.of(RowCountdown) and h.log.of(TemperatureChanged)
    assert h.database.telemetry.query_temperatures(experiment_id=EXP_ID, page=PageRequest()).total


def test_full_plate_96_tubes_runs_to_completed(harness: HarnessFactory) -> None:
    states: dict[int, GelState | None] = {
        no: GelState.FULLY_GELLED if no % 2 else GelState.NOT_GELLED
        for no in range(1, TUBE_COUNT + 1)
    }
    tubes = tuple(range(1, TUBE_COUNT + 1))
    h = harness(
        tubes, scenario=Scenario(tube_states=states), config=accelerated(), use_scaled_schedule=True
    )
    h.save_and_start()
    h.finished()

    stored = h.stored()
    assert stored.state is S.COMPLETED
    assert len(stored.results) == TUBE_COUNT
    assert all(
        r.auto_interpretation is (Interpretation.POSITIVE if no % 2 else Interpretation.NEGATIVE)
        for no, r in stored.results.items()
    )
    assert h.device.flipped_rows() == list(range(1, ROW_COUNT + 1))
    assert h.media_counts() == (ROW_COUNT, ROW_COUNT * 6)
    h.assert_media_intact()
    h.assert_clean()
    verification = verify_audit_chain(h.database)
    assert verification.ok and verification.checked == len(h.audit())


def test_measured_results_come_from_the_right_row(harness: HarnessFactory) -> None:
    """测量模式真的处理画面：翻转前的帧必须拍到第 2 排，而不是上一次对准的排。"""
    states: dict[int, GelState | None] = dict.fromkeys(range(1, TUBE_COUNT + 1))
    states.update(dict.fromkeys(range(1, 9), GelState.NOT_GELLED))
    states.update(dict.fromkeys(range(9, 17), GelState.FULLY_GELLED))
    h = harness(
        tubes_of_rows(2),
        scenario=Scenario(tube_states=states),
        config=accelerated(),
        use_scaled_schedule=True,
        width=960,
        height=540,
    )
    h.analyzer.scripted = False
    h.save_and_start()
    h.finished()
    stored = h.stored()
    assert stored.state is S.COMPLETED, h.log.alerts()
    assert {r.gel_state for r in stored.results.values()} == {GelState.FULLY_GELLED}
    assert all(r.analysis_status is TubeAnalysisStatus.OK for r in stored.results.values())
    calls = [c for c in h.device.calls if c[0] in {"select_row", "flip_row"}]
    assert calls == [("select_row", 2), ("flip_row", 2)]


def test_row_flow_follows_the_specified_order(harness: HarnessFactory) -> None:
    h = harness(config=accelerated(), use_scaled_schedule=True)
    h.save_and_start()
    h.finished()
    names = [name for name, _ in h.device.calls]
    assert names == [
        "connect",
        "move_tray",
        "select_row",
        "flip_row",
        "restore_row",
        "move_tray",
    ]
    assert h.device.threads == {"experiment-engine"}
    assert h.analyzer.threads == {"engine-analysis"}
    assert h.analyzer.closed.is_set()


def test_incubation_counts_from_start_on_the_schedule_clock(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.schedule.advance(3599.0)
    h.wait_rows(lambda rows: rows[0].remaining_s < 2.0)
    assert h.device.flipped_rows() == []
    assert h.state() is S.INCUBATING
    h.schedule.advance(2.0)
    h.finished()
    assert h.stored().state is S.COMPLETED


def test_only_requested_time_scale_accelerates() -> None:
    assert EngineConfig().time_scale == 1.0
    assert EngineConfig().incubation_s == 3600.0


def test_analysis_does_not_block_the_main_loop(harness: HarnessFactory) -> None:
    """第 1 排的算法卡住时，第 2 排照常按时读数，倒计时和温度照常更新。"""
    h = harness(tubes_of_rows(1, 2))
    gate = Gate()
    h.analyzer.gates[1] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()
    h.wait_rows(lambda rows: all(r.phase is RowPhase.ANALYZING for r in rows))
    assert h.device.flipped_rows() == [1, 2]
    countdowns = len(h.log.of(RowCountdown))
    temperatures = len(h.log.of(TemperatureChanged))

    assert eventually(
        lambda: (
            len(h.log.of(RowCountdown)) > countdowns
            and len(h.log.of(TemperatureChanged)) > temperatures
        ),
        10.0,
    )
    assert h.state() is S.ANALYZING
    gate.release.set()
    h.finished()
    assert h.stored().state is S.COMPLETED
    h.assert_clean()


def test_analysis_timeout_only_warns_and_is_audited(harness: HarnessFactory) -> None:
    h = harness(config=fast_config(analysis_timeout_s=0.01))
    gate = Gate()
    h.analyzer.gates[1] = gate
    h.save_and_start()
    h.incubating()
    h.fire_rows()
    gate.wait_entered()

    assert eventually(lambda: h.log.alerts(AlertCode.ANALYSIS_TIMEOUT), 10.0)
    assert h.state() is S.ANALYZING
    gate.release.set()
    h.finished()
    assert h.stored().state is S.COMPLETED
    assert len(h.audit_details(AuditAction.ANALYSIS_TIMEOUT)) == 1
    assert len(h.log.alerts(AlertCode.ANALYSIS_TIMEOUT)) == 1


@pytest.mark.parametrize(
    "status",
    [
        TubeAnalysisStatus.LOW_QUALITY,
        TubeAnalysisStatus.FAILED,
        TubeAnalysisStatus.TUBE_NOT_FOUND,
    ],
)
def test_single_tube_failures_go_to_review_never_negative(
    harness: HarnessFactory, status: TubeAnalysisStatus
) -> None:
    scenario = gelled_scenario(range(1, 9), GelState.NOT_GELLED)
    h = harness(scenario=scenario, config=accelerated(), use_scaled_schedule=True)
    h.analyzer.tube_faults[3] = status
    h.save_and_start()
    h.finished()
    stored = h.stored()
    assert stored.state is S.PENDING_REVIEW
    assert stored.results[3].analysis_status is status
    assert stored.results[3].auto_interpretation is Interpretation.PENDING_REVIEW
    assert stored.results[3].message
    assert stored.results[1].auto_interpretation is Interpretation.NEGATIVE


def test_tube_missing_from_result_goes_to_review(harness: HarnessFactory) -> None:
    scenario = gelled_scenario(range(1, 9), GelState.NOT_GELLED)
    h = harness(scenario=scenario, config=accelerated(), use_scaled_schedule=True)
    h.analyzer.drop_tubes = {5}
    h.save_and_start()
    h.finished()
    stored = h.stored()
    assert stored.state is S.PENDING_REVIEW
    assert stored.results[5].analysis_status is TubeAnalysisStatus.FAILED
    assert stored.results[5].auto_interpretation is Interpretation.PENDING_REVIEW
    assert h.log.alerts(AlertCode.ANALYSIS_CONTRACT)


def test_configured_mapping_is_applied(tmp_path: Path, thread_baseline: None) -> None:
    from gelinspect.domain.interpretation import GelStateMapping
    from gelinspect.services.experiment_engine import ExperimentEngine

    h = make_harness(
        tmp_path,
        scenario=gelled_scenario(range(1, 9), GelState.PARTIALLY_GELLED),
        config=accelerated(),
        use_scaled_schedule=True,
    )
    engine = ExperimentEngine(
        h.device,
        h.pipeline,
        h.analyzer,
        h.database,
        config=accelerated(),
        mapping=GelStateMapping(partially_gelled=Interpretation.POSITIVE),
    )
    h.database.experiments.save(ready_experiment(h.tubes))
    engine.start(EXP_ID, OPERATOR)
    assert engine.wait(30.0)
    assert h.stored().state is S.COMPLETED
    assert {r.auto_interpretation for r in h.stored().results.values()} == {Interpretation.POSITIVE}
    h.close()


# ---------------------------------------------------------------------- 启动条件


def test_start_requires_permission(harness: HarnessFactory) -> None:
    h = harness()
    h.database.experiments.save(ready_experiment(h.tubes))
    with pytest.raises(EnginePermissionError):
        h.engine.start(EXP_ID, ANALYST)
    assert h.stored().state is S.READY


@pytest.mark.parametrize("experiment_id", ["MISSING", "DRAFT"])
def test_start_requires_a_ready_experiment(harness: HarnessFactory, experiment_id: str) -> None:
    from gelinspect.domain.entities import Experiment

    h = harness()
    ready = ready_experiment(h.tubes, "DRAFT")
    draft = Experiment(
        experiment_id="DRAFT",
        experiment_type=ready.experiment_type,
        template_id=ready.template_id,
        instrument_no=ready.instrument_no,
        operator=ready.operator,
        created_at=ready.created_at,
        assignments=ready.assignments,
    )
    h.database.experiments.save(draft)
    with pytest.raises(EngineStateError):
        h.engine.start(experiment_id, OPERATOR)


def test_start_rejects_identifier_unusable_as_path(harness: HarnessFactory) -> None:
    h = harness()
    with pytest.raises(InvalidIdentifierError):
        h.engine.start("../evil", OPERATOR)


def test_second_start_is_rejected_while_running(harness: HarnessFactory) -> None:
    h = harness()
    h.save_and_start()
    h.incubating()
    h.database.experiments.save(ready_experiment(h.tubes, "EXP-T-2"))
    with pytest.raises(EngineBusyError):
        h.engine.start("EXP-T-2", OPERATOR)
    h.engine.stop(OPERATOR)
    h.finished()


@pytest.mark.parametrize("problem", ["refuse", "fault", "door", "temperature", "load", "tray"])
def test_preflight_failure_leaves_experiment_ready(harness: HarnessFactory, problem: str) -> None:
    from gelinspect.interfaces.device import DeviceFaultError

    h = harness()
    if problem == "refuse":
        h.device.refuse_connection()
    elif problem == "fault":
        h.device.inject_fault("E7", "电机过流")
    elif problem == "door":
        h.device.set_door(False)
    elif problem == "temperature":
        h.device.inject_temperature_excursion(5.0)
    elif problem == "load":
        h.analyzer.fail_load = True
    else:
        h.device.fail_next["move_tray"] = DeviceFaultError("滑台卡住")
    h.save_and_start()
    h.finished()
    assert h.stored().state is S.READY
    assert h.log.alerts(AlertCode.START_FAILED)
    assert h.audit_details(AuditAction.ENGINE_ALERT)
    assert h.device.flipped_rows() == []
    h.assert_clean()

    # 排除问题后可以重新启动。
    h.device.clear_faults()
    h.device.refuse_connection(False)
    h.device.set_door(True)
    h.analyzer.fail_load = False
    h.engine.start(EXP_ID, OPERATOR)
    h.incubating()
    h.engine.stop(OPERATOR)
    h.finished()
