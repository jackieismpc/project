"""顶部快捷栏：菜单按钮、快捷操作与状态区。

快捷操作由 :data:`DEFAULT_ACTIONS` 声明式定义（标识、文字、图标、快捷键、所需权限）。
每个操作对应一个 ``QAction`` 和一个 ``QShortcut``；没有权限时按钮禁用，快捷键也不生效。
宽度不足时，右端的操作收进“更多”弹出菜单，文字不截断、控件不越界。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from gelinspect.domain.permissions import Permission
from gelinspect.ui.punctuation import annotate, colon
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.util import repolish

TOOLBAR_HEIGHT = 56
"""工具栏高度（px）。"""

ICON_SIZE = 18
"""工具栏图标边长（px）。"""


@dataclass(frozen=True, slots=True)
class QuickAction:
    """一个快捷操作的声明。

    Attributes:
        id: 稳定标识，作为 ``actionTriggered`` 的参数。
        text: 显示文字（源文本，显示时经 ``tr`` 翻译，上下文为 ``QuickToolbar``）。
        icon: Lucide 图标名。
        shortcut: 快捷键。``QKeySequence.StandardKey`` 由 Qt 按平台映射；
            字符串用可移植写法（如 ``Ctrl+T``），macOS 下 ``Ctrl`` 自动对应 Cmd。
        permission: 使用该操作所需的权限。
    """

    id: str
    text: str
    icon: str
    shortcut: QKeySequence.StandardKey | str
    permission: Permission


DEFAULT_ACTIONS: tuple[QuickAction, ...] = (
    QuickAction(
        "new_experiment",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "新建试验")),
        "circle-plus",
        QKeySequence.StandardKey.New,
        Permission.EXPERIMENT_CREATE,
    ),
    QuickAction(
        "open_file",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "打开文件")),
        "folder-open",
        QKeySequence.StandardKey.Open,
        Permission.EXPERIMENT_VIEW,
    ),
    QuickAction(
        "templates",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "检测模板")),
        "columns-2",
        "Ctrl+T",
        Permission.TEMPLATE_VIEW,
    ),
    QuickAction(
        "print_report",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "打印报告")),
        "printer",
        QKeySequence.StandardKey.Print,
        Permission.REPORT_VIEW,
    ),
    QuickAction(
        "audit_trail",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "审计追踪")),
        "text-align-start",
        "Ctrl+L",
        Permission.AUDIT_VIEW,
    ),
    QuickAction(
        "instrument_settings",
        str(QT_TRANSLATE_NOOP("QuickToolbar", "仪器设置")),
        "settings",
        "Ctrl+,",
        Permission.EXPERIMENT_VIEW,  # 所有角色可以查看设置；修改需要 instrument_configure
    ),
)
"""设计评审确定的 6 个快捷操作，从左到右排列；宽度不足时从右端开始收进“更多”。"""


def native_shortcut_text(shortcut: QKeySequence.StandardKey | str) -> str:
    """把快捷键格式化为当前平台的显示文字（macOS 为 ``⌘N``，其他为 ``Ctrl+N``）。

    Args:
        shortcut: 标准键或可移植字符串。

    Returns:
        平台本地化的快捷键文字。
    """
    return QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)


def _icon(name: str) -> QIcon:
    """生成带禁用态的图标：正常用墨色，禁用用次要文字色。

    Args:
        name: Lucide 图标名。

    Returns:
        图标对象。
    """
    icon = QIcon()
    icon.addPixmap(IconLoader.pixmap(name, ICON_SIZE, "ink"), QIcon.Mode.Normal)
    icon.addPixmap(IconLoader.pixmap(name, ICON_SIZE, "ink_muted"), QIcon.Mode.Disabled)
    return icon


class _StatusChip(QWidget):
    """状态区的一项：图标加文字。"""

    def __init__(self, icon: str, text: str, parent: QWidget | None = None) -> None:
        """创建状态项。

        Args:
            icon: Lucide 图标名。
            text: 文字。
            parent: 父控件。
        """
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._icon = QLabel()
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._text = Label(text, "status")
        self._text.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.set_content(icon, text)

    def set_content(
        self, icon: str, text: str, *, icon_color: str = "ink_muted", tone: str = ""
    ) -> None:
        """更新图标和文字。

        Args:
            icon: Lucide 图标名。
            text: 文字。
            icon_color: 图标颜色 token 名。
            tone: 文字语气，``ok``、``bad`` 或空。
        """
        self._icon.setPixmap(IconLoader.pixmap(icon, ICON_SIZE, icon_color))
        self._text.setText(text)
        self._text.setProperty("tone", tone)
        repolish(self._text)
        self.updateGeometry()


class _MenuChip(QToolButton):
    """状态区里带下拉菜单的一项（语言、当前用户）：图标加文字，点击弹出菜单。"""

    def __init__(self, icon: str, text: str = "", parent: QWidget | None = None) -> None:
        """创建按钮。

        Args:
            icon: Lucide 图标名。
            text: 文字。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "quick")
        self.setProperty("iconOnly", False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(current_tokens().space.touch)
        self.set_content(icon, text)

    def set_content(self, icon: str, text: str) -> None:
        """更新图标和文字。

        Args:
            icon: Lucide 图标名。
            text: 文字。
        """
        self.setIcon(_icon(icon))
        self.setText(text)
        self.updateGeometry()


class QuickToolbar(QFrame):
    """全局常驻的顶部快捷栏，高度 56px。

    从左到右：菜单按钮、竖线、快捷操作、（宽度不足时的）“更多”、弹性空白、
    状态区（仪器连接、语言、当前用户）。

    Signals:
        actionTriggered: 某快捷操作被触发（点击或快捷键），参数为操作 id。
        menuRequested: 点击了菜单按钮，由 AppShell 决定展开或收起侧栏。
        languageSelected: 在语言菜单里选了某种语言，参数为语言代码（如 ``zh_CN``）。
        userActionRequested: 在用户菜单里选了某项，参数为 ``change_password``、
            ``lock`` 或 ``logout``。
    """

    actionTriggered = Signal(str)  # noqa: N815
    menuRequested = Signal()  # noqa: N815
    languageSelected = Signal(str)  # noqa: N815
    userActionRequested = Signal(str)  # noqa: N815

    def __init__(
        self,
        actions: Sequence[QuickAction] = DEFAULT_ACTIONS,
        parent: QWidget | None = None,
    ) -> None:
        """创建快捷栏。创建后没有任何权限，需调用 :meth:`set_user_permissions`。

        Args:
            actions: 快捷操作声明，按显示顺序。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "toolbar")
        self.setFixedHeight(TOOLBAR_HEIGHT)
        self.setAccessibleName(self.tr("快捷操作栏"))
        self._granted: frozenset[Permission] = frozenset()
        self._specs = tuple(actions)
        self._actions: dict[str, QAction] = {}
        self._shortcuts: dict[str, QShortcut] = {}
        self._buttons: dict[str, QToolButton] = {}

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 0, 16, 0)
        self._layout.setSpacing(4)
        self._edge_gap = 16

        self.menu_button = self._make_button()
        self.menu_button.setProperty("iconOnly", True)
        self.menu_button.setIcon(_icon("menu"))
        self.menu_button.setIconSize(QSize(ICON_SIZE + 2, ICON_SIZE + 2))
        self.menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.menu_button.setAccessibleName(self.tr("菜单"))
        self.menu_button.clicked.connect(self.menuRequested)
        self._layout.addWidget(self.menu_button)

        self._sep = QFrame()
        self._sep.setProperty("role", "vsep")
        self._sep.setFixedSize(1, 24)
        self._layout.addSpacing(4)
        self._layout.addWidget(self._sep)
        self._layout.addSpacing(4)

        for spec in self._specs:
            self._add_action(spec)

        self.more_button = self._make_button()
        self.more_button.setText(self.tr("更多"))
        self.more_button.setIcon(_icon("ellipsis"))
        self.more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setAccessibleName(self.tr("更多操作"))
        self.more_menu = QMenu(self.more_button)
        self.more_menu.setToolTipsVisible(True)
        self.more_menu.aboutToShow.connect(self._rebuild_more_menu)
        self.more_button.setMenu(self.more_menu)
        self.more_button.setVisible(False)
        self._layout.addWidget(self.more_button)

        self._layout.addStretch(1)

        self.status_area = QWidget()  # 右侧状态区容器：仪器连接、语言、当前用户
        status_layout = QHBoxLayout(self.status_area)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(16)
        self._instrument = _StatusChip("unplug", "")
        self._language = _MenuChip("languages", "")
        self._user = _MenuChip("user", "")
        for chip in (self._instrument, self._language, self._user):
            status_layout.addWidget(chip)
        self._layout.addWidget(self.status_area)

        self._connected = False
        self._language_name = ""
        self._user_name = ""
        self._user_role = ""
        self._language_group = QActionGroup(self)
        self._language_group.setExclusive(True)
        self._language_menu = QMenu(self._language)
        self._language.setMenu(self._language_menu)
        self._user_menu = QMenu(self._user)
        self._user.setMenu(self._user_menu)
        self._user_actions: dict[str, QAction] = {}
        for action_id, icon_name in (
            ("change_password", "key-round"),
            ("lock", "lock"),
            ("logout", "log-out"),
        ):
            if action_id == "logout":
                self._user_menu.addSeparator()
            entry = self._user_menu.addAction(_icon(icon_name), "")
            entry.triggered.connect(lambda _=False, i=action_id: self.userActionRequested.emit(i))
            self._user_actions[action_id] = entry

        self.set_instrument_status(False)
        self.set_user("", "")
        self.set_menu_hint(self.tr("展开或收起侧栏"))
        self._refresh_actions()
        self._retranslate_static()

    # ------------------------------------------------------------------ 构建

    def _make_button(self) -> QToolButton:
        """创建一个快捷栏按钮（44px 触控高度，键盘聚焦显示焦点环）。

        Returns:
            已设置样式角色的按钮。
        """
        button = QToolButton()
        button.setProperty("role", "quick")
        button.setProperty("iconOnly", False)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setFixedHeight(current_tokens().space.touch)
        return button

    def _add_action(self, spec: QuickAction) -> None:
        """为一个声明创建 QAction、QShortcut 和按钮。

        Args:
            spec: 操作声明。
        """
        action = QAction(self.tr(spec.text), self)
        action.setIcon(_icon(spec.icon))
        action.triggered.connect(lambda _=False, i=spec.id: self.actionTriggered.emit(i))
        self._actions[spec.id] = action

        shortcut = QShortcut(QKeySequence(spec.shortcut), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(lambda a=action: a.trigger() if a.isEnabled() else None)
        self._shortcuts[spec.id] = shortcut

        button = self._make_button()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAccessibleName(action.text())
        self._buttons[spec.id] = button
        self._layout.addWidget(button)

    # ------------------------------------------------------------------ 公共接口

    def action(self, action_id: str) -> QAction:
        """取某个快捷操作的 ``QAction``。

        Args:
            action_id: 操作 id。

        Returns:
            对应的 QAction。

        Raises:
            KeyError: id 不存在。
        """
        return self._actions[action_id]

    def button(self, action_id: str) -> QToolButton:
        """取某个快捷操作的按钮（可能因宽度不足而隐藏）。

        Args:
            action_id: 操作 id。

        Returns:
            对应的按钮。

        Raises:
            KeyError: id 不存在。
        """
        return self._buttons[action_id]

    def action_ids(self) -> tuple[str, ...]:
        """按显示顺序列出全部操作 id。

        Returns:
            操作 id 元组。
        """
        return tuple(spec.id for spec in self._specs)

    def overflow_ids(self) -> tuple[str, ...]:
        """当前收进“更多”菜单的操作 id。

        Returns:
            被隐藏按钮对应的 id，保持显示顺序。
        """
        return tuple(i for i in self.action_ids() if self._buttons[i].isHidden())

    def set_user_permissions(self, permissions: Iterable[Permission]) -> None:
        """设置当前用户的权限，刷新按钮、快捷键与提示。

        Args:
            permissions: 当前用户拥有的权限。无权限的操作按钮禁用，快捷键失效。
        """
        self._granted = frozenset(permissions)
        self._refresh_actions()

    def set_menu_hint(self, text: str) -> None:
        """设置菜单按钮的提示文字，附加平台格式的 ``Ctrl+B``。

        Args:
            text: 说明文字，例如“展开侧栏”。
        """
        full = annotate(text, native_shortcut_text("Ctrl+B"))
        self.menu_button.setToolTip(full)
        self.menu_button.setAccessibleDescription(text)

    def set_instrument_status(self, connected: bool) -> None:
        """更新仪器连接状态。图标与文字同时变化，不只靠颜色区分。

        Args:
            connected: 仪器是否已连接。
        """
        self._connected = connected
        if connected:
            self._instrument.set_content(
                "plug", self.tr("仪器已连接"), icon_color="success", tone="ok"
            )
            self._instrument.setAccessibleName(self.tr("仪器连接状态：已连接"))
        else:
            self._instrument.set_content(
                "unplug", self.tr("仪器未连接"), icon_color="danger", tone="bad"
            )
            self._instrument.setAccessibleName(self.tr("仪器连接状态：未连接"))
        self._after_status_change()

    def set_language(self, text: str) -> None:
        """更新当前界面语言的显示名。

        Args:
            text: 语言名，例如“简体中文”。
        """
        self._language_name = text
        self._language.set_content("languages", text)
        self._language.setAccessibleName(self.tr("界面语言：") + text)
        self._after_status_change()

    def set_language_options(self, options: Sequence[tuple[str, str]], current: str) -> None:
        """设置语言菜单的选项。选项用各语言自己的名字，不翻译。

        Args:
            options: ``(语言代码, 显示名)`` 序列。
            current: 当前语言代码，菜单里对应项打勾，状态区显示它的名字。
        """
        self._language_menu.clear()
        for action in list(self._language_group.actions()):
            self._language_group.removeAction(action)
        for code, name in options:
            entry = self._language_menu.addAction(name)
            entry.setCheckable(True)
            entry.setChecked(code == current)
            self._language_group.addAction(entry)
            entry.triggered.connect(lambda _=False, c=code: self.languageSelected.emit(c))
            if code == current:
                self.set_language(name)

    def set_user(self, name: str, role: str) -> None:
        """更新当前用户。

        Args:
            name: 用户名，空串表示未登录。
            role: 角色显示名。
        """
        self._user_name = name
        self._user_role = role
        label = f"{name} · {role}" if role else name
        text = label if name else self.tr("未登录")
        self._user.set_content("user", text)
        self._user.setAccessibleName(self.tr("当前用户：") + text)
        self._user.setEnabled(bool(name))
        self._after_status_change()

    # ------------------------------------------------------------------ 内部

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新全部文字。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_static()
        super().changeEvent(event)

    def _retranslate_static(self) -> None:
        """按当前语言重写所有文字、提示和无障碍名称（菜单按钮的提示由 AppShell 负责）。"""
        self.setAccessibleName(self.tr("快捷操作栏"))
        self.menu_button.setAccessibleName(self.tr("菜单"))
        self.more_button.setText(self.tr("更多"))
        self.more_button.setAccessibleName(self.tr("更多操作"))
        for spec in self._specs:
            name = self.tr(spec.text)
            self._actions[spec.id].setText(name)
            self._buttons[spec.id].setAccessibleName(name)
        self._refresh_actions()
        self._user_actions["change_password"].setText(self.tr("修改密码"))
        self._user_actions["lock"].setText(self.tr("锁定屏幕"))
        self._user_actions["logout"].setText(self.tr("退出登录"))
        self.set_instrument_status(self._connected)
        self.set_user(self._user_name, self._user_role)
        if self._language_name:
            self.set_language(self._language_name)

    def _refresh_actions(self) -> None:
        """按权限刷新各操作的可用状态与提示。"""
        for spec in self._specs:
            allowed = spec.permission in self._granted
            action = self._actions[spec.id]
            name = self.tr(spec.text)
            action.setEnabled(allowed)
            self._shortcuts[spec.id].setEnabled(allowed)
            if allowed:
                action.setToolTip(annotate(name, native_shortcut_text(spec.shortcut)))
            else:
                action.setToolTip(colon(name, self.tr("当前角色无此权限")))
            self._buttons[spec.id].setToolTip(action.toolTip())

    def _after_status_change(self) -> None:
        """状态区宽度可能变化，重新计算收纳。"""
        self.status_area.updateGeometry()
        self.updateGeometry()
        self._update_overflow()
        self._layout.activate()

    def _rebuild_more_menu(self) -> None:
        """打开“更多”前，用当前被收纳的操作重建菜单。"""
        self.more_menu.clear()
        for action_id in self.overflow_ids():
            spec = next(s for s in self._specs if s.id == action_id)
            source = self._actions[action_id]
            hint = native_shortcut_text(spec.shortcut)
            proxy = self.more_menu.addAction(source.icon(), f"{source.text()}\t{hint}")
            proxy.setEnabled(source.isEnabled())
            proxy.setToolTip(source.toolTip())
            proxy.triggered.connect(lambda _=False, a=source: a.trigger())

    def _fixed_width(self, *, with_more: bool) -> int:
        """左侧固定部分加状态区所需的宽度。

        Args:
            with_more: 是否计入“更多”按钮。

        Returns:
            宽度（px）。
        """
        m = self._layout.contentsMargins()
        sp = self._layout.spacing()
        width = m.left() + m.right() + self.menu_button.sizeHint().width()
        width += 4 + self._sep.width() + 4 + 2 * sp
        width += self.status_area.sizeHint().width() + self._edge_gap
        if with_more:
            width += self.more_button.sizeHint().width() + sp
        return width

    def _update_overflow(self) -> None:
        """根据当前宽度决定哪些操作直接显示，其余收进“更多”。"""
        sp = self._layout.spacing()
        widths = [self._buttons[i].sizeHint().width() for i in self.action_ids()]
        count = len(widths)
        shown = 0
        for k in range(count, -1, -1):
            needed = self._fixed_width(with_more=k < count) + sum(widths[:k]) + sp * k
            if needed <= self.width():
                shown = k
                break
        for index, action_id in enumerate(self.action_ids()):
            self._buttons[action_id].setVisible(index < shown)
        self.more_button.setVisible(shown < count)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小尺寸：菜单按钮、“更多”与状态区仍完整可见。

        Returns:
            最小尺寸。
        """
        return QSize(self._fixed_width(with_more=True), TOOLBAR_HEIGHT)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """宽度变化时重新计算收纳。

        Args:
            event: 尺寸事件。
        """
        super().resizeEvent(event)
        self._update_overflow()
