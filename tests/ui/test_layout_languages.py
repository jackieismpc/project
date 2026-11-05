"""两种语言、两种窗口尺寸下，页面里没有控件超界、被挤压或截断。"""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFontMetrics
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QLabel,
    QLineEdit,
    QScrollArea,
    QWidget,
)

from gelinspect.app_context import AppContext
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow, Screen
from gelinspect.ui.pages.auth_pages import ChangePasswordDialog
from gelinspect.ui.pages.dashboard import DashboardPage
from gelinspect.ui.routes import PAGE_ROUTES
from gelinspect.ui.widgets.dialog import ConfirmDialog, ErrorDialog
from tests.ui.conftest import screen_of, settle, sign_in

SIZES = [(1280, 800), (1024, 700)]
LANGUAGES = [Language.ZH_CN, Language.EN]
TAG = re.compile(r"<[^>]+>")


def rect_in(root: QWidget, widget: QWidget) -> QRect:
    return QRect(widget.mapTo(root, QPoint(0, 0)), widget.size())


def overflow_report(root: QWidget) -> list[str]:
    """列出超界、被挤压或文字截断的控件。"""
    settle(QApplication.instance())  # type: ignore[arg-type]
    problems: list[str] = []
    bounds = root.rect()
    for widget in root.findChildren(QWidget):
        if not widget.isVisibleTo(root) or widget.width() <= 0:
            continue
        if widget.objectName().startswith("qt_") or any(
            p.objectName().startswith("qt_") for p in _parents(widget, root)
        ):
            continue  # 滚动条等 Qt 内部控件
        name = f"{type(widget).__name__}({widget.objectName() or widget.property('role')})"
        area = next((p for p in _parents(widget, root) if isinstance(p, QScrollArea)), None)
        box = rect_in(root, widget)
        if area is None:
            if not bounds.contains(box):
                problems.append(f"{name} 超出窗口：{box} 不在 {bounds} 内")
        else:
            # 滚动区内只要求不超出可视宽度（纵向可以滚动）
            viewport = rect_in(root, area.viewport())
            if box.left() < viewport.left() - 1 or box.right() > viewport.right() + 1:
                problems.append(f"{name} 超出滚动区宽度：{box} 不在 {viewport} 内")
            if area.horizontalScrollBar().isVisible():
                problems.append(f"{name} 导致出现横向滚动条")
        if isinstance(widget, QLabel) and widget.text() and not widget.pixmap():
            rich = GuiQt.mightBeRichText(widget.text())
            text = TAG.sub("", widget.text()) if rich else widget.text()
            metrics = QFontMetrics(widget.font())
            if widget.wordWrap():
                needed = metrics.boundingRect(
                    QRect(0, 0, widget.width(), 10_000), widget.alignment().value | 0x1000, text
                ).height()
                if needed > widget.height() + 2:
                    problems.append(f"{name} 换行后被截断：{text!r}")
            elif rich:
                # 富文本里字号不统一，用控件自己报告的建议宽度判断有没有被挤压
                if widget.width() < widget.sizeHint().width() - 1:
                    problems.append(f"{name} 被挤压：{text!r}")
            elif metrics.horizontalAdvance(text) > widget.width() + 1:
                problems.append(f"{name} 文字被截断：{text!r}")
        elif isinstance(widget, QAbstractButton) and widget.text():
            if widget.width() < widget.sizeHint().width() - 2:
                problems.append(f"{name} 被挤压：{widget.text()!r}")
        elif isinstance(widget, QLineEdit) and widget.placeholderText():
            metrics = QFontMetrics(widget.font())
            if metrics.horizontalAdvance(widget.placeholderText()) > widget.width() - 16:
                problems.append(f"{name} 占位文字被截断：{widget.placeholderText()!r}")
    return problems


def _parents(widget: QWidget, stop: QWidget) -> list[QWidget]:
    chain: list[QWidget] = []
    parent = widget.parentWidget()
    while parent is not None and parent is not stop:
        chain.append(parent)
        parent = parent.parentWidget()
    return chain


def use(
    window: MainWindow, language: LanguageManager, lang: Language, size: tuple[int, int]
) -> None:
    language.set_language(lang, persist=False)
    window.resize(*size)
    settle(QApplication.instance())  # type: ignore[arg-type]


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_login_and_lock_pages_fit(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
) -> None:
    use(window, language, lang, size)
    assert screen_of(window) is Screen.LOGIN
    window.login_page.show_error("x" * 3)
    assert overflow_report(window) == []
    window.login_page.reset()
    window.login_page.username.setText("admin")
    window.login_page.password.setText("wrong-password")
    window.login_page.submit_button.click()
    assert overflow_report(window) == []
    sign_in(qapp, window, "admin")
    window.lock_screen()
    window.lock_page.password.setText("wrong-password")
    window.lock_page.submit_button.click()
    assert screen_of(window) is Screen.LOCK
    assert overflow_report(window) == []


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
@pytest.mark.parametrize("username", ["admin", "experimenter", "analyst"])
def test_every_reachable_page_fits(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
    username: str,
) -> None:
    use(window, language, lang, size)
    sign_in(qapp, window, username)
    from tests.ui.conftest import wait_for

    wait_for(
        qapp, lambda: window.shell.toolbar._instrument._text.text() in ("仪器已连接", "Connected")
    )
    for route in PAGE_ROUTES:
        if window.navigate(route.key):
            assert overflow_report(window) == [], f"{route.key} / {username} / {lang.value}"
    # 工具栏本身：状态区不越过右边缘
    toolbar = window.shell.toolbar
    assert rect_in(window, toolbar.status_area).right() <= toolbar.width()


@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_dashboard_with_data_fits(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    language: LanguageManager,
    lang: Language,
) -> None:
    from tests.services.engine_helpers import ready_experiment
    from tests.ui.conftest import wait_for

    use(window, language, lang, (1024, 700))
    sign_in(qapp, window, "experimenter")
    for i in range(3):
        app_context.database.experiments.save(ready_experiment(range(1, 9), f"EXP-LAYOUT-{i}"))
    dashboard = window.page("dashboard")
    assert isinstance(dashboard, DashboardPage)
    dashboard.refresh()
    assert wait_for(qapp, lambda: dashboard.recent_table.row_count() == 3)
    assert overflow_report(window) == []


@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_dialogs_fit(qapp: QApplication, language: LanguageManager, lang: Language) -> None:
    language.set_language(lang, persist=False)
    settle(qapp)
    dialogs: list[QWidget] = [
        ChangePasswordDialog(lambda _o, _n: None, forced=True),
        ChangePasswordDialog(lambda _o, _n: None),
        ErrorDialog("t", "m" * 200, details="detail", log_path="/some/where/gelinspect.log"),
        ConfirmDialog(
            language_text("Quit and interrupt the experiment?", "退出并中断试验？", lang),
            "x " * 80,
            danger=True,
        ),
    ]
    for dialog in dialogs:
        dialog.show()
        settle(qapp)
        assert overflow_report(dialog) == []
        dialog.close()


def language_text(en: str, zh: str, lang: Language) -> str:
    return en if lang is Language.EN else zh


def test_the_checker_detects_a_real_overflow(qapp: QApplication) -> None:
    """自检：把控件放到窗口外、把标签压窄，检查器必须报告。"""
    root = QWidget()
    root.resize(200, 100)
    label = QLabel("这是一段不会被换行的很长很长的文字", root)
    label.setGeometry(10, 10, 60, 20)
    outside = QLabel("x", root)
    outside.setGeometry(190, 10, 40, 20)
    root.show()
    report = overflow_report(root)
    assert any("文字被截断" in p for p in report)
    assert any("超出窗口" in p for p in report)


# ---------------------------------------------------------------------------
# P8：向导四步、模板管理、试剂溯源和模板编辑器（中英文 × 1280×800 与 1024×700）


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_wizard_steps_fit(
    qapp: QApplication,
    window: MainWindow,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
) -> None:
    from gelinspect.domain.enums import ReagentKind
    from simulation.reagents import SAMPLE_CODES
    from tests.ui.wizard_helpers import (
        choose_template,
        click,
        open_wizard,
        pass_reagents,
        scan,
    )

    use(window, language, lang, size)
    page = open_wizard(qapp, window)
    assert overflow_report(window) == [], "步骤 1"
    choose_template(qapp, page, "灵敏度复核 (2λ:1)")
    click(qapp, page.template_next)
    scan(qapp, page, ReagentKind.LAL_REAGENT, SAMPLE_CODES["lal_expired"])
    assert page.is_locked()
    assert overflow_report(window) == [], "步骤 2（锁定）"
    pass_reagents(qapp, page)
    assert overflow_report(window) == [], "步骤 2（通过）"
    click(qapp, page.reagent_next)
    click(qapp, page.auto_button)
    assert overflow_report(window) == [], "步骤 3"
    page.sample_table.cell_item(2, 1).setText(" ")  # 出现「名称为空」提示
    assert overflow_report(window) == [], "步骤 3（提示）"
    page.sample_table.cell_item(2, 1).setText("样品")
    click(qapp, page.sample_next)
    assert overflow_report(window) == [], "步骤 4"
    page._start_error = "温度 40.0 ℃ 不在 37±1 ℃ 内"
    page._render()
    assert overflow_report(window) == [], "步骤 4（启动失败）"
    page.reset()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_templates_reagents_pages_and_editor_fit(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
) -> None:
    from gelinspect.domain.enums import ExperimentType, ReagentKind
    from gelinspect.domain.templates import BUILTIN_TEMPLATES
    from gelinspect.services.template_service import draft_of
    from gelinspect.ui.pages.template_editor import TemplateEditorDialog
    from simulation.reagents import SAMPLE_CODES

    use(window, language, lang, size)
    sign_in(qapp, window, "admin")
    sign_in(qapp, window, "experimenter")
    for kind, key in (
        (ReagentKind.LAL_REAGENT, "lal_expired"),
        (ReagentKind.LAL_REAGENT, "lal_valid"),
        (ReagentKind.ENDOTOXIN, "endotoxin_valid"),
    ):
        app_context.reagents.scan(window.session, SAMPLE_CODES[key], expected_kind=kind)
    for key in ("reagents", "templates"):
        assert window.navigate(key)
        assert overflow_report(window) == [], f"{key} / {lang.value}"
    window.logout()
    sign_in(qapp, window, "admin")
    assert window.navigate("templates")
    assert overflow_report(window) == [], "templates（管理员）"
    template = BUILTIN_TEMPLATES[ExperimentType.INTERFERENCE]
    dialog = TemplateEditorDialog(lambda d: template, initial=draft_of(template))
    dialog.show()
    settle(qapp)
    dialog._show_error("x " * 60)
    assert overflow_report(dialog) == []
    dialog.close()
