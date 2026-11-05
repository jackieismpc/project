"""深色侧栏导航：品牌名加图标导航项，选中项反白。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.ui.punctuation import colon
from gelinspect.ui.theme.fonts import sans_font
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label

NAV_WIDTH = 208
"""侧栏宽度（px），与设计稿一致。"""


@dataclass(frozen=True, slots=True)
class NavEntry:
    """一个导航项。

    Attributes:
        key: 稳定标识，作为 ``currentChanged`` 的参数。
        label: 显示文字。
        icon: Lucide 图标名。
    """

    key: str
    label: str
    icon: str


def _nav(key: str, label: str, icon: str) -> NavEntry:
    return NavEntry(key, label, icon)


DEFAULT_NAV: tuple[NavEntry, ...] = (
    _nav("dashboard", str(QT_TRANSLATE_NOOP("SideNav", "工作台")), "layout-dashboard"),
    _nav("new", str(QT_TRANSLATE_NOOP("SideNav", "新试验")), "circle-plus"),
    _nav("monitor", str(QT_TRANSLATE_NOOP("SideNav", "试验监控")), "video"),
    _nav("history", str(QT_TRANSLATE_NOOP("SideNav", "历史记录")), "history"),
    _nav("templates", str(QT_TRANSLATE_NOOP("SideNav", "模板管理")), "columns-2"),
    _nav("reagents", str(QT_TRANSLATE_NOOP("SideNav", "试剂溯源")), "flask-conical"),
    _nav("reports", str(QT_TRANSLATE_NOOP("SideNav", "报告中心")), "file-text"),
    _nav("audit", str(QT_TRANSLATE_NOOP("SideNav", "审计追踪")), "text-align-start"),
    _nav("users", str(QT_TRANSLATE_NOOP("SideNav", "用户与权限")), "user"),
    _nav("settings", str(QT_TRANSLATE_NOOP("SideNav", "仪器设置")), "settings"),
    _nav("about", str(QT_TRANSLATE_NOOP("SideNav", "关于")), "info"),
)
"""设计稿中的 10 个主导航项，加上「关于」。label 是简体中文源文本，显示时经翻译。"""


class NavItem(QAbstractButton):
    """自绘导航项：图标 18px，文字 14px，高度 44px。"""

    def __init__(self, entry: NavEntry, parent: QWidget | None = None) -> None:
        """创建导航项。

        Args:
            entry: 导航项定义。
            parent: 父控件。
        """
        super().__init__(parent)
        self.entry = entry
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(current_tokens().space.touch)
        self._hover = False
        self._denied_reason = ""
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新文字、无障碍名称与提示。"""
        label = QCoreApplication.translate("SideNav", self.entry.label)
        self.setText(label)
        self.setAccessibleName(label)
        self.setToolTip(self._denied_reason and colon(label, self._denied_reason))

    def set_denied(self, denied: bool) -> None:
        """标记当前角色无权进入：置灰并禁用，提示原因。

        Args:
            denied: True 表示无权限。
        """
        self._denied_reason = (
            QCoreApplication.translate("SideNav", "当前角色无此权限") if denied else ""
        )
        self.setEnabled(not denied)
        self.retranslate()
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.set_denied(bool(self._denied_reason))
        super().changeEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            宽度由布局决定，高度 44px。
        """
        return QSize(NAV_WIDTH - 24, current_tokens().space.touch)

    def enterEvent(self, event: object) -> None:  # noqa: N802
        """记录悬停。

        Args:
            event: 进入事件。
        """
        self._hover = True
        self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        """清除悬停。

        Args:
            event: 离开事件。
        """
        self._hover = False
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """上下方向键在导航项间移动焦点。

        Args:
            event: 键盘事件。
        """
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.focusNextPrevChild(event.key() == Qt.Key.Key_Down)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制导航项。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        checked = self.isChecked()
        if not self.isEnabled():
            bg, fg = QColor(c.sidebar), QColor(c.sidebar_text)
            fg.setAlpha(110)
        elif checked:
            bg, fg = QColor(c.bg), QColor(c.sidebar)
        elif self._hover:
            bg, fg = QColor(c.sidebar_hover), QColor(c.on_accent)
        else:
            bg, fg = QColor(c.sidebar), QColor(c.sidebar_text)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 6, 6)
        icon_color = fg.name()
        pix = IconLoader.pixmap(self.entry.icon, 18, icon_color)
        p.setOpacity(fg.alphaF())
        p.drawPixmap(12, int((rect.height() - 18) / 2), pix)
        weight = sans_font(14)
        if checked:
            weight.setWeight(weight.Weight.Medium)
        p.setFont(weight)
        p.setPen(fg)
        p.drawText(
            QRectF(12 + 18 + 10, 0, rect.width() - 52, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.text(),
        )
        if self.hasFocus():
            p.setPen(QPen(QColor(c.focus), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        p.end()


class PinButton(QAbstractButton):
    """品牌行右侧的固定按钮：44px 触控目标，深色底上的图标按钮。

    未固定时显示 ``pin``（点击固定），已固定时显示 ``pin-off``（点击取消固定）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建按钮，初始为未固定。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        side = current_tokens().space.touch
        self.setFixedSize(side, side)
        self._hover = False
        self._pinned = False
        self.set_pinned(False)

    def set_pinned(self, pinned: bool) -> None:
        """更新图标与无障碍名称。

        Args:
            pinned: 侧栏当前是否为固定模式。
        """
        self._pinned = pinned
        text = self.tr("取消固定侧栏") if pinned else self.tr("固定侧栏")
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新提示。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.set_pinned(self._pinned)
        super().changeEvent(event)

    def enterEvent(self, event: object) -> None:  # noqa: N802
        """记录悬停。

        Args:
            event: 进入事件。
        """
        self._hover = True
        self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        """清除悬停。

        Args:
            event: 离开事件。
        """
        self._hover = False
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制圆角底色、图标与焦点环。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.sidebar_hover if self._hover else c.sidebar))
        p.drawRoundedRect(rect, 6, 6)
        pix = IconLoader.pixmap("pin-off" if self._pinned else "pin", 18, c.sidebar_text)
        p.drawPixmap(int((rect.width() - 18) / 2), int((rect.height() - 18) / 2), pix)
        if self.hasFocus():
            p.setPen(QPen(QColor(c.focus), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        p.end()


class SideNav(QFrame):
    """侧栏。点击导航项发出 :attr:`currentChanged`（选中变化）与 :attr:`itemActivated`（每次点击）。

    Signals:
        currentChanged: 选中项变化，参数为导航项 key。
        itemActivated: 用户点击了某项（含已选中项），参数为导航项 key。
        pinToggled: 用户点了品牌行的固定按钮。
    """

    currentChanged = Signal(str)  # noqa: N815
    itemActivated = Signal(str)  # noqa: N815
    pinToggled = Signal()  # noqa: N815

    def __init__(
        self,
        entries: Sequence[NavEntry],
        *,
        brand: str | None = None,
        pinnable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建侧栏。

        Args:
            entries: 导航项，按显示顺序。
            brand: 顶部品牌名，``None`` 使用「凝胶智检系统」（随语言切换）。
            pinnable: 品牌行右侧是否显示固定按钮（由 AppShell 使用）。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setFixedWidth(NAV_WIDTH)
        self.setAccessibleName(self.tr("主导航"))
        self._custom_brand = brand
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(4)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(12, 0, 0, 14)
        brand_row.setSpacing(0)
        title = Label(brand or self.tr("凝胶智检系统"), "brand")
        self._brand_label = title
        brand_row.addWidget(title, 1)
        self.pin_button = PinButton()
        self.pin_button.clicked.connect(self.pinToggled)
        self.pin_button.setVisible(pinnable)
        brand_row.addWidget(self.pin_button)
        brand_row.setAlignment(self.pin_button, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(brand_row)
        self._items: dict[str, NavItem] = {}
        for entry in entries:
            item = NavItem(entry)
            item.clicked.connect(lambda _=False, k=entry.key: self._on_clicked(k))
            self._items[entry.key] = item
            layout.addWidget(item)
        layout.addStretch(1)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新品牌名和无障碍名称。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.setAccessibleName(self.tr("主导航"))
            if self._custom_brand is None:
                self._brand_label.setText(self.tr("凝胶智检系统"))
        super().changeEvent(event)

    def set_denied(self, denied_keys: Sequence[str]) -> None:
        """按权限置灰导航项。

        Args:
            denied_keys: 当前角色无权进入的导航项 key，其余恢复可用。
        """
        denied = set(denied_keys)
        for key, item in self._items.items():
            item.set_denied(key in denied)

    def _on_clicked(self, key: str) -> None:
        """处理点击：先更新选中，再通知激活。

        Args:
            key: 导航项标识。
        """
        self.set_current(key)
        self.itemActivated.emit(key)

    def set_pinned(self, pinned: bool) -> None:
        """同步固定按钮的图标与名称。

        Args:
            pinned: 是否处于固定模式。
        """
        self.pin_button.set_pinned(pinned)

    def items(self) -> list[NavItem]:
        """按显示顺序返回全部导航项（供焦点管理与测试使用）。

        Returns:
            导航项列表。
        """
        return list(self._items.values())

    def current(self) -> str | None:
        """返回选中项的标识。

        Returns:
            选中项 key；没有选中时为 ``None``。
        """
        for key, item in self._items.items():
            if item.isChecked():
                return key
        return None

    def set_current(self, key: str) -> None:
        """选中某一项。

        Args:
            key: 导航项标识。

        Raises:
            KeyError: 标识不存在。
        """
        item = self._items[key]
        changed = self.current() != key
        for other in self._items.values():
            other.setChecked(other is item)
        if changed:
            self.currentChanged.emit(key)
