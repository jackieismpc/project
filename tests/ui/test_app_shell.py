from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from gelinspect.ui.widgets.app_shell import (
    HOT_ZONE_WIDTH,
    AppShell,
    MemoryShellSettings,
    NavMode,
    NavState,
    ShellSettings,
)
from gelinspect.ui.widgets.side_nav import NAV_WIDTH

LEFT = Qt.MouseButton.LeftButton


def make_shell(
    qtbot: QtBot,
    *,
    mode: NavMode | None = None,
    settings: ShellSettings | None = None,
    delay_ms: int = 10,
    size: tuple[int, int] = (1280, 800),
) -> AppShell:
    content = QWidget()
    content.setMinimumSize(300, 200)
    shell = AppShell(
        content,
        settings=settings or MemoryShellSettings(),
        mode=mode,
        animations_enabled=False,
        leave_delay_ms=delay_ms,
    )
    qtbot.addWidget(shell)
    shell.resize(*size)
    shell.show()
    qtbot.waitExposed(shell)
    shell.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is shell, timeout=2000)
    return shell


def mouse_enter(widget: QWidget) -> None:
    point = QPointF(2, 2)
    QApplication.sendEvent(widget, QEnterEvent(point, point, point))


def mouse_leave(widget: QWidget) -> None:
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))


def test_default_mode_is_auto_hide_and_fully_hidden(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    assert shell.mode() is NavMode.AUTO
    assert shell.state() is NavState.CLOSED
    assert not shell.nav.isVisible()
    assert shell.hot_zone.isVisible()
    assert shell.hot_zone.width() == HOT_ZONE_WIDTH
    assert shell.content.x() == 0
    assert shell.content.width() == shell.width()


def test_hot_zone_enter_opens_and_delayed_leave_closes(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    mouse_enter(shell.hot_zone)
    assert shell.state() is NavState.OPEN_HOVER
    assert shell.nav.isVisible()
    mouse_enter(shell.nav)
    mouse_leave(shell.nav)
    assert shell.leave_timer.isActive()
    assert shell.state() is NavState.OPEN_HOVER  # 延迟内仍然展开
    qtbot.waitUntil(lambda: shell.state() is NavState.CLOSED, timeout=1000)
    assert not shell.nav.isVisible()


def test_returning_within_delay_cancels_close(qtbot: QtBot) -> None:
    shell = make_shell(qtbot, delay_ms=40)
    mouse_enter(shell.hot_zone)
    mouse_enter(shell.nav)
    mouse_leave(shell.nav)
    assert shell.leave_timer.isActive()
    mouse_enter(shell.nav)
    assert not shell.leave_timer.isActive()
    qtbot.wait(120)  # 超过延迟仍保持展开
    assert shell.state() is NavState.OPEN_HOVER
    assert shell.nav.isVisible()


def test_leave_delay_default_is_500ms(qtbot: QtBot) -> None:
    content = QWidget()
    shell = AppShell(content, settings=MemoryShellSettings(), animations_enabled=False)
    qtbot.addWidget(shell)
    assert shell.leave_timer.interval() == 500


def test_menu_button_toggles(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    QTest.mouseClick(shell.toolbar.menu_button, LEFT)
    assert shell.state() is NavState.OPEN_STICKY
    assert shell.nav.isVisible()
    QTest.mouseClick(shell.toolbar.menu_button, LEFT)
    assert shell.state() is NavState.CLOSED
    assert not shell.nav.isVisible()


def test_click_or_touch_open_survives_mouse_leave(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    QTest.mouseClick(shell.hot_zone, LEFT)  # 触摸会合成鼠标按下
    assert shell.state() is NavState.OPEN_STICKY
    mouse_enter(shell.nav)
    mouse_leave(shell.nav)
    assert not shell.leave_timer.isActive()
    qtbot.wait(60)
    assert shell.state() is NavState.OPEN_STICKY
    assert shell.nav.isVisible()


def test_hover_open_becomes_sticky_when_pressed_inside_nav(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    mouse_enter(shell.hot_zone)
    QTest.mouseClick(shell.nav, LEFT, pos=shell.nav.rect().center())
    assert shell.state() is NavState.OPEN_STICKY


def test_outside_click_closes(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    QTest.mouseClick(shell.content, LEFT)
    assert shell.state() is NavState.CLOSED
    assert not shell.nav.isVisible()


def test_click_inside_nav_does_not_close(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    QTest.mouseClick(shell.nav, LEFT, pos=shell.nav.rect().center())
    assert shell.state() is NavState.OPEN_STICKY


def test_escape_closes(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    QTest.keyClick(shell, Qt.Key.Key_Escape)
    assert shell.state() is NavState.CLOSED


def test_selecting_item_closes_and_reports(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    item = shell.nav.items()[3]
    with qtbot.waitSignal(shell.navigationRequested) as blocker:
        QTest.mouseClick(item, LEFT)
    assert blocker.args == [item.entry.key]
    assert shell.state() is NavState.CLOSED
    assert not shell.nav.isVisible()


def test_selecting_current_item_also_closes(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.nav.set_current("monitor")
    shell.open_nav()
    current = next(i for i in shell.nav.items() if i.isChecked())
    QTest.mouseClick(current, LEFT)
    assert shell.state() is NavState.CLOSED


def test_ctrl_b_toggles(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    QTest.keyClick(shell, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert shell.state() is NavState.OPEN_STICKY
    QTest.keyClick(shell, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert shell.state() is NavState.CLOSED


def test_overlay_does_not_reflow_content(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    before = shell.content.geometry()
    shell.open_nav()
    assert shell.nav.geometry().width() == NAV_WIDTH
    assert shell.nav.x() == 0
    assert shell.content.geometry() == before
    shell.close_nav()
    assert shell.content.geometry() == before


def test_pinned_mode_narrows_content(qtbot: QtBot) -> None:
    shell = make_shell(qtbot, mode=NavMode.PINNED)
    assert shell.nav.isVisible()
    assert shell.nav.width() == NAV_WIDTH
    assert shell.content.x() == NAV_WIDTH
    assert shell.content.width() == shell.width() - NAV_WIDTH
    assert not shell.hot_zone.isVisible()
    assert shell.nav.geometry().right() < shell.content.geometry().left()


def test_switching_mode_reflows_content(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    wide = shell.content.width()
    shell.set_mode(NavMode.PINNED)
    assert shell.content.width() == wide - NAV_WIDTH
    shell.set_mode(NavMode.AUTO)
    assert shell.content.width() == wide
    assert not shell.nav.isVisible()


def test_pin_button_switches_mode_and_updates_label(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    pin = shell.nav.pin_button
    assert pin.accessibleName() == "固定侧栏"
    QTest.mouseClick(pin, LEFT)
    assert shell.mode() is NavMode.PINNED
    assert shell.nav.isVisible()
    assert pin.accessibleName() == "取消固定侧栏"
    QTest.mouseClick(pin, LEFT)
    assert shell.mode() is NavMode.AUTO


def test_menu_button_in_pinned_mode_returns_to_auto_hide(qtbot: QtBot) -> None:
    shell = make_shell(qtbot, mode=NavMode.PINNED)
    QTest.mouseClick(shell.toolbar.menu_button, LEFT)
    assert shell.mode() is NavMode.AUTO
    assert shell.state() is NavState.CLOSED
    assert not shell.nav.isVisible()


def test_mode_is_persisted_and_restored(qtbot: QtBot) -> None:
    settings = MemoryShellSettings()
    first = make_shell(qtbot, settings=settings)
    assert settings.load_nav_mode() is NavMode.AUTO
    first.set_mode(NavMode.PINNED)
    assert settings.load_nav_mode() is NavMode.PINNED
    second = make_shell(qtbot, settings=settings)
    assert second.mode() is NavMode.PINNED
    assert second.nav.isVisible()


def test_mode_changed_signal(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    spy = QSignalSpy(shell.modeChanged)
    shell.set_mode(NavMode.PINNED)
    shell.set_mode(NavMode.PINNED)  # 重复设置不发信号
    assert spy.count() == 1


def test_overlay_height_follows_window(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav()
    top = shell.toolbar.height()
    assert shell.nav.height() == 800 - top
    shell.resize(1280, 600)
    assert shell.nav.height() == 600 - top
    assert shell.hot_zone.height() == 600 - top


def test_closed_nav_cannot_take_focus(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    for item in [*shell.nav.items(), shell.nav.pin_button]:
        assert not item.isVisible()
        item.setFocus(Qt.FocusReason.TabFocusReason)
        assert not item.hasFocus()


def test_focus_leaves_nav_when_it_closes(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.open_nav(focus=True)
    focused = QApplication.focusWidget()
    assert focused is not None
    assert shell.nav.isAncestorOf(focused)
    shell.close_nav()
    after = QApplication.focusWidget()
    assert after is None or not shell.nav.isAncestorOf(after)
    assert not shell.nav.isVisible()


def test_menu_hint_reflects_state(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    button = shell.toolbar.menu_button
    assert "展开" in button.toolTip()
    shell.open_nav()
    assert "收起" in button.toolTip()
    shell.set_mode(NavMode.PINNED)
    assert "取消固定" in button.toolTip()


def test_tab_traversal_never_enters_closed_nav(qtbot: QtBot) -> None:
    shell = make_shell(qtbot)
    shell.toolbar.menu_button.setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(20):
        shell.focusNextPrevChild(True)
        focused = QApplication.focusWidget()
        assert focused is None or not shell.nav.isAncestorOf(focused)
