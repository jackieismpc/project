"""退出：进行中的试验先确认，有序关闭，没有线程残留。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.infrastructure.db import open_database
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.widgets.dialog import ConfirmDialog
from tests.services.engine_helpers import eventually, ready_experiment
from tests.ui.conftest import settle, sign_in, worker_threads


def start_run(app: QApplication, window: MainWindow, context: AppContext, name: str) -> None:
    sign_in(app, window, "experimenter")
    session = window.session
    assert session is not None
    context.database.experiments.save(ready_experiment(range(1, 9), name))
    context.engine.start(name, session.user)
    assert eventually(lambda: context.engine.active_experiment_id == name)


def test_closing_an_idle_window_needs_no_confirmation(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "analyst")
    window.close()
    settle(qapp)
    assert not window.isVisible()
    page = app_context.database.audit.query(
        AuditQuery(action=AuditAction.LOGOUT, actor="analyst"), PageRequest()
    )
    assert len(page.items) == 1


def test_closing_during_a_run_asks_and_can_be_cancelled(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def decline(parent: object, title: str, message: str, **kwargs: object) -> bool:
        asked.append(message)
        assert kwargs["danger"] is True
        return False

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(decline))
    start_run(qapp, window, app_context, "EXP-QUIT-1")
    window.close()
    settle(qapp)
    assert window.isVisible()
    assert len(asked) == 1 and "EXP-QUIT-1" in asked[0]
    assert app_context.engine.active_experiment_id == "EXP-QUIT-1"  # 试验没有被打断
    assert window.session is not None  # 也没有退出登录
    app_context.engine.stop(window.session.user)
    assert app_context.engine.wait(30.0)


def test_confirmed_quit_shuts_everything_down_in_order(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *a, **k: True))
    start_run(qapp, window, app_context, "EXP-QUIT-2")
    assert any(name == "instrument-monitor" for name in worker_threads())
    window.close()
    settle(qapp)
    assert not window.isVisible()
    app_context.close()
    app_context.close()  # 可重复调用
    assert worker_threads() == []
    # 被打断的试验转异常，并有审计
    reopened = open_database(app_context.config.database_path)
    try:
        experiment = reopened.experiments.get("EXP-QUIT-2")
        assert experiment is not None and experiment.state is ExperimentState.FAULTED
    finally:
        reopened.close()


def test_closing_a_locked_window_during_a_run_is_refused(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
) -> None:
    start_run(qapp, window, app_context, "EXP-QUIT-3")
    window.lock_screen()
    window.close()
    settle(qapp)
    assert window.isVisible()  # 没有弹确认框（夹具会让弹框立刻失败），直接拒绝
    assert window.session is not None
    window.lock_page.password.setText("Experimenter@2026")
    window.lock_page.submit_button.click()
    assert window.session is not None and not window.session.locked
    app_context.engine.stop(window.session.user)
    assert app_context.engine.wait(30.0)


def test_no_threads_remain_after_a_normal_session(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "admin")
    assert eventually(lambda: "instrument-monitor" in worker_threads())
    window.close()
    app_context.close()
    assert worker_threads() == []
