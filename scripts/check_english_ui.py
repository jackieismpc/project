"""英文界面检查：切到 English，逐页浏览，列出仍含中文字符的界面文字。

用法::

    QT_QPA_PLATFORM=offscreen uv run python scripts/check_english_ui.py

先用真实向导跑完并复核、签署一次试验，让监控、单管、报告、历史、审计各页都有数据，
再以三种角色逐页扫描所有控件（标签、按钮、提示、下拉项、标签页、表头和单元格、对话框）。
没有残留时返回 0，否则列出残留并返回 1。试验数据本身（用户填的样品名称等）不算界面文字，
但内置模板名、结论、状态这类由软件生成的内容必须翻译。
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import acceptance_support as support
from PySide6.QtCore import QAbstractItemModel, QAbstractListModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QLabel,
    QLineEdit,
    QTabBar,
    QWidget,
)

from gelinspect.ui.i18n import Language
from gelinspect.ui.routes import PAGE_ROUTES
from gelinspect.ui.widgets.toast import Toast

CJK = re.compile(r"[　-〿一-鿿＀-￯]")
ALLOWED = {"简体中文"}
"""语言菜单里各语言用自己的名字显示，这是应该保留的中文。"""


def _model_strings(model: QAbstractItemModel) -> list[str]:
    found: list[str] = []
    root = QModelIndex()
    columns = 1 if isinstance(model, QAbstractListModel) else model.columnCount(root)
    for column in range(columns):
        header = model.headerData(column, Qt.Orientation.Horizontal)
        if isinstance(header, str):
            found.append(header)
    for row in range(min(model.rowCount(root), 200)):
        for column in range(columns):
            value = model.index(row, column).data(Qt.ItemDataRole.DisplayRole)
            if isinstance(value, str):
                found.append(value)
    return found


def widget_strings(widget: QWidget) -> list[tuple[str, str]]:
    """收集一个控件上所有用户可见的文字，返回（来源, 文字）。"""
    found: list[tuple[str, str]] = []
    if widget.toolTip():
        found.append(("toolTip", widget.toolTip()))
    if isinstance(widget, QDialog) and widget.windowTitle():
        found.append(("windowTitle", widget.windowTitle()))
    if isinstance(widget, QLabel):
        found.append(("label", widget.text()))
    if isinstance(widget, QAbstractButton):
        found.append(("button", widget.text()))
    if isinstance(widget, QGroupBox):
        found.append(("group", widget.title()))
    if isinstance(widget, QLineEdit):
        found.append(("placeholder", widget.placeholderText()))
    if isinstance(widget, QComboBox):
        found.extend(("combo", widget.itemText(i)) for i in range(widget.count()))
    if isinstance(widget, QTabBar):
        found.extend(("tab", widget.tabText(i)) for i in range(widget.count()))
    if isinstance(widget, QAbstractItemView) and widget.model() is not None:
        found.extend(("cell", text) for text in _model_strings(widget.model()))
    return found


def scan(root_widgets: list[QWidget], where: str, hits: dict[str, set[str]]) -> None:
    """扫描一组顶层控件及其全部子孙，把含中文的文字记到 ``hits``。"""
    for top in root_widgets:
        for widget in [top, *top.findChildren(QWidget)]:
            if isinstance(widget, Toast):
                continue  # 提示条是转瞬即逝的，语言切换前弹出的不算
            if not widget.isVisibleTo(top) or isinstance(widget.parent(), Toast):
                continue  # 没有显示出来的页面和控件用户看不到
            for source, text in widget_strings(widget):
                plain = re.sub(r"<[^>]+>", "", text)
                if CJK.search(plain) and plain.strip() not in ALLOWED:
                    hits[f"{where} / {type(widget).__name__}.{source}"].add(plain.strip()[:100])


def main() -> int:
    """跑一遍并打印结果。"""
    hits: dict[str, set[str]] = defaultdict(set)
    with tempfile.TemporaryDirectory() as tmp:
        rig = support.open_rig(Path(tmp))
        try:
            experiment_id = support.run_experiment(rig)
            support.finish_and_review(rig, experiment_id)
            support.sign_experiment(rig, experiment_id)
            # 再跑一个未签署的，让工作台和历史有“已完成”的条目
            rig.window.logout(confirm=False)
            support.settle(rig.app)
            rig.language.set_language(Language.EN, persist=False)
            support.settle(rig.app)
            scan([rig.window], "login", hits)
            for username in ("experimenter", "analyst", "admin"):
                support.sign_in(rig.app, rig.window, username)
                for route in PAGE_ROUTES:
                    if rig.window.navigate(route.key):
                        support.settle(rig.app)
                        scan([rig.window], f"{username}:{route.key}", hits)
                rig.window.open_experiment(experiment_id)
                support.settle(rig.app)
                scan([rig.window], f"{username}:monitor-open", hits)
                for tab in range(4):
                    page = rig.window.page("monitor")
                    page.tabs.set_current(tab)
                    page.stack.setCurrentIndex(tab)
                    support.settle(rig.app)
                    scan([rig.window], f"{username}:monitor-tab{tab}", hits)
                rig.window.open_tube(experiment_id, 1)
                support.settle(rig.app)
                scan([rig.window], f"{username}:tube", hits)
                rig.window.open_report(experiment_id)
                support.settle(rig.app)
                scan([rig.window], f"{username}:report", hits)
                scan_dialogs(rig, username, experiment_id, hits)
                rig.window.lock_screen()
                support.settle(rig.app)
                scan([rig.window], f"{username}:lock", hits)
                rig.window.logout(confirm=False)
                support.settle(rig.app)
        finally:
            support.close_rig(rig)
    if not hits:
        print("英文界面没有发现中文残留。")
        return 0
    print(f"发现 {sum(len(v) for v in hits.values())} 处含中文的界面文字：")
    for key in sorted(hits):
        for text in sorted(hits[key]):
            print(f"  {key}: {text}")
    return 1


def scan_dialogs(
    rig: support.Rig, username: str, experiment_id: str, hits: dict[str, set[str]]
) -> None:
    """实例化各个对话框（不进入事件循环）并扫描。"""
    from gelinspect.domain.enums import Role
    from gelinspect.services.reporting.report_service import ReportService
    from gelinspect.ui.pages.auth_pages import ChangePasswordDialog
    from gelinspect.ui.pages.export_password import ExportPasswordDialog
    from gelinspect.ui.pages.report_dialogs import BatchExportDialog, SignDialog
    from gelinspect.ui.pages.users_dialogs import CredentialDialog, NewUserDialog, RoleDialog

    services = rig.window.services
    assert isinstance(services.reports, ReportService)
    session = rig.window.session
    records = services.reports.signed_reports(session) if session is not None else []
    dialogs: list[QDialog] = [
        SignDialog(lambda _p: None, subject=experiment_id, signer=username, parent=rig.window),
        BatchExportDialog(records, lambda *_: None, rig.window),
        ExportPasswordDialog(rig.window),
        NewUserDialog(lambda *_: None, rig.window),
        RoleDialog("analyst", Role.ANALYST, lambda _r: None, rig.window),
        CredentialDialog("newuser", "Temp#Pass1", rig.window),
        ChangePasswordDialog(lambda _o, _n: None, parent=rig.window),
    ]
    for dialog in dialogs:
        dialog.show()
        support.settle(rig.app)
        scan([dialog], f"{username}:dialog:{type(dialog).__name__}", hits)
        dialog.close()


if __name__ == "__main__":
    sys.exit(main())
