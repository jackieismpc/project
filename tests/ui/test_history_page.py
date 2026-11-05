"""历史记录页：筛选、分页、打开回看、选中导出 Excel、空状态、权限。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication, QFileDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.history_page import HistoryPage
from tests.ui.admin_helpers import column_values, seed_experiments, select_rows
from tests.ui.conftest import settle, sign_in, wait_for


def open_history(
    app: QApplication, window: MainWindow, context: AppContext, who: str = "admin", count: int = 12
) -> HistoryPage:
    seed_experiments(context, count)
    sign_in(app, window, who)
    assert window.navigate("history")
    page = window.page("history")
    assert isinstance(page, HistoryPage)
    settle(app)
    return page


def ids(page: HistoryPage) -> list[str]:
    return [str(v) for v in column_values(page.table, "id")]


def test_lists_newest_first_and_pages(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gelinspect.ui.pages.history_page.PAGE_SIZE", 5)
    page = open_history(qapp, window, app_context)
    assert page.total == 12 and page.pager.pages == 3 and page.page_index == 0
    assert ids(page) == [f"EXP-H-{n:03d}" for n in (11, 10, 9, 8, 7)]
    assert not page.pager.prev_button.isEnabled() and page.pager.next_button.isEnabled()
    assert "第 1/3 页" in page.pager.summary.text() and "共 12 条" in page.pager.summary.text()
    page.pager.next_button.click()
    assert page.page_index == 1 and ids(page) == [f"EXP-H-{n:03d}" for n in (6, 5, 4, 3, 2)]
    page.pager.next_button.click()
    assert ids(page) == ["EXP-H-001", "EXP-H-000"] and not page.pager.next_button.isEnabled()
    page.pager.prev_button.click()
    assert page.page_index == 1


def test_each_filter_narrows_the_list(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_history(qapp, window, app_context)
    assert page.total == 12 and not page.filters_active()

    page.type_box.setCurrentIndex(page.type_box.findData(ExperimentType.LIMIT.value))
    page.search()
    assert set(ids(page)) == {"EXP-H-004", "EXP-H-009"}  # 编号 n 的类型是 TYPES[n % 5]
    page.reset_filters()

    page.state_box.setCurrentIndex(page.state_box.findData(ExperimentState.STOPPED.value))
    page.search()
    assert page.total == 4 and set(column_values(page.table, "state")) == {"已停止"}
    page.reset_filters()

    page.operator_box.setCurrentIndex(page.operator_box.findData("admin"))
    page.search()
    assert page.total == 6 and set(column_values(page.table, "operator")) == {"admin"}
    page.reset_filters()

    page.batch_edit.setText("LAL-1")  # 试剂批号 n % 3 == 1
    page.search()
    assert {i[-3:] for i in ids(page)} == {"001", "004", "007", "010"}
    page.batch_edit.setText("SB-3")  # 样品批号 n % 4 == 3，同一个输入框
    page.search()
    assert {i[-3:] for i in ids(page)} == {"003", "007", "011"}
    page.reset_filters()

    page.start_edit.set_value(date(2026, 10, 4))  # 10-01 + n 天
    page.end_edit.set_value(date(2026, 10, 6))  # 结束日期含当天
    page.search()
    assert {i[-3:] for i in ids(page)} == {"003", "004", "005"}

    page.type_box.setCurrentIndex(page.type_box.findData(ExperimentType.RETEST.value))
    page.search()  # 组合：类型 + 日期，RETEST 是 n % 5 == 2，日期内只有 n=3,4,5 → 无
    assert page.total == 0
    page.reset_filters()
    assert page.total == 12 and not page.filters_active()


def test_no_result_shows_an_empty_state_that_says_why(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_history(qapp, window, app_context)
    page.batch_edit.setText("没有这个批号")
    page.search()
    assert page.total == 0 and page.table.is_empty_state_visible()
    assert page.empty_title() == "没有符合条件的试验"
    assert not page.pager.next_button.isEnabled() and "共 0 条" in page.pager.summary.text()
    page.reset_filters()
    assert not page.table.is_empty_state_visible()


def test_empty_database_shows_the_first_run_hint(qapp: QApplication, window: MainWindow) -> None:
    sign_in(qapp, window, "admin")
    window.navigate("history")
    page = window.page("history")
    assert isinstance(page, HistoryPage) and page.table.is_empty_state_visible()
    assert page.empty_title() == "还没有历史试验"
    assert not page.export_list_button.isEnabled()


def test_open_goes_to_the_experiment_in_read_only_review(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_history(qapp, window, app_context)
    seen: list[str] = []
    page.experimentRequested.connect(seen.append)
    select_rows(page.table, [2])
    page.open_button.click()
    assert seen == ["EXP-H-009"]  # 第 3 行（最新在前）
    assert window.current_route == "monitor"
    monitor = window.page("monitor")
    assert monitor is not None and monitor.experiment_id == "EXP-H-009"  # type: ignore[attr-defined]
    window.navigate("history")
    page.table.rowActivated.emit(0)
    assert seen[-1] == "EXP-H-011"


def export_with(
    app: QApplication, page: HistoryPage, monkeypatch: pytest.MonkeyPatch, target: Path, call: str
) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    getattr(page, call)()
    assert wait_for(app, lambda: not page.export_busy, 30)


def test_export_selected_writes_only_the_selected_experiments(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = open_history(qapp, window, app_context)
    assert not page.export_selected_button.isEnabled()  # 没选中
    select_rows(page.table, [0, 3])
    assert page.export_selected_button.isEnabled()
    target = tmp_path / "选中.xlsx"
    export_with(qapp, page, monkeypatch, target, "request_export_selected")
    summary = load_workbook(target)["试验"]
    exported = {row[0] for row in summary.iter_rows(min_row=2, values_only=True)}
    assert exported == {"EXP-H-011", "EXP-H-008"}
    exported_events = app_context.database.audit.query(
        AuditQuery(action=AuditAction.DATA_EXPORTED), PageRequest()
    )
    lines = [r.event.detail for r in exported_events.items]
    assert any("2 个试验" in line for line in lines)


def test_export_list_covers_every_page_of_the_current_filter(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("gelinspect.ui.pages.history_page.PAGE_SIZE", 3)
    page = open_history(qapp, window, app_context)
    page.state_box.setCurrentIndex(page.state_box.findData(ExperimentState.COMPLETED.value))
    page.search()
    assert page.total == 4 and len(page.items()) == 3  # 当前页只有 3 条
    target = tmp_path / "list.xlsx"
    export_with(qapp, page, monkeypatch, target, "request_export_list")
    rows = list(load_workbook(target)["历史列表"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 4 and {r[2] for r in rows} == {
        "已完成"
    }  # 界面按当前语言导出；全部 4 条，不只当前页


def test_cancelling_the_save_dialog_exports_nothing(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_history(qapp, window, app_context)
    select_rows(page.table, [0])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: ("", ""))
    page.request_export_selected()
    assert not page.export_busy


@pytest.mark.parametrize(
    ("who", "can_export"), [("experimenter", False), ("analyst", True), ("admin", True)]
)
def test_export_follows_the_role(
    qapp: QApplication, window: MainWindow, app_context: AppContext, who: str, can_export: bool
) -> None:
    page = open_history(qapp, window, app_context, who)
    select_rows(page.table, [0])
    assert page.export_list_button.isEnabled() is can_export
    assert page.export_selected_button.isEnabled() is can_export
    if not can_export:
        assert "无此权限" in page.export_list_button.toolTip()
    assert page.open_button.isEnabled()  # 所有角色都能打开回看
