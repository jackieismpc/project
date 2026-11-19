"""试剂溯源页：批次列表（过期高亮、剩余天数）、校验记录和扫码查询。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, ReagentCheckStatus, ReagentKind
from gelinspect.interfaces.storage import PageRequest, ReagentCheckRecord
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.dashboard import DashboardPage
from gelinspect.ui.pages.reagents_page import ReagentsPage
from simulation.reagents import SAMPLE_CODES
from tests.ui.conftest import FIXED_NOW, settle, sign_in
from tests.ui.test_new_experiment import audit_actions
from tests.ui.wizard_helpers import choose_template, click, open_wizard, scan


def test_dashboard_expired_batch_can_be_removed_after_confirmation(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_context.database.reagent_checks.add(
        ReagentCheckRecord(
            checked_at=FIXED_NOW,
            actor="experimenter",
            kind=ReagentKind.LAL_REAGENT,
            batch_no="LAL2501B",
            product_code="P",
            status=ReagentCheckStatus.EXPIRED,
            registered=True,
            expiry_date=FIXED_NOW.date() - timedelta(days=10),
        )
    )
    sign_in(qapp, window, "experimenter")
    dashboard = window.page("dashboard")
    assert isinstance(dashboard, DashboardPage)
    links = [dashboard.attention_box.itemAt(i) for i in range(dashboard.attention_box.count())]
    link = next(
        widget
        for item in links
        if item is not None
        if isinstance((widget := item.widget()), QAbstractButton)
        and "LAL2501B" in widget.accessibleName()
    )
    link.click()
    page = window.page("reagents")
    assert isinstance(page, ReagentsPage)
    selected = page.batch_table.view.selectionModel().selectedRows()
    assert (
        len(selected) == 1 and page.batch_table.model.rows[selected[0].row()]["batch"] == "LAL2501B"
    )
    assert not hasattr(page, "action_card")
    monkeypatch.setattr(
        "gelinspect.ui.pages.reagents_page.ConfirmDialog.ask", lambda *a, **kw: True
    )
    page.batch_table.view.clicked.emit(page.batch_table.model.index(selected[0].row(), 0))
    assert page.batch_table.row_count() == 1
    assert app_context.database.reagent_checks.query(PageRequest()).total == 1
    assert app_context.reagents.attention_dismissed(
        window.session, ReagentKind.LAL_REAGENT, "LAL2501B"
    )
    assert not app_context.reagents.dismiss_attention(
        window.session, ReagentKind.LAL_REAGENT, "LAL2501B", 1
    )
    assert len(audit_actions(app_context, AuditAction.REAGENT_ATTENTION_DISMISSED)) == 1
    assert window.navigate("dashboard")
    assert all(item.batch_no != "LAL2501B" for item in dashboard._attention)
    app_context.database.reagent_checks.add(
        ReagentCheckRecord(
            checked_at=FIXED_NOW,
            actor="experimenter",
            kind=ReagentKind.LAL_REAGENT,
            batch_no="LAL2501B",
            product_code="P",
            status=ReagentCheckStatus.EXPIRED,
            registered=True,
            expiry_date=FIXED_NOW.date() - timedelta(days=10),
        )
    )
    dashboard.refresh()
    assert any(item.batch_no == "LAL2501B" for item in dashboard._attention)


def open_reagents(app: QApplication, win: MainWindow, username: str) -> ReagentsPage:
    sign_in(app, win, username)
    assert win.navigate("reagents")
    page = win.page("reagents")
    assert isinstance(page, ReagentsPage)
    settle(app)
    return page


def test_empty_page_explains_where_batches_come_from(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_reagents(qapp, window, "experimenter")
    assert page.batch_table.is_empty_state_visible() and page.check_table.is_empty_state_visible()
    assert page.batch_table._empty_title.text() == "还没有录入的试剂批次"
    assert not hasattr(page, "gov_card")


def test_batches_and_checks_from_the_wizard_show_up_with_expiry_highlighting(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    wizard = open_wizard(qapp, window)
    choose_template(qapp, wizard, "限度试验")
    click(qapp, wizard.template_next)
    scan(qapp, wizard, ReagentKind.LAL_REAGENT, SAMPLE_CODES["lal_expired"])
    scan(qapp, wizard, ReagentKind.LAL_REAGENT, SAMPLE_CODES["lal_valid"])
    scan(qapp, wizard, ReagentKind.ENDOTOXIN, SAMPLE_CODES["endotoxin_valid"])
    wizard.reset()
    assert window.navigate("reagents")
    page = window.page("reagents")
    assert isinstance(page, ReagentsPage)
    rows = {r["batch"]: dict(r) for r in page.batch_table.model.rows}
    assert set(rows) == {"LAL2606A", "LAL2501B", "CSE2605A"}
    expired = rows["LAL2501B"]
    assert expired["status"] == "已过期" and expired["_tone"] == "danger"
    expiry = date.fromisoformat(str(expired["expiry"]))
    assert expired["days"] == (expiry - FIXED_NOW.date()).days
    assert expiry < FIXED_NOW.date()
    valid = rows["LAL2606A"]
    assert valid["status"] == "有效" and "_tone" not in valid
    assert isinstance(valid["days"], int) and valid["days"] > 30
    assert rows["CSE2605A"]["kind"] == "内毒素"
    # 校验记录：最新在前，过期那条标红并写明锁定
    checks = page.check_table.model.rows
    assert [c["batch"] for c in checks] == ["CSE2605A", "LAL2606A", "LAL2501B"]
    assert checks[2]["result"] == "已过期，锁定" and checks[2]["_tone"] == "danger"
    assert checks[0]["result"] == "通过" and checks[0]["actor"] == "experimenter"


def test_soon_to_expire_and_unregistered_batches_are_flagged(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    repo = app_context.database.reagent_checks

    def record(
        kind: ReagentKind,
        batch: str,
        status: ReagentCheckStatus,
        *,
        registered: bool,
        expiry: date | None = None,
    ) -> ReagentCheckRecord:
        return ReagentCheckRecord(
            checked_at=FIXED_NOW,
            actor="experimenter",
            kind=kind,
            batch_no=batch,
            product_code="P",
            status=status,
            registered=registered,
            expiry_date=expiry,
        )

    soon = FIXED_NOW.date() + timedelta(days=10)
    repo.add(
        record(ReagentKind.WATER, "SOON", ReagentCheckStatus.VALID, registered=True, expiry=soon)
    )
    repo.add(
        record(ReagentKind.ENDOTOXIN, "NOREG", ReagentCheckStatus.NOT_REGISTERED, registered=False)
    )
    page = open_reagents(qapp, window, "analyst")  # 所有角色都能看
    rows = {r["batch"]: r for r in page.batch_table.model.rows}
    assert rows["SOON"]["status"] == "即将到期" and rows["SOON"]["_tone"] == "warning"
    assert rows["SOON"]["days"] == 10
    assert rows["NOREG"]["status"] == "未备案" and rows["NOREG"]["_tone"] == "danger"


@pytest.mark.parametrize("username", ["experimenter", "analyst", "admin"])
def test_scan_query_checks_without_recording(
    qapp: QApplication, window: MainWindow, app_context: AppContext, username: str
) -> None:
    page = open_reagents(qapp, window, username)
    page.query_input.setFocus()
    QTest.keyClicks(page.query_input, SAMPLE_CODES["lal_expired"])
    QTest.keyClick(page.query_input, Qt.Key.Key_Return)  # 扫码枪回车
    settle(qapp)
    result = page.query_result
    assert result.batch.text() == "LAL2501B"
    assert (
        result.status.text() == "已于 2026-01-31 过期"
        and result.status.property("role") == "danger"
    )
    assert "已过期" in result.days.text() and "0.25" in result.extra.text()
    # 查询不写校验记录，也不出现在批次列表里
    assert app_context.database.reagent_checks.query(PageRequest()).total == 0
    assert page.batch_table.row_count() == 0
    assert audit_actions(app_context, AuditAction.REAGENT_CHECKED) == []
    page.query_input.setText(SAMPLE_CODES["water_valid"])
    page.run_query()
    assert result.status.text().startswith("已备案") and result.status.property("role") == "ok"
    page.query_input.setText("not-a-reagent-code")
    page.run_query()
    assert result.status.text() == "无法识别这个码，请重新扫码"
    page.query_input.setText("   ")
    page.run_query()  # 空输入什么都不做
    assert result.status.text() == "无法识别这个码，请重新扫码"
