"""监控、历史和报告不显示固定实验级规则结论，保留逐管结果与签署流程。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from gelinspect.app_context import AppContext
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.widgets.marks import WellState
from tests.ui.conftest import sign_in
from tests.ui.test_report_center import finish_experiment, reports_of

_FIXED_RULE_TEXT = (
    "sensitivity-v1",
    "规则版本",
    "规则来源",
    "中国药典通则 1143",
    "未满足的前提",
    "λc",
    "待定",
    "合格",
    "不合格",
    "管位与规则要求的布局不符",
)


def _visible_text(root: QWidget) -> str:
    texts: list[str] = []
    for widget in (root, *root.findChildren(QWidget)):
        if widget is not root and not widget.isVisibleTo(root):
            continue
        for attribute in ("text", "toolTip", "placeholderText"):
            getter = getattr(widget, attribute, None)
            value = getter() if callable(getter) else None
            if isinstance(value, str) and value:
                texts.append(value)
    return "\n".join(texts)


def _assert_no_fixed_rule_text(root: QWidget) -> None:
    text = _visible_text(root)
    found = [value for value in _FIXED_RULE_TEXT if value in text]
    assert not found, f"界面遗留了固定实验级规则结论：{found}"


def test_monitor_history_and_reports_keep_per_tube_review_and_signing_without_rule_summary(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
) -> None:
    experiment_id = finish_experiment(qapp, fast_window, fast_context)
    sign_in(qapp, fast_window, "analyst")

    assert fast_window.open_experiment(experiment_id)
    monitor = fast_window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    assert not hasattr(monitor, "conclusion_card")
    assert "lambda_c" not in monitor._fact_values  # noqa: SLF001
    rows = monitor.table.model.rows
    assert len(rows) == 18
    assert any(isinstance(value, WellState) for row in rows for value in row.values())
    assert any(column.key == "result" for column in monitor.table.model.columns)
    _assert_no_fixed_rule_text(monitor)

    assert fast_window.navigate("history")
    history = fast_window.page("history")
    assert history is not None
    _assert_no_fixed_rule_text(history)

    assert fast_window.navigate("reports")
    reports = reports_of(fast_window)
    assert reports.open_experiment(experiment_id)
    preview = reports.current_preview
    assert preview is not None and preview.can_sign
    assert reports.sign_button.isEnabled()
    assert reports.status_text.text() == "可以签署：实验已完成"
    _assert_no_fixed_rule_text(reports)
