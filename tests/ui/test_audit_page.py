"""审计追踪页：筛选和分页、后台查询和校验（界面不冻结、有进度）、篡改能被检出、导出、权限。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

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
    assert {"分析员"} & set(column_values(page.table, "role"))  # “权限”列是操作时的角色


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


def write_audits(context: AppContext, count: int, tag: str = "并发") -> None:
    """模拟别处（例如有人登录）在翻页期间写入审计。"""
    for i in range(count):
        context.database.audit.append(
            AuditEvent(
                occurred_at=BASE + timedelta(days=30, minutes=i),
                actor="carol",
                actor_role=Role.EXPERIMENTER,
                action=AuditAction.LOGIN,
                detail=f"{tag}写入 {i}",
            )
        )


def seqs_of(page: AuditPage) -> list[int]:
    return [int(str(v)) for v in column_values(page.table, "seq")]


def go_to(app: QApplication, page: AuditPage, target: int) -> None:
    button = page.pager.next_button if target > page.page_index else page.pager.prev_button
    button.click()
    assert wait_for(app, lambda: page.page_index == target and not page.loading)


def test_pagination_does_not_shift_when_audits_are_written_while_paging(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    """翻页期间有新审计写入：各页不重不漏、总数不变；点“查询”才看到新记录。"""
    page = open_audit(qapp, window, app_context)
    total = page.total
    first = seqs_of(page)
    write_audits(app_context, 3)  # 第一页已经显示，别人又写了 3 条

    go_to(qapp, page, 1)
    second = seqs_of(page)
    assert page.total == total
    assert not set(first) & set(second)
    assert min(first) - 1 == max(second)  # 紧挨着，不漏
    write_audits(app_context, 2)  # 翻页中间再写
    go_to(qapp, page, 2)
    third = seqs_of(page)
    assert page.total == total and len(third) == total - 100
    assert min(second) - 1 == max(third) and not set(second) & set(third)

    # 翻回去：第一页还是原来那一页，总数也没变（新写入的没有顶进来）
    go_to(qapp, page, 1)
    assert seqs_of(page) == second and page.total == total
    go_to(qapp, page, 0)
    assert seqs_of(page) == first and page.total == total
    assert not page.pager.prev_button.isEnabled()

    # 点“查询”才开始新的一次，看到新记录
    run_query(qapp, page)
    assert page.page_index == 0 and page.total >= total + 5
    assert max(seqs_of(page)) > max(first)
    assert any("并发写入" in r.event.detail for r in page.records)


def test_total_and_rows_agree_when_a_record_lands_between_count_and_select(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """总数和记录是两条语句：中间被写入一条，也不能出现“总数 N、实际 N+1 条”的错位。"""
    import gelinspect.infrastructure.db.repositories.audit as audit_module
    from gelinspect.infrastructure.db.repositories._common import count_where

    page = open_audit(qapp, window, app_context)
    real_count = count_where
    armed = {"on": True}

    def count_then_write(session: Session, entity: Any, filters: list[ColumnElement[bool]]) -> int:
        total = real_count(session, entity, filters)
        if armed["on"]:
            armed["on"] = False
            write_audits(app_context, 1, "夹缝")
        return total

    monkeypatch.setattr(audit_module, "count_where", count_then_write)
    page.search()
    assert wait_for(qapp, lambda: not page.loading and not armed["on"])
    monkeypatch.setattr(audit_module, "count_where", real_count)
    total = page.total
    collected = seqs_of(page)
    for target in range(1, page.pager.pages):
        go_to(qapp, page, target)
        collected += seqs_of(page)
    assert len(collected) == len(set(collected)) == total
    assert collected == sorted(collected, reverse=True)
    assert not any("夹缝" in r.event.detail for r in page.records)


def test_export_uses_the_range_of_the_current_query(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    tmp_path: Path,
) -> None:
    page = open_audit(qapp, window, app_context)
    total = page.total
    write_audits(app_context, 4)
    target = tmp_path / "audit.xlsx"
    page.export_to(target)
    assert wait_for(qapp, lambda: not page.exporting and target.is_file())
    sheet = load_workbook(target).active
    assert sheet is not None
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == total  # 导出的就是这次查询看到的范围，后来写入的 4 条不在里面


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
