"""监控服务：试验视图、开始时间、停止的权限和状态、默认试验。"""

from __future__ import annotations

import pytest

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import ReagentKind
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.engine_types import RowPhase, WellStatus
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.monitor_service import MonitorService, result_status
from tests.services.conftest import PASSWORDS
from tests.services.engine_helpers import eventually
from tests.services.test_new_experiment_engine import request_for_tubes
from tests.services.test_review_service import make_pending


def test_view_of_a_stored_experiment_is_read_only_and_complete(real_app: AppContext) -> None:
    from tests.services.conftest import Stack  # noqa: F401

    session = real_app.auth.login("analyst", PASSWORDS["analyst"])
    experimenter = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    experiment_id = real_app.experiments.create_experiment(
        experimenter, request_for_tubes(list(range(1, 18)))
    )
    monitor = real_app.monitor
    view = monitor.view(session, experiment_id)
    assert view.state is ExperimentState.READY and not view.live and not view.can_stop
    assert len(view.tubes) == 17 and [r.row_index for r in view.rows] == [1, 2, 3]
    assert view.reagent_batches[ReagentKind.LAL_REAGENT] == "LAL2606A"
    assert view.labeled_sensitivity == 0.25 and view.measured_sensitivity is None
    assert view.operator == "experimenter" and view.operator_name
    assert view.template_builtin and view.started_at is None
    assert all(t.status is None and t.result is None for t in view.tubes)
    assert all(r.phase is None for r in view.rows)
    assert monitor.view(session, experiment_id).tube(18) is None
    with pytest.raises(ServiceError) as missing:
        monitor.view(session, "NOPE")
    assert missing.value.code is ServiceErrorCode.EXPERIMENT_NOT_FOUND
    with pytest.raises(PermissionDeniedError):
        monitor.view(None, experiment_id)


def test_live_view_shows_row_phases_start_time_and_stop_rules(real_app: AppContext) -> None:
    monitor = real_app.monitor
    experimenter = real_app.auth.login("experimenter", PASSWORDS["experimenter"])
    analyst = real_app.auth.login("analyst", PASSWORDS["analyst"])
    experiment_id = real_app.experiments.create_experiment(
        experimenter, request_for_tubes(list(range(1, 18)))
    )
    real_app.experiments.start_experiment(experimenter, experiment_id)
    assert eventually(lambda: monitor.view(experimenter, experiment_id).live)
    assert eventually(lambda: monitor.view(experimenter, experiment_id).started_at is not None)
    view = monitor.view(experimenter, experiment_id)
    assert monitor.active_experiment_id() == experiment_id
    assert monitor.default_experiment_id(analyst) == experiment_id
    assert view.can_stop and {r.phase for r in view.rows} == {RowPhase.WAITING}
    assert {t.status for t in view.tubes} == {WellStatus.INCUBATING}
    assert all(r.remaining_s is not None and r.remaining_s > 0 for r in view.rows)

    with pytest.raises(PermissionDeniedError):  # 分析员不能停止试验
        monitor.stop(analyst, experiment_id, "x")
    assert real_app.engine.active_experiment_id == experiment_id
    monitor.stop(experimenter, experiment_id, "测试")
    assert real_app.engine.wait(30.0)
    stopped = monitor.view(experimenter, experiment_id)
    assert stopped.state is ExperimentState.STOPPED and not stopped.live and not stopped.can_stop
    with pytest.raises(ServiceError) as caught:
        monitor.stop(experimenter, experiment_id, "再停一次")
    assert caught.value.code is ServiceErrorCode.STOP_NOT_ALLOWED


def test_default_experiment_falls_back_to_the_most_recent(stack: object) -> None:
    from tests.services.conftest import Stack

    assert isinstance(stack, Stack)
    make_pending(stack)
    monitor = MonitorService(stack.auth, stack.database, _IdleEngine())
    assert monitor.default_experiment_id(stack.login("analyst")) == "EXP-REV-1"
    view = monitor.view(stack.login("analyst"), "EXP-REV-1")
    assert view.pending_review == (3, 5) and view.state is ExperimentState.PENDING_REVIEW
    assert view.last_elapsed_ms == 12.0
    assert result_status(view.tubes[2].result) is WellStatus.PENDING_REVIEW  # type: ignore[arg-type]
    assert view.tubes[0].status is WellStatus.POSITIVE


class _IdleEngine:
    active_experiment_id = None

    def snapshot(self) -> None:
        return None

    def stop(self, user: object, reason: str = "") -> bool:
        return False

    def stop_faulted(self, experiment_id: str, user: object, reason: str = "") -> None:
        raise AssertionError("不应被调用")

    def resume(self, user: object) -> bool:
        raise AssertionError("不应被调用")

    def retry_analysis(self, user: object) -> bool:
        raise AssertionError("不应被调用")

    def retry_interrupted(self, experiment_id: str, user: object) -> None:
        raise AssertionError("不应被调用")

    def retryable_rows(self, experiment_id: str) -> tuple[int, ...]:
        return ()
