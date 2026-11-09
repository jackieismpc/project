"""新试验服务与真实引擎联调：创建、启动、引擎收到的管位，以及启动失败的原因。"""

from __future__ import annotations

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import ExperimentType, ReagentKind
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.services.engine_types import AlertCode, EngineEvent
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import ExperimentDraft, NewExperimentRequest
from simulation.sim_device import SimDevice

from .conftest import GOOD_CODES, PASSWORDS
from .engine_helpers import EventLog, eventually

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]


def request_for_tubes(tubes: list[int]) -> NewExperimentRequest:
    draft = ExperimentDraft()
    draft.choose_template(SENSITIVITY)
    draft.set_selected(tubes)
    request = draft.to_request()
    return NewExperimentRequest(request.template_id, request.assignments, GOOD_CODES)


def test_the_engine_receives_exactly_the_selected_tubes(real_app: AppContext) -> None:
    session = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    tubes = list(range(10, 28))  # 跨 Row02–Row04
    experiment_id = real_app.experiments.create_experiment(session, request_for_tubes(tubes))
    real_app.experiments.start_experiment(session, experiment_id)

    def incubating() -> bool:
        snapshot = real_app.engine.snapshot()
        return snapshot is not None and snapshot.state is ExperimentState.INCUBATING

    assert eventually(incubating)
    snapshot = real_app.engine.snapshot()
    assert snapshot is not None and snapshot.experiment_id == experiment_id
    expected: dict[int, list[int]] = {}
    for tube in tubes:
        expected.setdefault(tube_to_position(tube).row_index, []).append(tube)
    engine_rows = {row.row_index: list(row.tubes) for row in snapshot.rows if row.tubes}
    assert engine_rows == expected  # 引擎按 domain.plate 的映射分排
    stored = real_app.database.experiments.get(experiment_id)
    assert stored is not None
    assert [a.tube_no for a in stored.assignments] == tubes
    real_app.engine.stop(session.user, "测试结束")
    assert real_app.engine.wait(30.0)


def test_a_failed_start_reports_why_and_the_experiment_can_be_retried(
    real_app: AppContext,
) -> None:
    session = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    log = EventLog()
    real_app.engine.add_listener(log)
    device = real_app.device
    assert isinstance(device, SimDevice)
    device.inject_temperature_excursion(+3.0)  # 40 ℃，超出 37±1 ℃
    experiment_id = real_app.experiments.create_experiment(
        session, request_for_tubes(list(range(1, 19)))
    )
    real_app.experiments.start_experiment(session, experiment_id)  # 立即返回，失败走引擎告警

    assert eventually(lambda: log.alerts(AlertCode.START_FAILED))
    alert = log.alerts(AlertCode.START_FAILED)[0]
    assert alert.experiment_id == experiment_id
    assert "温度" in alert.message and "37" in alert.message  # 说明具体原因
    stored = real_app.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.READY  # 仍为就绪，可重试

    device.clear_faults()
    assert real_app.engine.wait(30.0)
    real_app.experiments.start_experiment(session, experiment_id)
    assert eventually(
        lambda: (
            (s := real_app.engine.snapshot()) is not None and s.state is ExperimentState.INCUBATING
        )
    )
    real_app.engine.stop(session.user, "测试结束")
    assert real_app.engine.wait(30.0)


def test_starting_while_another_experiment_runs_is_refused_clearly(real_app: AppContext) -> None:
    session = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    first = real_app.experiments.create_experiment(session, request_for_tubes(list(range(1, 19))))
    real_app.experiments.start_experiment(session, first)
    assert eventually(
        lambda: (
            (s := real_app.engine.snapshot()) is not None and s.state is ExperimentState.INCUBATING
        )
    )
    second = real_app.experiments.create_experiment(session, request_for_tubes(list(range(1, 19))))
    try:
        real_app.experiments.start_experiment(session, second)
    except ServiceError as error:
        assert error.code is ServiceErrorCode.ENGINE_BUSY
    else:
        raise AssertionError("设备只有一台，不应该同时启动第二个试验")
    stored = real_app.database.experiments.get(second)
    assert stored is not None and stored.state is ExperimentState.READY
    real_app.engine.stop(session.user, "测试结束")
    assert real_app.engine.wait(30.0)


def test_reagent_locked_experiments_never_reach_the_engine(real_app: AppContext) -> None:
    from gelinspect.services.reagent_service import ReagentLockedError
    from simulation.reagents import SAMPLE_CODES

    session = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    log: list[EngineEvent] = []
    real_app.engine.add_listener(log.append)
    request = request_for_tubes(list(range(1, 19)))
    bad = NewExperimentRequest(
        request.template_id,
        request.assignments,
        {**GOOD_CODES, ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_expired"]},
    )
    try:
        real_app.experiments.create_experiment(session, bad)
    except ReagentLockedError:
        pass
    else:
        raise AssertionError("过期试剂必须被拒绝")
    assert real_app.engine.snapshot() is None and log == []
