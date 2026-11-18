import pytest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from gelinspect.ui.widgets.badge import StatusBadge
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.icons import IconLoader, available_icons
from gelinspect.ui.widgets.marks import WellState
from gelinspect.ui.widgets.side_nav import DEFAULT_NAV, SideNav
from gelinspect.ui.widgets.sparkline import Sparkline
from gelinspect.ui.widgets.stat_card import ReadoutCard
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
        nav.set_current("history")
    assert blocker.args == ["history"]
    assert nav.current() == "history"


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


@pytest.mark.parametrize("font_scale", ["small", "system", "large"])
def test_readout_value_keeps_its_font_metrics_height(
    qtbot: QtBot, qapp: QApplication, font_scale: str
) -> None:
    from gelinspect.services.appearance_service import AppearancePreferences
    from gelinspect.ui.theme.appearance import apply_appearance

    apply_appearance(qapp, AppearancePreferences(font_scale=font_scale))
    try:
        card = ReadoutCard("室温", "37.1", "℃")
        qtbot.addWidget(card)
        card.resize(560, card.sizeHint().height())
        card.show()
        qtbot.wait(20)
        assert "37.1" in card._value.text() and "℃" in card._value.text()
        assert card._value.height() >= card._value.sizeHint().height()
    finally:
        apply_appearance(qapp, AppearancePreferences())


def test_all_icons_load(qapp: QApplication) -> None:
    names = available_icons()
    assert len(names) >= 20
    for name in names:
        assert not IconLoader.pixmap(name, 18, "primary").isNull()


def test_toast_can_be_replaced_after_native_deletion(qtbot: QtBot) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    host = QWidget()
    qtbot.addWidget(host)
    first = Toast.show_message(host, "第一条", timeout_ms=0)
    # 也覆盖外部直接删除；此时 Python 引用仍在，但原生对象已经不存在。
    first.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(first)
    second = Toast.show_message(host, "第二条", timeout_ms=0)
    assert second.message_label.text() == "第二条"
    assert isValid(second)


def test_toast_repeated_replacement_and_expiry(qtbot: QtBot) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    for index in range(30):
        first = Toast.show_message(host, f"保存 {index}", timeout_ms=0)
        second = Toast.show_message(host, f"替换 {index}", timeout_ms=1)
        # 重复关闭旧提示不能清除当前提示的引用。
        first.dismiss()
        assert getattr(host, "_gel_toast", None) is second
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qtbot.waitUntil(lambda: getattr(host, "_gel_toast", None) is None, timeout=2000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not host.findChildren(Toast)
        host.resize(400 + index, 300)


def test_toast_expiry_regression_in_separate_process() -> None:
    """原生崩溃必须由独立进程的退出状态检测，不能靠捕获 Python 异常。"""
    import os
    import subprocess
    import sys

    script = """
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QCoreApplication, QEvent
from gelinspect.ui.widgets.toast import Toast
app = QApplication([])
host = QWidget()
for i in range(50):
    toast = Toast.show_message(host, str(i), timeout_ms=0)
    toast.dismiss()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
Toast.show_message(host, "done", timeout_ms=0)
host.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
