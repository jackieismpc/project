"""96 孔（12 排 × 8 支）：整板启动、监控以及两种宽度下的布局。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QScrollArea
from pytestqt.qtbot import QtBot

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import ExperimentType, TubeRole
from gelinspect.domain.plate import HOLES_PER_ROW, ROW_COUNT, TUBE_COUNT, tube_to_position
from gelinspect.services.template_service import TemplateDraft, TemplateItemInput
from gelinspect.ui.i18n import Language, LanguageManager
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateMode
from tests.ui.conftest import DEMO_PASSWORDS, settle, wait_for
from tests.ui.monitor_helpers import wait_finished
from tests.ui.test_layout_languages import LANGUAGES, SIZES, overflow_report, use
from tests.ui.wizard_helpers import (
    STEP_SAMPLES,
    choose_template,
    click,
    open_wizard,
    pass_reagents,
    select_tubes,
)

FULL_PLATE = "整板 96 管"


def assert_no_horizontal_scroll(window: MainWindow) -> None:
    """页面（和其中的滚动区）不出现横向滚动条：12 列放得下，不能靠横向滚动。"""
    for area in window.findChildren(QScrollArea):
        if area.isVisible():
            bar = area.horizontalScrollBar()
            assert bar.maximum() == 0, f"{area.objectName() or type(area).__name__} 出现横向滚动"


def add_full_plate_template(context: AppContext) -> None:
    admin = context.auth.login("admin", DEMO_PASSWORDS["admin"])
    context.templates.create_template(
        admin,
        TemplateDraft(
            FULL_PLATE,
            ExperimentType.INTERFERENCE,
            [TemplateItemInput(f"整板样品 {n}", TubeRole.TEST) for n in range(1, TUBE_COUNT + 1)],
        ),
    )


@pytest.mark.parametrize("mode", list(PlateMode), ids=lambda m: m.value)
@pytest.mark.parametrize("width", [640, 760, 1000])
def test_plate_grid_has_twelve_columns_that_fit(qtbot: QtBot, mode: PlateMode, width: int) -> None:
    grid = PlateGrid(mode)
    qtbot.addWidget(grid)
    grid.set_countdown(1, "Analyzing", active=True)
    grid.resize(width, grid.sizeHint().height())
    grid.show()
    bounds = QRectF(grid.rect())
    column = grid.width() / ROW_COUNT
    for tube in range(1, TUBE_COUNT + 1):
        rect = grid.well_rect(tube)
        assert bounds.contains(rect), (tube, rect)
        pos = tube_to_position(tube)
        assert rect.center().x() // column + 1 == pos.row_index  # 每排一竖列
    # 排标签（Row12）在一列宽度内放得下
    label_width = QFontMetrics(grid.font()).horizontalAdvance("Row12")
    assert label_width <= column
    assert grid.minimumSizeHint().width() <= 12 * 40


def test_first_and_last_rows_are_numbered_left_to_right(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.resize(760, 420)
    first = [grid.tube_at(grid.well_rect(t).center()) for t in range(1, HOLES_PER_ROW + 1)]
    assert first == list(range(1, 9))
    last = grid.well_rect(TUBE_COUNT)
    assert (
        tube_to_position(TUBE_COUNT).row_index == 12
        and tube_to_position(TUBE_COUNT).hole_index == 8
    )
    assert last.center().x() > grid.well_rect(1).center().x()
    assert grid.well_rect(89).center().y() == grid.well_rect(1).center().y()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("lang", LANGUAGES, ids=lambda x: x.value)
def test_full_plate_wizard_and_monitor_fit(
    qapp: QApplication,
    fast_window: MainWindow,
    fast_context: AppContext,
    language: LanguageManager,
    lang: Language,
    size: tuple[int, int],
) -> None:
    window = fast_window
    add_full_plate_template(fast_context)
    use(window, language, lang, size)
    page = open_wizard(qapp, window, "experimenter")
    choose_template(qapp, page, FULL_PLATE)
    click(qapp, page.template_next)
    pass_reagents(qapp, page)
    click(qapp, page.reagent_next)
    assert page.current_step() == STEP_SAMPLES
    select_tubes(qapp, page, range(1, TUBE_COUNT + 1))
    assert page.draft().selected == list(range(1, TUBE_COUNT + 1))
    assert overflow_report(window) == []
    click(qapp, page.sample_next)
    click(qapp, page.confirm_start)
    assert wait_for(qapp, lambda: window.current_route == "monitor", 30.0)
    monitor = window.page("monitor")
    assert isinstance(monitor, MonitorPage)
    settle(qapp, 8)
    assert overflow_report(window) == []
    # 读数开始后视频卡下方多出“翻转角度”等文字，仍在实时页上再查一次
    assert wait_for(qapp, lambda: monitor.plate.state(1) is not WellState.INCUBATING, 120.0)
    assert monitor.view_model is not None and monitor.view_model.live
    assert wait_for(qapp, lambda: "°" in monitor.algo_info.text(), 60.0)
    assert overflow_report(window) == []
    assert_no_horizontal_scroll(window)
    wait_finished(qapp, fast_context, 300.0)
    assert wait_for(qapp, lambda: monitor.view_model is not None and not monitor.view_model.live)
    stored = fast_context.database.experiments.get(monitor.experiment_id or "")
    assert stored is not None and len(stored.results) == TUBE_COUNT
    assert monitor.plate.state(TUBE_COUNT) is not WellState.EMPTY
    assert overflow_report(window) == []
    window.navigate("dashboard")
    assert overflow_report(window) == []
