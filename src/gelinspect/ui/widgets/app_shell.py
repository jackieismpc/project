"""应用外壳：顶部快捷栏、内容区与可自动隐藏的侧栏。

布局（自上而下）：``QuickToolbar`` 横贯窗口顶部；其下左侧为 ``SideNav``，右侧为内容区。

两种侧栏模式：

* **自动隐藏（默认）**：侧栏不占布局宽度，收起时完全不可见；左边缘留 6px 热区。
  展开时作为覆盖层滑出，内容区不重排。
* **固定**：侧栏 208px，在布局中占位，内容区变窄。

模式通过 :class:`ShellSettings` 持久化，默认用 ``QSettings``，测试注入内存实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QHideEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.quick_toolbar import QuickAction, QuickToolbar, native_shortcut_text
from gelinspect.ui.widgets.side_nav import DEFAULT_NAV, NAV_WIDTH, NavEntry, SideNav

HOT_ZONE_WIDTH = 6
"""自动隐藏模式下，左边缘热区宽度（px）。"""

LEAVE_DELAY_MS = 500
"""鼠标离开侧栏后到收起的延迟（ms）。"""

ANIMATION_MS = 180
"""侧栏滑入、滑出动画时长（ms）。"""

SHADOW_WIDTH = 14
"""覆盖层右侧阴影宽度（px）。"""

TOGGLE_SHORTCUT = "Ctrl+B"
"""展开、收起侧栏的快捷键。"""

_SETTINGS_KEY = "ui/nav_mode"


class NavMode(StrEnum):
    """侧栏模式。"""

    AUTO = "auto"
    """自动隐藏，覆盖层滑出。"""
    PINNED = "pinned"
    """固定，占布局宽度。"""


class NavState(StrEnum):
    """侧栏当前状态。"""

    CLOSED = "closed"
    """自动隐藏模式下已收起。"""
    OPEN_HOVER = "open_hover"
    """由鼠标进入热区展开，鼠标离开后延迟收起。"""
    OPEN_STICKY = "open_sticky"
    """由点击、触摸、按钮或快捷键展开，只由外部点击、选中项、Esc 等收起。"""
    PINNED = "pinned"
    """固定模式，常显。"""


class ShellSettings(Protocol):
    """外壳设置的持久化接口。"""

    def load_nav_mode(self) -> NavMode | None:
        """读取已保存的侧栏模式。

        Returns:
            已保存的模式；没有或无法识别时为 ``None``。
        """
        ...

    def save_nav_mode(self, mode: NavMode) -> None:
        """保存侧栏模式。

        Args:
            mode: 要保存的模式。
        """
        ...


class MemoryShellSettings:
    """内存版设置，用于测试与截图，不写磁盘。"""

    def __init__(self, mode: NavMode | None = None) -> None:
        """创建内存设置。

        Args:
            mode: 初始保存的模式。
        """
        self._mode = mode

    def load_nav_mode(self) -> NavMode | None:
        """读取模式。

        Returns:
            已保存的模式或 ``None``。
        """
        return self._mode

    def save_nav_mode(self, mode: NavMode) -> None:
        """保存模式。

        Args:
            mode: 要保存的模式。
        """
        self._mode = mode


class QSettingsShellSettings:
    """基于 ``QSettings`` 的设置，应用默认使用。"""

    def __init__(self, settings: QSettings | None = None) -> None:
        """创建设置。

        Args:
            settings: ``QSettings`` 实例，默认使用组织 GelInspect、应用 GelInspect。
        """
        self._settings = settings or QSettings("GelInspect", "GelInspect")

    def load_nav_mode(self) -> NavMode | None:
        """读取模式。

        Returns:
            已保存的模式；没有或值非法时为 ``None``。
        """
        raw = self._settings.value(_SETTINGS_KEY)
        try:
            return NavMode(str(raw))
        except ValueError:
            return None

    def save_nav_mode(self, mode: NavMode) -> None:
        """保存模式并立即同步。

        Args:
            mode: 要保存的模式。
        """
        self._settings.setValue(_SETTINGS_KEY, mode.value)
        self._settings.sync()


class _HotZone(QWidget):
    """自动隐藏模式下左边缘的透明热区：鼠标进入或按下时请求展开。"""

    entered = Signal()
    pressed = Signal()

    def __init__(self, parent: QWidget) -> None:
        """创建热区。

        Args:
            parent: 父控件（外壳）。
        """
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAccessibleName(self.tr("侧栏热区"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新无障碍名称。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.setAccessibleName(self.tr("侧栏热区"))
        super().changeEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        """鼠标进入：请求悬停展开。

        Args:
            event: 进入事件。
        """
        self.entered.emit()
        super().enterEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """点按（触摸屏的主要入口之一）：请求持久展开。

        Args:
            event: 鼠标事件。
        """
        self.pressed.emit()
        event.accept()


class _EdgeShadow(QWidget):
    """覆盖层右侧的分隔阴影：1px 边线加向右渐隐的阴影，不拦截鼠标。"""

    def __init__(self, parent: QWidget) -> None:
        """创建阴影。

        Args:
            parent: 父控件（外壳）。
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制渐隐阴影。

        Args:
            event: 绘制事件。
        """
        colors = current_tokens().colors
        start = QColor(colors.ink)
        start.setAlpha(46)
        end = QColor(colors.ink)
        end.setAlpha(0)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, start)
        gradient.setColorAt(1, end)
        p = QPainter(self)
        p.fillRect(self.rect(), gradient)
        p.end()


class AppShell(QWidget):
    """应用外壳：快捷栏加侧栏加内容区。

    Signals:
        navigationRequested: 用户在侧栏选中了某项（含已选中项），参数为导航项 key。
        modeChanged: 模式变化，参数为 ``NavMode`` 的值。
        navOpenChanged: 覆盖层展开或收起，参数为是否展开（固定模式下不发出）。
    """

    navigationRequested = Signal(str)  # noqa: N815
    modeChanged = Signal(str)  # noqa: N815
    navOpenChanged = Signal(bool)  # noqa: N815

    def __init__(
        self,
        content: QWidget,
        *,
        nav_entries: tuple[NavEntry, ...] = DEFAULT_NAV,
        quick_actions: tuple[QuickAction, ...] | None = None,
        settings: ShellSettings | None = None,
        mode: NavMode | None = None,
        animations_enabled: bool = True,
        leave_delay_ms: int = LEAVE_DELAY_MS,
        parent: QWidget | None = None,
    ) -> None:
        """创建外壳。

        Args:
            content: 内容区控件。
            nav_entries: 侧栏导航项。
            quick_actions: 快捷操作声明，默认使用 ``DEFAULT_ACTIONS``。
            settings: 模式持久化接口，默认使用 ``QSettings``。
            mode: 强制初始模式（不读取已保存值，也不立即保存），主要给截图用。
            animations_enabled: 是否启用滑入滑出动画。测试与离屏截图应关闭。
            leave_delay_ms: 鼠标离开侧栏后的收起延迟，测试可注入较小值。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "root")
        self._settings: ShellSettings = settings or QSettingsShellSettings()
        self._animations_enabled = animations_enabled
        self._state = NavState.CLOSED
        self._mode = mode or self._settings.load_nav_mode() or NavMode.AUTO
        self._restore_focus: QWidget | None = None

        self.content = content
        self.content.setParent(self)
        self.content.show()
        self.toolbar = QuickToolbar(quick_actions) if quick_actions else QuickToolbar()
        self.toolbar.setParent(self)
        self.toolbar.show()
        self.nav = SideNav(nav_entries, pinnable=True, parent=self)
        self.nav.hide()
        self.hot_zone = _HotZone(self)
        self.shadow = _EdgeShadow(self)

        self.leave_timer = QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.setInterval(max(0, leave_delay_ms))
        self.leave_timer.timeout.connect(self._on_leave_timeout)

        self._animation = QPropertyAnimation(self.nav, b"pos", self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._place_shadow)
        self._animation.finished.connect(self._on_animation_finished)

        self.toolbar.menuRequested.connect(self._on_menu_clicked)
        self.nav.pinToggled.connect(self._on_pin_toggled)
        self.nav.itemActivated.connect(self._on_item_activated)
        self.hot_zone.entered.connect(self._on_hot_enter)
        self.hot_zone.pressed.connect(self._on_hot_press)
        self.hot_zone.installEventFilter(self)
        self.nav.installEventFilter(self)

        self._toggle_shortcut = QShortcut(QKeySequence(TOGGLE_SHORTCUT), self)
        self._toggle_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._toggle_shortcut.activated.connect(self._on_toggle_shortcut)
        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._escape_shortcut.activated.connect(self.close_nav)
        self._escape_shortcut.setEnabled(False)

        self._apply_mode(initial=True)
        if mode is None:
            # 首次启动没有已保存的值时，把默认模式写入，之后保持一致。
            self._settings.save_nav_mode(self._mode)

    # ------------------------------------------------------------------ 公共接口

    def mode(self) -> NavMode:
        """当前侧栏模式。

        Returns:
            ``NavMode.AUTO`` 或 ``NavMode.PINNED``。
        """
        return self._mode

    def state(self) -> NavState:
        """当前侧栏状态。

        Returns:
            :class:`NavState`。
        """
        return self._state

    def is_nav_open(self) -> bool:
        """侧栏当前是否对用户可见（固定模式或覆盖层已展开）。

        Returns:
            可见时为 True。
        """
        return self._state is not NavState.CLOSED

    def set_mode(self, mode: NavMode) -> None:
        """切换模式并持久化。

        Args:
            mode: 新模式。
        """
        if mode is self._mode:
            return
        self._mode = mode
        self._settings.save_nav_mode(mode)
        self._apply_mode(initial=False)
        self.modeChanged.emit(mode.value)

    def open_nav(self, *, sticky: bool = True, focus: bool = False) -> None:
        """展开覆盖层。固定模式下无操作。

        Args:
            sticky: True 表示由点击、触摸、按钮或快捷键打开，鼠标离开不收起；
                False 表示悬停打开，鼠标离开后延迟收起。
            focus: 是否把键盘焦点移到侧栏当前项（键盘打开时使用）。
        """
        if self._mode is NavMode.PINNED:
            return
        self.leave_timer.stop()
        target = NavState.OPEN_STICKY if sticky else NavState.OPEN_HOVER
        if self._state is NavState.CLOSED:
            self._restore_focus = QApplication.focusWidget()
            self._state = target
            self._escape_shortcut.setEnabled(True)
            self._slide(open_=True)
            self.navOpenChanged.emit(True)
        elif sticky:
            self._state = NavState.OPEN_STICKY
        if focus:
            self._focus_nav()
        self._update_menu_hint()

    def close_nav(self) -> None:
        """收起覆盖层并归还焦点。固定模式或已收起时无操作。"""
        if self._mode is NavMode.PINNED or self._state is NavState.CLOSED:
            return
        self.leave_timer.stop()
        self._state = NavState.CLOSED
        self._escape_shortcut.setEnabled(False)
        self._return_focus()
        self._slide(open_=False)
        self.navOpenChanged.emit(False)
        self._update_menu_hint()

    def toggle_nav(self, *, focus: bool = False) -> None:
        """切换：覆盖层展开则收起，收起则以持久方式展开。

        固定模式下等同于取消固定，侧栏回到自动隐藏并收起。

        Args:
            focus: 展开时是否把焦点移入侧栏。
        """
        if self._mode is NavMode.PINNED:
            self.set_mode(NavMode.AUTO)
        elif self._state is NavState.CLOSED:
            self.open_nav(sticky=True, focus=focus)
        else:
            self.close_nav()

    # ------------------------------------------------------------------ 模式与布局

    def _apply_mode(self, *, initial: bool) -> None:
        """按当前模式重置侧栏、热区与阴影，并重新布局。

        Args:
            initial: 是否为构造阶段。
        """
        self._animation.stop()
        self.leave_timer.stop()
        pinned = self._mode is NavMode.PINNED
        self.nav.set_pinned(pinned)
        if not initial and not pinned:
            self._return_focus()
        self._state = NavState.PINNED if pinned else NavState.CLOSED
        self._escape_shortcut.setEnabled(False)
        self.nav.setVisible(pinned)
        self.nav.setEnabled(pinned)  # 收起时禁用，保证其控件无法获得焦点
        self.hot_zone.setVisible(not pinned)
        self.shadow.hide()
        self._relayout()
        self._update_menu_hint()

    def _body_top(self) -> int:
        """侧栏与内容区顶部位置，即快捷栏下沿。

        Returns:
            y 坐标（px）。
        """
        return self.toolbar.height()

    def _relayout(self) -> None:
        """手动布局：快捷栏、内容区、侧栏（固定时）、热区与覆盖层高度。"""
        w, h = self.width(), self.height()
        top = self._body_top()
        body_h = max(0, h - top)
        pinned = self._mode is NavMode.PINNED
        self.toolbar.setGeometry(0, 0, w, top)
        left = NAV_WIDTH if pinned else 0
        self.content.setGeometry(left, top, max(0, w - left), body_h)
        running = self._animation.state() == QAbstractAnimation.State.Running
        if running:
            self.nav.setGeometry(self.nav.x(), top, NAV_WIDTH, body_h)
        elif pinned or self._state is not NavState.CLOSED:
            self.nav.setGeometry(0, top, NAV_WIDTH, body_h)
        else:
            self.nav.setGeometry(-NAV_WIDTH, top, NAV_WIDTH, body_h)
        self.hot_zone.setGeometry(0, top, HOT_ZONE_WIDTH, body_h)
        self._stack()
        self._place_shadow()

    def _stack(self) -> None:
        """层级：内容区在底，其上为热区、阴影，侧栏最上。"""
        self.content.lower()
        self.hot_zone.raise_()
        self.shadow.raise_()
        self.nav.raise_()
        self.toolbar.raise_()

    def _place_shadow(self) -> None:
        """让阴影贴着侧栏右缘（仅覆盖层可见时显示）。"""
        overlay = self._mode is NavMode.AUTO and self.nav.isVisible()
        if not overlay:
            self.shadow.hide()
            return
        right = self.nav.x() + self.nav.width()
        self.shadow.setGeometry(max(0, right), self.nav.y(), SHADOW_WIDTH, self.nav.height())
        self.shadow.show()
        self.shadow.raise_()
        self.nav.raise_()
        self.toolbar.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """窗口缩放：内容区与覆盖层高度跟随。

        Args:
            event: 尺寸事件。
        """
        super().resizeEvent(event)
        self._relayout()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小尺寸：快捷栏最小宽度与内容区最小尺寸（固定时加上侧栏）。

        Returns:
            最小尺寸。
        """
        left = NAV_WIDTH if self._mode is NavMode.PINNED else 0
        content = self.content.minimumSizeHint()
        return QSize(
            max(self.toolbar.minimumSizeHint().width(), left + content.width()),
            self.toolbar.height() + content.height(),
        )

    # ------------------------------------------------------------------ 动画

    def _slide(self, *, open_: bool) -> None:
        """滑入或滑出覆盖层。

        Args:
            open_: True 为展开。
        """
        top = self._body_top()
        start = QPoint(self.nav.x() if self.nav.isVisible() else -NAV_WIDTH, top)
        end = QPoint(0 if open_ else -NAV_WIDTH, top)
        self._animation.stop()
        self.nav.setGeometry(start.x(), top, NAV_WIDTH, max(0, self.height() - top))
        if open_:
            self.nav.setEnabled(True)
            self.nav.show()
        if not self._animations_enabled:
            self.nav.move(end)
            self._on_animation_finished()
            return
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._place_shadow()
        self._animation.start()

    def _on_animation_finished(self) -> None:
        """动画结束：收起后彻底隐藏侧栏，使其控件不可聚焦。"""
        if self._mode is NavMode.AUTO and self._state is NavState.CLOSED:
            self.nav.setVisible(False)
            self.nav.setEnabled(False)
        self._place_shadow()

    # ------------------------------------------------------------------ 焦点

    def _focus_nav(self) -> None:
        """把键盘焦点移到侧栏当前项（没有选中项时移到第一项）。"""
        items = self.nav.items()
        target = next((i for i in items if i.isChecked()), items[0] if items else None)
        if target is not None:
            target.setFocus(Qt.FocusReason.TabFocusReason)

    def _return_focus(self) -> None:
        """收起前，若焦点在侧栏内，则归还给展开前的控件或菜单按钮。"""
        focused = QApplication.focusWidget()
        if focused is None or not (focused is self.nav or self.nav.isAncestorOf(focused)):
            return
        back = self._restore_focus
        if back is None or not back.isVisible() or self.nav.isAncestorOf(back):
            back = self.toolbar.menu_button
        back.setFocus(Qt.FocusReason.OtherFocusReason)
        self._restore_focus = None

    # ------------------------------------------------------------------ 事件

    def _on_hot_enter(self) -> None:
        """鼠标进入热区：悬停展开。"""
        if self._state is NavState.CLOSED:
            self.open_nav(sticky=False)

    def _on_hot_press(self) -> None:
        """点按热区：持久展开。"""
        self.open_nav(sticky=True)

    def _on_menu_clicked(self) -> None:
        """菜单按钮：键盘聚焦时的触发视为键盘操作，把焦点带入侧栏。"""
        self.toggle_nav(focus=self.toolbar.menu_button.hasFocus())

    def _on_toggle_shortcut(self) -> None:
        """Ctrl+B：切换，展开时焦点进入侧栏。"""
        self.toggle_nav(focus=True)

    def _on_pin_toggled(self) -> None:
        """品牌行的固定按钮：在两种模式间切换。"""
        self.set_mode(NavMode.AUTO if self._mode is NavMode.PINNED else NavMode.PINNED)

    def _on_item_activated(self, key: str) -> None:
        """选中菜单项：通知外部并收起覆盖层。

        Args:
            key: 导航项标识。
        """
        self.navigationRequested.emit(key)
        self.close_nav()

    def _on_leave_timeout(self) -> None:
        """离开延迟到期：仍是悬停展开则收起。"""
        if self._state is NavState.OPEN_HOVER:
            self.close_nav()

    def _update_menu_hint(self) -> None:
        """按状态更新菜单按钮的提示，使固定与自动隐藏下的语义明确一致。"""
        if self._mode is NavMode.PINNED:
            text = self.tr("取消固定，侧栏改为自动隐藏")
        elif self._state is NavState.CLOSED:
            text = self.tr("展开侧栏")
        else:
            text = self.tr("收起侧栏")
        self.toolbar.set_menu_hint(text)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新菜单按钮提示。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self._update_menu_hint()
        super().changeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """处理侧栏与热区的鼠标进出，以及覆盖层展开时的外部点击。

        Args:
            watched: 事件目标。
            event: 事件。

        Returns:
            始终为 False，不拦截事件，点击外部时下层控件照常响应。
        """
        kind = event.type()
        if watched is self.nav:
            if kind == QEvent.Type.Enter:
                self.leave_timer.stop()
            elif kind == QEvent.Type.Leave and self._state is NavState.OPEN_HOVER:
                self.leave_timer.start()
        elif watched is self.hot_zone:
            if kind == QEvent.Type.Leave and self._state is NavState.OPEN_HOVER:
                self.leave_timer.start()
        elif (
            kind == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and self._state in (NavState.OPEN_HOVER, NavState.OPEN_STICKY)
        ):
            self._on_outside_press(event)
        return False

    def _on_outside_press(self, event: QMouseEvent) -> None:
        """应用级鼠标按下：点在侧栏与菜单按钮之外则收起。

        Args:
            event: 鼠标事件。
        """
        point = event.globalPosition().toPoint()
        nav_rect = QRect(self.nav.mapToGlobal(QPoint(0, 0)), self.nav.size())
        button = self.toolbar.menu_button
        button_rect = QRect(button.mapToGlobal(QPoint(0, 0)), button.size())
        if nav_rect.contains(point):
            if self._state is NavState.OPEN_HOVER:
                self._state = NavState.OPEN_STICKY  # 在侧栏内点按，视为有意操作
                self.leave_timer.stop()
            return
        if button_rect.contains(point):
            return  # 菜单按钮自己负责切换，避免先收起再展开
        self.close_nav()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """显示时安装应用级过滤器，用于检测覆盖层外部点击。

        Args:
            event: 显示事件。
        """
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            app.installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """隐藏时移除应用级过滤器。

        Args:
            event: 隐藏事件。
        """
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def shortcut_hint(self) -> str:
        """侧栏切换快捷键的平台显示文字。

        Returns:
            例如 ``⌘B`` 或 ``Ctrl+B``。
        """
        return native_shortcut_text(TOGGLE_SHORTCUT)
