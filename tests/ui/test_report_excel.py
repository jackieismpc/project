"""报告中心的「导出 Excel 数据」：真实试验、真实向导，按钮接到导出服务。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook
from PySide6.QtWidgets import QApplication, QFileDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from tests.ui.conftest import sign_in
from tests.ui.test_report_center import finish_experiment, reports_of


def test_excel_button_exports_the_current_experiment_with_conclusion_and_review(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    experiment_id = finish_experiment(qapp, fast_window, fast_context)
    sign_in(qapp, fast_window, "analyst")
    assert fast_window.navigate("reports")
    page = reports_of(fast_window)
    assert page.excel_button.text() == "导出 Excel 数据…" and page.excel_button.isEnabled()
    target = tmp_path / "导出.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    page.excel_button.click()
    assert target.is_file()
    book = load_workbook(target)
    assert book.sheetnames == ["试验", "单管结果", "结论依据"]
    summary = [c.value for c in book["试验"][2]]
    assert summary[0] == experiment_id and summary[16] is not None  # 结论列有值
    tubes = book["单管结果"]
    header = [c.value for c in tubes[1]]
    reviewed = [
        r for r in tubes.iter_rows(min_row=2, values_only=True) if r[header.index("复核人")]
    ]
    assert reviewed and all(r[header.index("复核备注")] == "人工判阳性" for r in reviewed)
    assert tubes.max_row == 18  # 17 管
    events = fast_context.database.audit.query(
        AuditQuery(action=AuditAction.DATA_EXPORTED), PageRequest()
    )
    assert any("导出试验结果到 Excel" in r.event.detail for r in events.items)


def test_the_experimenter_cannot_export_excel(
    qapp: QApplication, fast_window: MainWindow, fast_context: AppContext
) -> None:
    finish_experiment(qapp, fast_window, fast_context)
    sign_in(qapp, fast_window, "experimenter")
    fast_window.navigate("reports")
    page = reports_of(fast_window)
    assert not page.excel_button.isEnabled() and "无此权限" in page.excel_button.toolTip()
