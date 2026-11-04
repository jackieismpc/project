"""深色侧栏导航：品牌名加图标导航项，选中项反白。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QSizePolicy, QVBoxLayout, QWidget

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


DEFAULT_NAV: tuple[NavEntry, ...] = (
    NavEntry("dashboard", "工作台", "layout-dashboard"),
    NavEntry("new", "新试验", "circle-plus"),
    NavEntry("monitor", "试验监控", "video"),
    NavEntry("history", "历史记录", "history"),
    NavEntry("templates", "模板管理", "columns-2"),
    NavEntry("reagents", "试剂溯源", "flask-conical"),
    NavEntry("reports", "报告中心", "file-text"),
    NavEntry("audit", "审计追踪", "text-align-start"),
    NavEntry("users", "用户与权限", "user"),
    NavEntry("settings", "仪器设置", "settings"),
)
"""设计稿中的 10 个主导航项。"""


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
        self.setText(entry.label)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(current_tokens().space.touch)
        self.setAccessibleName(entry.label)
        self._hover = False

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
        if checked:
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


class SideNav(QFrame):
    """侧栏。点击导航项发出 :attr:`currentChanged`。"""

    currentChanged = Signal(str)  # noqa: N815

    def __init__(
        self,
        entries: Sequence[NavEntry],
        *,
        brand: str = "凝胶智检系统",
        parent: QWidget | None = None,
    ) -> None:
        """创建侧栏。

        Args:
            entries: 导航项，按显示顺序。
            brand: 顶部品牌名。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setFixedWidth(NAV_WIDTH)
        self.setAccessibleName("主导航")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(4)
        title = Label(brand, "brand")
        title.setContentsMargins(12, 4, 12, 18)
        layout.addWidget(title)
        self._items: dict[str, NavItem] = {}
        for entry in entries:
            item = NavItem(entry)
            item.clicked.connect(lambda _=False, k=entry.key: self.set_current(k))
            self._items[entry.key] = item
            layout.addWidget(item)
        layout.addStretch(1)

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
