from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import Permission, permissions_of
from gelinspect.ui.widgets.quick_toolbar import (
    DEFAULT_ACTIONS,
    TOOLBAR_HEIGHT,
    QuickToolbar,
    native_shortcut_text,
)

CTRL = Qt.KeyboardModifier.ControlModifier

SHORTCUT_KEYS = {
    "new_experiment": Qt.Key.Key_N,
    "open_file": Qt.Key.Key_O,
    "templates": Qt.Key.Key_T,
    "print_report": Qt.Key.Key_P,
    "audit_trail": Qt.Key.Key_L,
    "instrument_settings": Qt.Key.Key_Comma,
}


def make_toolbar(qtbot: QtBot, width: int = 1280, *, allow_all: bool = True) -> QuickToolbar:
    toolbar = QuickToolbar()
    qtbot.addWidget(toolbar)
    if allow_all:
        toolbar.set_user_permissions(set(Permission))
    toolbar.resize(width, TOOLBAR_HEIGHT)
    toolbar.show()
    qtbot.waitExposed(toolbar)
    toolbar.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is toolbar, timeout=2000)
    return toolbar


def test_declared_actions_and_height(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot)
    assert toolbar.action_ids() == tuple(SHORTCUT_KEYS)
    assert [a.id for a in DEFAULT_ACTIONS] == list(SHORTCUT_KEYS)
    assert toolbar.height() == TOOLBAR_HEIGHT
    assert toolbar.menu_button.height() >= 44
    for action_id in toolbar.action_ids():
        assert toolbar.button(action_id).height() >= 44


def test_button_click_emits_action_id(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot)
    with qtbot.waitSignal(toolbar.actionTriggered) as blocker:
        QTest.mouseClick(toolbar.button("templates"), Qt.MouseButton.LeftButton)
    assert blocker.args == ["templates"]


def test_menu_button_emits_menu_requested(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot)
    with qtbot.waitSignal(toolbar.menuRequested):
        QTest.mouseClick(toolbar.menu_button, Qt.MouseButton.LeftButton)


def test_shortcuts_trigger_actions(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot)
    for action_id, key in SHORTCUT_KEYS.items():
        with qtbot.waitSignal(toolbar.actionTriggered, timeout=1000) as blocker:
            QTest.keyClick(toolbar, key, CTRL)
        assert blocker.args == [action_id]


def test_tooltip_shows_native_shortcut(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot)
    for spec in DEFAULT_ACTIONS:
        tip = toolbar.button(spec.id).toolTip()
        assert native_shortcut_text(spec.shortcut) in tip
        assert tip.startswith(toolbar.action(spec.id).text())


def test_no_permission_disables_button_and_shortcut(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, allow_all=False)
    spy = QSignalSpy(toolbar.actionTriggered)
    for action_id, key in SHORTCUT_KEYS.items():
        assert not toolbar.button(action_id).isEnabled()
        assert "无此权限" in toolbar.button(action_id).toolTip()
        QTest.keyClick(toolbar, key, CTRL)
    assert spy.count() == 0


def test_permissions_follow_role(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, allow_all=False)
    toolbar.set_user_permissions(permissions_of(Role.EXPERIMENTER))
    enabled = {i for i in toolbar.action_ids() if toolbar.button(i).isEnabled()}
    assert enabled == {"new_experiment", "open_file", "templates", "print_report"}
    spy = QSignalSpy(toolbar.actionTriggered)
    QTest.keyClick(toolbar, Qt.Key.Key_L, CTRL)  # 审计追踪：实验员无权限
    assert spy.count() == 0
    QTest.keyClick(toolbar, Qt.Key.Key_N, CTRL)
    assert spy.count() == 1


def _assert_layout_ok(toolbar: QuickToolbar) -> None:
    rect = toolbar.rect()
    boxes = [toolbar.menu_button.geometry(), toolbar.status_area.geometry()]
    visible = [toolbar.button(i) for i in toolbar.action_ids() if not toolbar.button(i).isHidden()]
    if toolbar.more_button.isVisible():
        visible.append(toolbar.more_button)
    for button in visible:
        # 文字不截断：按钮宽度不小于其建议宽度。
        assert button.width() >= button.sizeHint().width()
        boxes.append(button.geometry())
    for box in boxes:
        assert rect.contains(box), box
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not a.intersects(b), (a, b)


def test_wide_toolbar_shows_all_actions(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, 1280)
    assert toolbar.overflow_ids() == ()
    assert not toolbar.more_button.isVisible()
    _assert_layout_ok(toolbar)


def test_narrow_toolbar_moves_tail_actions_to_more(qtbot: QtBot) -> None:
    previous = 0
    for width in (1280, 1024, 800):
        toolbar = make_toolbar(qtbot, width)
        _assert_layout_ok(toolbar)
        overflow = toolbar.overflow_ids()
        assert len(overflow) >= previous
        previous = len(overflow)
        # 收纳的总是右端连续的一段。
        assert overflow == toolbar.action_ids()[len(toolbar.action_ids()) - len(overflow) :]
        assert toolbar.more_button.isVisible() == bool(overflow)
        if overflow:
            toolbar.more_menu.aboutToShow.emit()
            assert len(toolbar.more_menu.actions()) == len(overflow)
    assert previous > 0


def test_overflow_menu_triggers_action_and_shortcut_still_works(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, 800)
    assert "instrument_settings" in toolbar.overflow_ids()
    toolbar.more_menu.aboutToShow.emit()
    last = toolbar.more_menu.actions()[-1]
    with qtbot.waitSignal(toolbar.actionTriggered) as blocker:
        last.trigger()
    assert blocker.args == ["instrument_settings"]
    with qtbot.waitSignal(toolbar.actionTriggered) as blocker:
        QTest.keyClick(toolbar, Qt.Key.Key_Comma, CTRL)
    assert blocker.args == ["instrument_settings"]


def test_overflow_menu_marks_disabled_actions(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, 800, allow_all=False)
    toolbar.more_menu.aboutToShow.emit()
    assert toolbar.more_menu.actions()
    assert all(not a.isEnabled() for a in toolbar.more_menu.actions())


def test_resize_back_restores_buttons(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, 800)
    assert toolbar.overflow_ids()
    toolbar.resize(1280, TOOLBAR_HEIGHT)
    assert toolbar.overflow_ids() == ()


def test_status_area_updates(qtbot: QtBot) -> None:
    toolbar = make_toolbar(qtbot, 1280)
    toolbar.set_instrument_status(True)
    toolbar.set_language("English")
    toolbar.set_user("张工", "实验员")
    _assert_layout_ok(toolbar)
