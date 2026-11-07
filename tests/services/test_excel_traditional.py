"""Excel 导出的表头和枚举值随界面语言：繁体中文下是台湾用语，字符都在内置字体里。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication

from gelinspect.interfaces.storage import ExperimentQuery
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.export_service import ExportService
from gelinspect.ui.export_labels import export_labels
from gelinspect.ui.i18n import Language, LanguageManager, MemoryLanguageSettings
from gelinspect.ui.theme.fonts import FONT_DIR
from tests.services.reporting.conftest import make_completed

from .conftest import NOW, Stack


@pytest.fixture
def traditional(qapp: QApplication) -> Iterator[LanguageManager]:
    manager = LanguageManager(qapp, settings=MemoryLanguageSettings())
    assert manager.set_language(Language.ZH_TW, persist=False)
    yield manager
    manager.set_language(Language.ZH_CN, persist=False)


def test_excel_headers_and_values_are_traditional_chinese(
    stack: Stack, traditional: LanguageManager, tmp_path: Path
) -> None:
    make_completed(stack)
    exports = ExportService(stack.auth, stack.database, clock=lambda: NOW, conclusions_enabled=True)
    admin = stack.login("admin")
    labels = export_labels()
    exports.export_results(admin, ["EXP-SAMPLE-0001"], tmp_path / "r.xlsx", labels)
    exports.export_history(admin, ExperimentQuery(), tmp_path / "h.xlsx", labels)
    exports.export_audit(admin, AuditFilter(), tmp_path / "a.xlsx", labels)

    results = load_workbook(tmp_path / "r.xlsx")
    assert results.sheetnames[:2] == ["試驗", "單管結果"]
    header = [c.value for c in results["單管結果"][1]]
    for expected in ("管號", "樣品名稱", "稀釋倍數", "複核狀態", "最終判讀", "面積變化率 ΔA（%）"):
        assert expected in header
    history = [c.value for c in load_workbook(tmp_path / "h.xlsx")["歷史清單"][1]]
    assert history[0] == "試驗編號" and "簽署時間" in history
    audit = load_workbook(tmp_path / "a.xlsx")
    assert audit.sheetnames == ["稽核軌跡"]
    assert [c.value for c in audit["稽核軌跡"][1]][:3] == ["序號", "時間", "操作者"]

    texts: set[str] = set()
    for name in ("r.xlsx", "h.xlsx", "a.xlsx"):
        for sheet in load_workbook(tmp_path / name):
            texts.update(
                cell.value
                for row in sheet.iter_rows(max_row=1)
                for cell in row
                if isinstance(cell.value, str)
            )
    font = QRawFont(str(FONT_DIR / "NotoSansSC-Regular.ttf"), 12)
    assert not {c for t in texts for c in t if not font.supportsCharacter(ord(c))}
    column = header.index("最終判讀")
    values = {row[column].value for row in results["單管結果"].iter_rows(min_row=2)}
    assert values and values <= {"陽性", "陰性", "待複核"}
