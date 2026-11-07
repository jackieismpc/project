"""试验结论功能关闭（默认）时的界面：监控页、历史回看、报告中心、PDF 和 Excel 都找不到结论文字。

判定规则甲方还没有确认，默认只显示每支管的凝固状态（三态）、阴阳性和复核标注；
签署不要求有入库结论，但仍要求全部待复核已处理、试验已完成。打开开关时的行为见
``test_conclusion_ui.py`` 和 ``test_report_center.py``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.widgets.marks import WellState
from tests.services.reporting.pdftext import flat_text
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in
from tests.ui.test_report_center import auto_sign, click, finish_experiment, reports_of

FORBIDDEN = ("λc", "合格", "无效", "待定", "依据", "规则版本", "实测灵敏度", "结论")
"""关闭时界面、PDF、Excel 里都不应出现的文字。"""


def visible_texts(root: QWidget) -> list[str]:
    """``root`` 下全部可见控件上的文字（标签、按钮、徽标、提示）。"""
    texts: list[str] = []
    for widget in [root, *root.findChildren(QWidget)]:
        if widget is not root and not widget.isVisibleTo(root):
            continue
        for attribute in ("text", "toolTip", "placeholderText"):
            getter = getattr(widget, attribute, None)
            value = getter() if callable(getter) else None
            if isinstance(value, str) and value:
                texts.append(value)
    return texts


def assert_no_conclusion_text(root: QWidget) -> None:
    joined = "\n".join(visible_texts(root))
    found = [word for word in FORBIDDEN if word in joined]
    assert not found, f"界面上不应出现结论文字：{found}"


def test_monitor_shows_only_states_and_review_marks_when_off(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    window, context = fast_window, fast_context
    experiment_id = finish_experiment(qapp, window, context)  # 向导 → 跑完 → 复核 → 已完成
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    # 不计算、不入库结论，也没有结论审计
    assert stored.conclusion is None and stored.measured_sensitivity is None
    assert stored.completed_at is not None
    recorded = context.database.audit.query(
        AuditQuery(action=AuditAction.CONCLUSION_RECORDED), PageRequest()
    )
    assert recorded.total == 0

    sign_in(qapp, window, "analyst")
    window.open_experiment(experiment_id)  # 历史回看
    settle(qapp)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    view = monitor.view_model
    assert view is not None and view.conclusion is None and not view.conclusions_enabled
    assert view.measured_sensitivity is None
    assert not monitor.conclusion_card.isVisibleTo(monitor)
    assert not monitor._fact_values["lambda_c"].isVisibleTo(monitor)  # noqa: SLF001
    assert not monitor._fact_labels["lambda_c"].isVisibleTo(monitor)  # noqa: SLF001
    assert_no_conclusion_text(monitor)
    # 每管的凝固状态、阴阳性和复核标注都在
    rows = monitor.table.model.rows
    assert len(rows) == 17
    cells = {v for row in rows for v in row.values() if isinstance(v, WellState)}
    assert {WellState.POSITIVE, WellState.NEGATIVE} <= cells

    # 工作台和历史记录也没有结论文字
    for route in ("dashboard", "history"):
        assert window.navigate(route)
        settle(qapp)
        page = window.page(route)
        assert page is not None
        assert_no_conclusion_text(page)


def test_monitor_while_running_has_no_conclusion_card_when_off(
    qapp: QApplication, live_window: MainWindow
) -> None:
    from tests.ui.monitor_helpers import start_sensitivity

    monitor = start_sensitivity(qapp, live_window)
    settle(qapp)
    view = monitor.view_model
    assert view is not None and view.conclusion is None
    assert not monitor.conclusion_card.isVisibleTo(monitor)
    assert_no_conclusion_text(monitor)


def test_signing_needs_no_conclusion_and_pdf_and_excel_have_none_when_off(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window, context = fast_window, fast_context
    experiment_id = finish_experiment(qapp, window, context)

    sign_in(qapp, window, "analyst")
    assert window.navigate("reports")
    page = reports_of(window)
    preview = page.current_preview
    assert preview is not None and preview.can_sign and not preview.conclusions_enabled
    assert page.sign_button.isEnabled()
    assert "可以签署" in page.status_text.text()
    assert_no_conclusion_text(page)

    # 签署：不要求有入库结论
    auto_sign(monkeypatch, DEMO_PASSWORDS["analyst"])
    click(qapp, page.sign_button)
    stored = context.database.experiments.get(experiment_id)
    assert stored is not None and stored.state is ExperimentState.SIGNED
    assert stored.conclusion is None
    record = page.current_record
    assert record is not None and record.signed
    assert page.status_text.text() == "已签署"
    assert_no_conclusion_text(page)

    # PDF 里没有结论文字，结果表和复核标注还在
    pdf = context.reports.resolve(record).read_bytes()
    text = flat_text(pdf)
    for word in FORBIDDEN:
        assert word not in text, word
    assert "试管编号" in text and "已复核" in text

    # Excel：没有结论列和结论依据表
    target = tmp_path / "导出.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    page.excel_button.click()
    assert target.is_file()
    book = load_workbook(target)
    assert book.sheetnames == ["试验", "单管结果"]
    for sheet in book:
        for row in sheet.iter_rows(values_only=True):
            for value in row:
                if isinstance(value, str):
                    for word in FORBIDDEN:
                        assert word not in value, (sheet.title, value)
    tubes = book["单管结果"]
    header = [c.value for c in tubes[1]]
    assert "最终判读" in header and "复核人" in header
