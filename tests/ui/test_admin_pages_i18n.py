"""数据管理页的中英文：英文下没有残留的中文界面文字；导出的列标题与服务层登记的一致。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from gelinspect.app_context import AppContext
from gelinspect.interfaces.storage import AuditRecord
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.errors import ServiceErrorCode
from gelinspect.services.excel_export import EXPORT_TEXTS, write_audit
from gelinspect.services.user_settings import SettingsProblemCode
from gelinspect.ui import export_labels as export_labels_module
from gelinspect.ui import labels
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.audit_page import AuditPage
from gelinspect.ui.pages.history_page import HistoryPage
from gelinspect.ui.pages.settings_page import SettingsPage
from tests.infrastructure.db.conftest import make_event
from tests.ui.admin_helpers import seed_experiments
from tests.ui.conftest import settle, sign_in, wait_for

CJK = re.compile(r"[一-鿿]")


def visible_texts(root: QWidget) -> list[str]:
    """页面里用户看得到的界面文字：标签、按钮、占位文字、下拉项、表头。"""
    texts: list[str] = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisibleTo(root):
            continue
        if isinstance(widget, QLabel | QAbstractButton):
            texts.append(widget.text())
        elif isinstance(widget, QLineEdit):
            texts.append(widget.placeholderText())
        elif isinstance(widget, QComboBox):
            texts += [widget.itemText(i) for i in range(widget.count())]
    return [t for t in texts if t]


def chinese_left(root: QWidget, allowed: tuple[str, ...] = ()) -> list[str]:
    return [t for t in visible_texts(root) if CJK.search(t) and not any(a in t for a in allowed)]


def test_the_four_pages_are_fully_english_in_english(
    qapp: QApplication, window: MainWindow, language: LanguageManager, app_context: AppContext
) -> None:
    seed_experiments(app_context, 4)
    language.set_language(Language.EN, persist=False)
    sign_in(qapp, window, "admin")
    for key in ("history", "audit", "users", "settings"):
        assert window.navigate(key), key
        page = window.page(key)
        assert page is not None
        if isinstance(page, AuditPage):
            assert wait_for(qapp, lambda p=page: not p.loading)  # type: ignore[misc]
        if isinstance(page, SettingsPage):
            assert wait_for(qapp, lambda p=page: not p.previewing)  # type: ignore[misc]
        settle(qapp)
        # 审计的操作记录是库里存的原文（数据），不算界面文字；数据在表格里，不在这里的标签里
        assert chinese_left(page) == [], key
    history = window.page("history")
    assert isinstance(history, HistoryPage)
    assert not history.table.is_empty_state_visible()  # 有数据，没有空状态
    history.batch_edit.setText("nothing")
    history.search()
    assert history.table.empty_title_text() == "No experiments match"
    assert chinese_left(history) == []


def test_readonly_and_error_states_are_english_too(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    language.set_language(Language.EN, persist=False)
    sign_in(qapp, window, "analyst")
    window.navigate("settings")
    page = window.page("settings")
    assert isinstance(page, SettingsPage)
    assert wait_for(qapp, lambda: not page.previewing)
    assert chinese_left(page) == []
    assert page.readonly_badge.text() == "Read-only"
    window.navigate("audit")
    audit = window.page("audit")
    assert isinstance(audit, AuditPage)
    assert wait_for(qapp, lambda: not audit.loading)
    audit.verify_button.click()
    assert wait_for(qapp, lambda: not audit.verifying, 20)
    assert audit.banner.title.text() == "Verification passed: the hash chain is intact"
    assert chinese_left(audit) == []
    audit.start_edit.set_value(date(2026, 5, 2))
    audit.end_edit.set_value(date(2026, 5, 1))
    audit.search()
    assert wait_for(qapp, lambda: not audit.loading)
    assert audit.table.empty_title_text() == "The start time must be earlier than the end time"
    audit.wait_idle()


def test_every_service_error_and_settings_problem_has_a_message_and_an_english_text(
    qapp: QApplication, language: LanguageManager
) -> None:
    assert set(ServiceErrorCode) <= set(labels._SERVICE_MESSAGES)  # noqa: SLF001
    assert set(SettingsProblemCode) <= set(labels._PROBLEM_TEXTS)  # noqa: SLF001
    language.set_language(Language.EN, persist=False)
    for source in (*labels._SERVICE_MESSAGES.values(), *labels._PROBLEM_TEXTS.values()):  # noqa: SLF001
        context = (
            "ServiceError" if source in labels._SERVICE_MESSAGES.values() else "SettingsProblem"
        )  # noqa: SLF001
        assert not CJK.search(QApplication.translate(context, source)), source


def test_export_texts_registered_for_translation_match_the_service(
    qapp: QApplication, language: LanguageManager
) -> None:
    assert set(export_labels_module._SOURCES) == set(EXPORT_TEXTS)  # noqa: SLF001
    language.set_language(Language.EN, persist=False)
    labels_en = export_labels_module.export_labels()
    assert [t for t in EXPORT_TEXTS if CJK.search(labels_en.text(t))] == []
    assert labels_en.text("操作记录") == "Action" and labels_en.text("试验编号") == "Experiment ID"


def test_an_english_workbook_has_english_headers_and_values(
    qapp: QApplication, language: LanguageManager, tmp_path: Path
) -> None:
    language.set_language(Language.EN, persist=False)
    record = AuditRecord(1, make_event(0, detail="=cmd"), "0" * 64, "a" * 64)
    target = tmp_path / "a.xlsx"
    write_audit([record], target, export_labels_module.export_labels())
    sheet = load_workbook(target)["Audit Trail"]
    assert [c.value for c in sheet[1]][:5] == ["No.", "Time", "Actor", "Permission", "Event type"]
    assert sheet[2][3].value == "Analyst"  # 角色按当前语言
    assert AuditFilter().describe() == "无条件"  # 写入审计库的原文固定为简体中文


def test_a_verification_banner_follows_a_language_switch(
    qapp: QApplication, window: MainWindow, language: LanguageManager
) -> None:
    sign_in(qapp, window, "admin")
    window.navigate("audit")
    page = window.page("audit")
    assert isinstance(page, AuditPage)
    assert wait_for(qapp, lambda: not page.loading)
    page.verify_button.click()
    assert wait_for(qapp, lambda: not page.verifying, 20)
    assert page.banner.title.text() == "校验通过：哈希链完好"
    language.set_language(Language.EN, persist=False)
    settle(qapp)
    assert page.banner.title.text() == "Verification passed: the hash chain is intact"
    assert chinese_left(page) == []
    page.wait_idle()
