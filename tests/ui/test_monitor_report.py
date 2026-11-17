"""监控页报告预览：真实分页、延迟加载、刷新、错误恢复及上下文隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtPrintSupport import QPrintPreviewWidget
from PySide6.QtWidgets import QApplication, QGraphicsView
from pytestqt.qtbot import QtBot

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import PermissionGroup
from gelinspect.services.auth import Session
from gelinspect.services.reporting.texts import ReportLanguage
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import TAB_DATA, TAB_REPORT, MonitorPage
from gelinspect.ui.pages.monitor_report import MonitorReportPreview
from tests.services.reporting.test_rendering import big_experiment
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in
from tests.ui.test_layout_languages import overflow_report, use


def prepare(context: AppContext, tubes: int = 18) -> tuple[Session, str]:
    experiment = big_experiment(tubes)
    context.database.experiments.save(experiment)
    session = context.auth.login("admin", DEMO_PASSWORDS["admin"])
    return session, experiment.experiment_id


def test_read_only_session_can_render_monitor_preview(
    qtbot: QtBot, qapp: QApplication, app_context: AppContext
) -> None:
    experiment = big_experiment(18)
    app_context.database.experiments.save(experiment)
    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    user = app_context.database.users.get_by_username("experimenter")
    assert user is not None
    app_context.users.set_permission_groups(admin, user.user_id, frozenset({PermissionGroup.VIEW}))
    session = app_context.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    pane = MonitorReportPreview(app_context.reports)
    qtbot.addWidget(pane)
    pane.show()
    pane.set_context(session, experiment.experiment_id, 1)
    pane.set_active(True)
    settle(qapp)
    assert pane.current_preview is not None
    assert pane.preview_widget.pageCount() > 0


@pytest.mark.parametrize("tubes", [18, 96])
def test_preview_is_real_paginated_and_matches_report_center(
    qtbot: QtBot, qapp: QApplication, app_context: AppContext, tmp_path: Path, tubes: int
) -> None:
    session, experiment_id = prepare(app_context, tubes)
    pane = MonitorReportPreview(app_context.reports)
    qtbot.addWidget(pane)
    pane.resize(850, 700)
    pane.show()
    pane.set_context(session, experiment_id, 1)
    assert pane.current_preview is None  # 尚未进入报告页签，不排版
    pane.set_active(True)
    settle(qapp)
    assert pane.current_preview is not None
    assert pane.current_preview.digest == app_context.reports.preview(session, experiment_id).digest
    count = pane.preview_widget.pageCount()
    assert pane.pages.count() == count
    assert count >= (3 if tubes == 96 else 1)
    assert pane.preview_widget.isVisible()
    assert pane.preview_widget.viewMode() == QPrintPreviewWidget.ViewMode.AllPagesView
    pane._select_page(count - 1)
    assert pane.preview_widget.currentPage() == count
    assert pane.preview_widget.viewMode() == QPrintPreviewWidget.ViewMode.SinglePageView
    pane.overview_button.click()
    assert pane.preview_widget.viewMode() == QPrintPreviewWidget.ViewMode.AllPagesView
    # 检查实际纸面区域有文字笔画，不能仅断言pageCount存在。
    pane._select_page(0)
    settle(qapp)
    view = pane.preview_widget.findChild(QGraphicsView)
    assert view is not None
    image = view.viewport().grab().toImage()
    assert not image.isNull()
    colors = [
        image.pixelColor(x, y)
        for x in range(20, image.width() - 20, 3)
        for y in range(20, image.height() - 20, 3)
    ]
    assert sum(c.lightness() > 240 for c in colors) > 100
    assert sum(c.lightness() < 80 for c in colors) > 20
    assert pane.grab().save(str(tmp_path / f"monitor-report-{tubes}.png"))


def test_switch_language_experiment_and_logout_clear_old_content(
    qtbot: QtBot, app_context: AppContext
) -> None:
    session, experiment_id = prepare(app_context)
    pane = MonitorReportPreview(app_context.reports)
    qtbot.addWidget(pane)
    pane.set_context(session, experiment_id, 1)
    pane.set_active(True)
    assert pane.current_preview is not None
    pane.set_active(False)
    pane.set_ui_language("en")
    assert pane.current_preview is None and pane.preview_widget.isHidden()
    pane.set_active(True)
    assert pane.current_preview is not None
    assert pane.current_preview.language is ReportLanguage.EN
    pane.set_context(session, "missing-experiment", 2)
    assert pane.current_preview is None and pane.preview_widget.isHidden()
    pane.refresh()
    assert pane.current_preview is None and pane.message.text()
    pane.set_context(None, None, None)
    pane.refresh()
    assert pane.current_preview is None and pane.pages.count() == 0


def test_paint_failure_is_logged_and_retry_recovers(
    qtbot: QtBot,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, experiment_id = prepare(app_context)
    pane = MonitorReportPreview(app_context.reports)
    qtbot.addWidget(pane)
    pane.set_context(session, experiment_id, 1)
    original = app_context.reports.paint

    def broken(*args: object) -> int:
        raise RuntimeError("injected paint failure")

    monkeypatch.setattr(app_context.reports, "paint", broken)
    pane.set_active(True)
    assert pane.current_preview is None
    assert pane.preview_widget.isHidden() and pane.pages.count() == 0
    assert "刷新预览" in pane.message.text()
    assert "REPORT_PREVIEW_PAINT_FAILED" in caplog.text
    assert "injected paint failure" in caplog.text
    assert pane.refresh_button.isEnabled()
    monkeypatch.setattr(app_context.reports, "paint", original)
    pane.refresh_button.click()
    assert pane.current_preview is not None
    assert not pane.preview_widget.isHidden() and pane.pages.count() > 0
    assert not pane.message.text()


def test_failed_load_does_not_keep_old_preview(qtbot: QtBot, app_context: AppContext) -> None:
    session, experiment_id = prepare(app_context)
    pane = MonitorReportPreview(app_context.reports)
    qtbot.addWidget(pane)
    pane.set_context(session, experiment_id, 1)
    pane.set_active(True)
    assert pane.current_preview is not None
    pane.set_active(False)
    app_context.auth.logout(session)
    pane.set_active(True)
    assert pane.current_preview is None
    assert pane.preview_widget.isHidden() and pane.pages.count() == 0
    assert pane.message.text()


def test_monitor_tab_opens_current_experiment_and_cancels_pending_refresh(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    _, experiment_id = prepare(app_context)
    sign_in(qapp, window, "admin")
    assert window.open_experiment(experiment_id)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    assert monitor.report_preview.current_preview is None
    monitor.tabs.set_current(TAB_REPORT)
    settle(qapp)
    preview = monitor.report_preview.current_preview
    assert preview is not None and preview.experiment_id == experiment_id
    monitor.report_preview.set_ui_language("en")
    monitor.tabs.set_current(TAB_DATA)
    settle(qapp)
    assert not monitor.report_preview._timer.isActive()
    monitor.tabs.set_current(TAB_REPORT)
    assert monitor.report_preview.current_preview is not None
    assert monitor.report_preview.current_preview.language is ReportLanguage.EN
    monitor.shutdown()
    assert monitor.report_preview.current_preview is None
    assert not monitor.report_preview._timer.isActive()


@pytest.mark.parametrize("size", [(1024, 700), (1280, 800)])
@pytest.mark.parametrize("lang", list(Language))
def test_monitor_report_layout_and_language(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    language: LanguageManager,
    size: tuple[int, int],
    lang: Language,
) -> None:
    _, experiment_id = prepare(app_context)
    use(window, language, lang, size)
    sign_in(qapp, window, "admin")
    assert window.open_experiment(experiment_id)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    monitor.tabs.set_current(TAB_REPORT)
    settle(qapp)
    assert monitor.report_preview.current_preview is not None
    assert monitor.report_preview.current_preview.language.value == lang.value
    assert overflow_report(window) == []
