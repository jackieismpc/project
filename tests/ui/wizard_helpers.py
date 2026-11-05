"""向导测试的公共操作：全部通过真实控件（点击、键入、回车）驱动，不直接调用页面内部方法。"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from gelinspect.domain.enums import ReagentKind
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.new_experiment import (
    STEP_CONFIRM,
    STEP_REAGENTS,
    STEP_SAMPLES,
    STEP_TEMPLATE,
    NewExperimentPage,
)
from simulation.reagents import SAMPLE_CODES
from tests.ui.conftest import settle, sign_in

__all__ = [
    "STEP_CONFIRM",
    "STEP_REAGENTS",
    "STEP_SAMPLES",
    "STEP_TEMPLATE",
    "choose_template",
    "click",
    "open_wizard",
    "pass_reagents",
    "scan",
    "select_tubes",
]

GOOD = {
    ReagentKind.LAL_REAGENT: SAMPLE_CODES["lal_valid"],
    ReagentKind.ENDOTOXIN: SAMPLE_CODES["endotoxin_valid"],
    ReagentKind.WATER: SAMPLE_CODES["water_valid"],
}


def open_wizard(
    app: QApplication, win: MainWindow, username: str = "experimenter"
) -> NewExperimentPage:
    """登录并打开新试验页。"""
    sign_in(app, win, username)
    assert win.navigate("new")
    page = win.page("new")
    assert isinstance(page, NewExperimentPage)
    settle(app)
    return page


def click(app: QApplication, button: QWidget) -> None:
    """鼠标点击一个按钮。"""
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(app)


def choose_template(app: QApplication, page: NewExperimentPage, name: str) -> None:
    """在第 1 步点击名称为 ``name`` 的模板。"""
    option = next(o for o in page._options if o.template.name == name)
    QTest.mouseClick(option, Qt.MouseButton.LeftButton)
    settle(app)


def scan(app: QApplication, page: NewExperimentPage, kind: ReagentKind, code: str) -> None:
    """像键盘楔式扫码枪那样：把码当作键盘输入，再按回车。"""
    card = page.reagent_cards[kind]
    card.input.clear()
    card.input.setFocus()
    QTest.keyClicks(card.input, code)
    QTest.keyClick(card.input, Qt.Key.Key_Return)
    settle(app)


def pass_reagents(app: QApplication, page: NewExperimentPage) -> None:
    """三种试剂都扫合格的码。"""
    for kind, code in GOOD.items():
        scan(app, page, kind, code)


def select_tubes(app: QApplication, page: NewExperimentPage, tubes: Iterable[int]) -> None:
    """在孔板上逐个点击管位（真实鼠标事件）；已选的点一下取消。"""
    for tube in tubes:
        center = page.plate.well_rect(tube).center()
        QTest.mouseClick(
            page.plate, Qt.MouseButton.LeftButton, pos=QPoint(round(center.x()), round(center.y()))
        )
    settle(app)
