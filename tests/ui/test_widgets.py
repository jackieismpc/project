from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from gelinspect.ui.widgets.badge import StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.icons import IconLoader, available_icons
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.side_nav import DEFAULT_NAV, SideNav
from gelinspect.ui.widgets.sparkline import Sparkline
from gelinspect.ui.widgets.step_indicator import StepIndicator
from gelinspect.ui.widgets.tabs import Tabs
from gelinspect.ui.widgets.toast import Toast


def test_data_table_empty_state(qtbot: QtBot) -> None:
    table = DataTable([Column("a", "A")], empty_title="没有数据")
    qtbot.addWidget(table)
    assert table.is_empty_state_visible()
    table.set_rows([{"a": 1}, {"a": WellState.POSITIVE}])
    assert not table.is_empty_state_visible()
    assert table.row_count() == 2
    table.set_rows([])
    assert table.is_empty_state_visible()


def test_toast_shows_and_expires(qtbot: QtBot) -> None:
    host = QWidget()
    host.resize(400, 300)
    qtbot.addWidget(host)
    host.show()
    toast = Toast.show_message(host, "已保存", "success", timeout_ms=50)
    assert toast.isVisible()
    assert toast.x() + toast.width() <= host.width()
    qtbot.waitUntil(lambda: not host.findChildren(Toast), timeout=2000)


def test_button_min_height_and_variant(qtbot: QtBot) -> None:
    button = Button("停止", ButtonVariant.DANGER, icon="x")
    qtbot.addWidget(button)
    button.show()
    assert button.height() >= 44
    assert button.property("variant") == "danger"


def test_side_nav_selection(qtbot: QtBot) -> None:
    nav = SideNav(DEFAULT_NAV)
    qtbot.addWidget(nav)
    with qtbot.waitSignal(nav.currentChanged) as blocker:
        nav.set_current("monitor")
    assert blocker.args == ["monitor"]
    assert nav.current() == "monitor"


def test_step_tabs_badge_sparkline_paint(qtbot: QtBot) -> None:
    widgets: list[QWidget] = [
        StepIndicator(["a", "b", "c"], 1),
        Tabs(["x", "y"]),
        StatusBadge("ok", "success"),
        Sparkline([36.9, 37.0, 37.2]),
        Sparkline([], compact=True),
    ]
    for w in widgets:
        qtbot.addWidget(w)
        w.resize(w.sizeHint())
        assert not w.grab().isNull()


def test_all_icons_load(qapp: QApplication) -> None:
    names = available_icons()
    assert len(names) >= 20
    for name in names:
        assert not IconLoader.pixmap(name, 18, "primary").isNull()
