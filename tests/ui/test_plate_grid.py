import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from gelinspect.domain.plate import position_to_tube, tube_to_position
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.plate_grid import PlateGrid, PlateLegend, PlateMode, TubeStrip


def test_numbering_matches_domain(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.resize(600, 420)
    for tube in range(1, 81):
        pos = tube_to_position(tube)
        assert grid.tube_at(grid.well_rect(tube).center()) == position_to_tube(
            pos.row_index, pos.hole_index
        )
    # 竖向编号：第 1 列是 1-8，第 2 列第一孔是 9。
    assert grid.well_rect(1).center().x() == grid.well_rect(8).center().x()
    assert grid.well_rect(9).center().x() > grid.well_rect(8).center().x()
    assert grid.well_rect(2).center().y() > grid.well_rect(1).center().y()


def test_click_emits_tube_clicked(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.resize(600, 420)
    grid.show()
    center = grid.well_rect(17).center().toPoint()
    with qtbot.waitSignal(grid.tubeClicked) as blocker:
        QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=center)
    assert blocker.args == [17]


def test_select_mode_toggles(qtbot: QtBot) -> None:
    grid = PlateGrid(PlateMode.SELECT)
    qtbot.addWidget(grid)
    grid.resize(600, 420)
    grid.show()
    seen: list[list[int]] = []
    grid.selectionChanged.connect(seen.append)
    for tube in (3, 4, 3):
        QTest.mouseClick(
            grid, Qt.MouseButton.LeftButton, pos=grid.well_rect(tube).center().toPoint()
        )
    assert seen == [[3], [3, 4], [4]]
    assert grid.selected_tubes() == [4]


def test_selection_limit(qtbot: QtBot) -> None:
    grid = PlateGrid(PlateMode.SELECT)
    qtbot.addWidget(grid)
    grid.set_selection_limit(2)
    grid.set_selected([1, 2, 3])
    assert grid.selected_tubes() == [1, 2]


def test_keyboard_navigation(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.show()
    QTest.keyClick(grid, Qt.Key.Key_Down)
    assert grid.current_tube() == 2
    QTest.keyClick(grid, Qt.Key.Key_Right)
    assert grid.current_tube() == 10
    with qtbot.waitSignal(grid.tubeClicked) as blocker:
        QTest.keyClick(grid, Qt.Key.Key_Space)
    assert blocker.args == [10]
    QTest.keyClick(grid, Qt.Key.Key_End)
    assert grid.current_tube() == 80
    assert "80" in grid.accessibleDescription()
    assert grid.accessibleName()


def test_all_states_paint(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.resize(600, 420)
    states = list(WellState)
    grid.set_states({n: states[n % len(states)] for n in range(1, 81)})
    grid.set_countdown(2, "读数中", active=True)
    pixmap = grid.grab()
    assert not pixmap.isNull()
    for sel_mode in PlateMode:
        grid.set_mode(sel_mode)
        assert not grid.grab().isNull()


def test_invalid_tube_rejected(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    with pytest.raises(Exception, match="管号"):
        grid.set_state(81, WellState.POSITIVE)


def test_hit_test_outside(qtbot: QtBot) -> None:
    grid = PlateGrid()
    qtbot.addWidget(grid)
    grid.resize(600, 420)
    assert grid.tube_at(QPointF(1, 1)) is None


def test_legend_and_strip_paint(qtbot: QtBot) -> None:
    legend = PlateLegend()
    strip = TubeStrip([0.5, 2.0, -1.0], caption_left="a", caption_right="b")
    qtbot.addWidget(legend)
    qtbot.addWidget(strip)
    assert strip.levels() == [0.5, 1.0, 0.0, 0, 0, 0, 0, 0]
    assert not legend.grab().isNull()
    assert not strip.resize(360, 190) or not strip.grab().isNull()
