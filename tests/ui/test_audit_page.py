"""审计追踪页：筛选和分页、后台查询和校验（界面不冻结、有进度）、篡改能被检出、导出、权限。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.interfaces.storage import AuditQuery, ChainFailure, PageRequest
from gelinspect.services.audit_service import AuditService
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.audit_page import AuditPage
from tests.ui.admin_helpers import BASE, column_values, seed_audit
from tests.ui.conftest import settle, sign_in, wait_for


def open_audit(
    app: QApplication, window: MainWindow, context: AppContext, who: str = "admin", count: int = 130
) -> AuditPage:
    seed_audit(context, count)
    sign_in(app, window, who)
    assert window.navigate("audit")
    page = window.page("audit")
    assert isinstance(page, AuditPage)
    assert wait_for(app, lambda: not page.loading and page.total > 0)
    return page


def run_query(app: QApplication, page: AuditPage) -> None:
    page.search()
    assert page.loading  # 查询在后台，调用立即返回
    assert wait_for(app, lambda: not page.loading)


def test_opening_the_page_queries_in_the_background_and_shows_the_columns(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    seed_audit(app_context, 130)
    sign_in(qapp, window, "analyst")
    window.navigate("audit")
    page = window.page("audit")
    assert isinstance(page, AuditPage)
    assert page.loading  # 还没有结果：查询在后台线程里
    assert wait_for(qapp, lambda: not page.loading)
    titles = [c.title for c in page.table.model.columns]
    assert titles == ["序号", "操作者", "时间", "权限", "操作记录"]
    seqs = [int(str(v)) for v in column_values(page.table, "seq")]
    assert len(seqs) == 50 and seqs == sorted(seqs, reverse=True)  # 每页 50，最新在前
    assert page.total >= 130
    assert {"分析员"} & set(column_values(page.table, "role"))  # 「权限」列是操作时的角色


def test_pagination(qapp: QApplication, window: MainWindow, app_context: AppContext) -> None:
    page = open_audit(qapp, window, app_context)
    total = page.total
    first = {int(str(v)) for v in column_values(page.table, "seq")}
    assert not page.pager.prev_button.isEnabled() and page.pager.next_button.isEnabled()
    page.pager.next_button.click()
    assert wait_for(qapp, lambda: page.page_index == 1 and not page.loading)
    second = {int(str(v)) for v in column_values(page.table, "seq")}
    assert not first & second and max(second) < min(first)
    page.pager.next_button.click()
    assert wait_for(qapp, lambda: page.page_index == 2 and not page.loading)
    assert len(page.records) == total - 100 and not page.pager.next_button.isEnabled()
    page.pager.prev_button.click()
    assert wait_for(qapp, lambda: page.page_index == 1 and not page.loading)


def test_filters(qapp: QApplication, window: MainWindow, app_context: AppContext) -> None:
    page = open_audit(qapp, window, app_context)
    page.actor_box.setEditText("bob")
    run_query(qapp, page)
    assert page.total == 44 and set(column_values(page.table, "actor")) == {"bob"}
    page.reset_filters()
    assert wait_for(qapp, lambda: not page.loading)

    page.keyword_edit.setText("lal-5")
    run_query(qapp, page)
    assert page.total == len([i for i in range(130) if i % 7 == 5])
    page.reset_filters()
    assert wait_for(qapp, lambda: not page.loading)

    page.start_edit.set_value(date(2026, 10, 2))  # 造的记录从 2026-10-01 08:00Z 起每小时一条
    page.end_edit.set_value(date(2026, 10, 2))
    run_query(qapp, page)
    in_day = [
        r for r in page.records if "第" in r.event.detail and r.event.actor in ("alice", "bob")
    ]
    assert page.total == len(in_day) and 18 <= page.total <= 24  # 一天（本地）内的小时数
    page.actor_box.setEditText("nobody")
    run_query(qapp, page)
    assert page.total == 0 and page.table.is_empty_state_visible()
    assert page.table.empty_title_text() == "没有符合条件的审计记录"

    page.reset_filters()
    assert wait_for(qapp, lambda: not page.loading and page.total >= 130)


def test_an_invalid_range_is_explained(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_audit(qapp, window, app_context)
    page.start_edit.set_value(date(2026, 10, 9))
    page.end_edit.set_value(date(2026, 10, 3))
    run_query(qapp, page)
    assert page.total == 0 and page.table.is_empty_state_visible()
    assert page.table.empty_title_text() == "起始时间必须早于结束时间"


def test_viewing_writes_an_audit_record_but_paging_does_not(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_audit(qapp, window, app_context)

    def views() -> int:
        return app_context.database.audit.query(
            AuditQuery(action=AuditAction.AUDIT_VIEWED), PageRequest()
        ).total

    before = views()
    page.pager.next_button.click()
    assert wait_for(qapp, lambda: page.page_index == 1 and not page.loading)
    assert views() == before  # 翻页不写
    run_query(qapp, page)
    assert views() == before + 1  # 新开一次查询才写


def test_verify_is_in_the_background_shows_progress_and_the_ui_stays_responsive(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_audit(qapp, window, app_context)
    gate, reached_half = threading.Event(), threading.Event()
    real = AuditService.verify_chain

    def slow(self: AuditService, session: object, progress: object = None):  # type: ignore[no-untyped-def]
        if callable(progress):
            progress(50, 100)
            reached_half.set()
        assert gate.wait(10)
        return real(self, session, progress)  # type: ignore[arg-type]

    monkeypatch.setattr(AuditService, "verify_chain", slow)
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    page.verify_button.click()
    assert (
        page.verifying
        and page.progress_card.isVisibleTo(page)
        and not page.verify_button.isEnabled()
    )
    assert wait_for(
        qapp, lambda: reached_half.is_set() and abs(page.progress_bar.value - 0.5) < 1e-6
    )
    before = len(ticks)
    assert wait_for(qapp, lambda: len(ticks) > before + 3, 2)  # 校验卡着时，界面事件仍在处理
    assert "50 / 100" in page.progress_label.text()
    gate.set()
    assert wait_for(qapp, lambda: not page.verifying, 20)
    timer.stop()
    assert not page.progress_card.isVisibleTo(page) and page.verify_button.isEnabled()
    result = page.last_verification
    assert result is not None and result.ok
    assert (
        "校验通过" in page.banner.title.text() and f"{result.checked}" in page.banner.detail.text()
    )


def test_verify_reports_the_first_tampered_record_and_marks_the_row(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_audit(qapp, window, app_context)
    victim = page.records[3].seq
    path = app_context.database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audit_events SET detail = '被篡改' WHERE seq = ?", (victim,))
    run_query(qapp, page)  # 刷新列表，让被改的这一行在当前页里
    page.verify_button.click()
    assert wait_for(qapp, lambda: not page.verifying, 20)
    result = page.last_verification
    assert result is not None and not result.ok
    assert result.first_bad_seq == victim and result.failure is ChainFailure.HASH_MISMATCH
    assert "校验未通过" in page.banner.title.text()
    assert f"序号 {victim}" in page.banner.detail.text()
    assert "内容可能被改动" in page.banner.detail.text()
    flagged = [r for r in page.table.model.rows if r.get("_tone") == "danger"]
    assert [int(str(r["seq"])) for r in flagged] == [victim]
    line = (
        app_context.database.audit.query(
            AuditQuery(action=AuditAction.AUDIT_VERIFIED), PageRequest()
        )
        .items[0]
        .event.detail
    )
    assert f"序号 {victim}" in line


def test_a_deleted_record_is_reported_as_missing(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_audit(qapp, window, app_context)
    path = app_context.database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM audit_events WHERE seq = 40")
    page.verify_button.click()
    assert wait_for(qapp, lambda: not page.verifying, 20)
    assert page.last_verification is not None and page.last_verification.first_bad_seq == 40
    assert "序号 40" in page.banner.detail.text() and "删除" in page.banner.detail.text()


def test_export_writes_every_row_of_the_current_filter(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = open_audit(qapp, window, app_context)
    page.actor_box.setEditText("bob")
    run_query(qapp, page)
    target = tmp_path / "审计.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    page.request_export()
    assert page.exporting
    assert wait_for(qapp, lambda: not page.exporting, 30)
    rows = list(load_workbook(target)["审计追踪"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 44 and {r[2] for r in rows} == {"bob"}  # 不只当前页
    assert rows[0][0] > rows[-1][0]


@pytest.mark.parametrize("who", ["admin", "analyst"])
def test_admin_and_analyst_can_use_the_page(
    qapp: QApplication, window: MainWindow, app_context: AppContext, who: str
) -> None:
    page = open_audit(qapp, window, app_context, who)
    assert page.export_button.isEnabled() and page.verify_button.isEnabled()


def test_experimenter_cannot_open_it_and_the_service_refuses_too(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    assert not window.navigate("audit") and window.page("audit") is None
    page = AuditPage(window.services)
    page.set_session(window.session)
    assert wait_for(qapp, lambda: not page.loading)
    assert page.total == 0 and page.table.is_empty_state_visible()  # 服务层拒绝：页面没有数据
    assert not page.export_button.isEnabled()
    settle(qapp)
    page.wait_idle()


def test_paging_is_stable_while_new_records_arrive(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    """翻页期间有新记录写入（登录、别人的操作），后面的页不会把同一条记录再显示一次。"""
    page = open_audit(qapp, window, app_context)
    first = {int(str(v)) for v in column_values(page.table, "seq")}
    for n in range(7):
        app_context.database.audit.append(
            AuditEvent(
                BASE + timedelta(days=1, minutes=n),
                "carol",
                Role.ANALYST,
                AuditAction.LOGIN,
                "新写入",
            )
        )
    page.pager.next_button.click()
    assert wait_for(qapp, lambda: page.page_index == 1 and not page.loading)
    second = {int(str(v)) for v in column_values(page.table, "seq")}
    assert not first & second and max(second) < min(first)
