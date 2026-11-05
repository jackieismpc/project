"""告警横幅栈：温度越限、视频断流、数据库忙、算法失败等，按键去重，可关闭。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.util import repolish

MAX_BANNERS = 4
"""同时显示的横幅数，更多的合并为「另有 N 条告警」。"""


class _Banner(QFrame):
    dismissed = Signal(str)

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setProperty("role", "banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(10)
        self._icon = Label()
        self._icon.setFixedSize(22, 22)
        self.text = Label("", "body")
        self.text.setWordWrap(True)
        self.close_button = Button("", ButtonVariant.SECONDARY, icon="x")
        self.close_button.setFixedSize(36, 36)
        self.close_button.setMinimumHeight(36)
        self.close_button.clicked.connect(lambda: self.dismissed.emit(self.key))
        row.addWidget(self._icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.close_button)

    def set_content(self, tone: str, text: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)
        icon, color = {
            "danger": ("circle-x", "danger"),
            "warning": ("triangle-alert", "warning"),
        }.get(tone, ("info", "primary"))
        self._icon.setPixmap(IconLoader.pixmap(icon, 20, color))
        self.text.setText(text)
        self.setAccessibleName(text)


class AlertStack(QWidget):
    """纵向排列的告警横幅。

    Signals:
        changed: 横幅集合变化。
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建横幅栈。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(8)
        self._items: dict[str, tuple[str, str]] = {}
        self._widgets: dict[str, _Banner] = {}
        self._more = Label("", "meta")
        self._box.addWidget(self._more)
        self._more.setVisible(False)
        self.setVisible(False)

    def post(self, key: str, tone: str, text: str) -> None:
        """显示或更新一条告警；同一个 key 只保留最新的，并排到最后。

        Args:
            key: 去重键。
            tone: ``danger``、``warning`` 或 ``info``。
            text: 说明。
        """
        self._items.pop(key, None)
        self._items[key] = (tone, text)
        self._rebuild()

    def clear_key(self, key: str) -> None:
        """撤销一条告警（状况已恢复）。

        Args:
            key: 去重键。
        """
        if self._items.pop(key, None) is not None:
            self._rebuild()

    def clear(self) -> None:
        """撤销全部告警。"""
        if self._items:
            self._items.clear()
            self._rebuild()

    def active_keys(self) -> list[str]:
        """当前告警的键，旧的在前。

        Returns:
            键列表。
        """
        return list(self._items)

    def text_of(self, key: str) -> str:
        """某条告警的文字。

        Args:
            key: 去重键。

        Returns:
            文字，不存在时为空串。
        """
        return self._items.get(key, ("", ""))[1]

    def visible_count(self) -> int:
        """正在显示的横幅数。

        Returns:
            数量。
        """
        return len(self._widgets)

    def _rebuild(self) -> None:
        shown = list(self._items)[-MAX_BANNERS:]
        for key in list(self._widgets):
            if key not in shown:
                widget = self._widgets.pop(key)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for index, key in enumerate(shown):
            banner = self._widgets.get(key)
            if banner is None:
                banner = _Banner(key)
                banner.dismissed.connect(self.clear_key)
                self._widgets[key] = banner
            tone, text = self._items[key]
            banner.set_content(tone, text)
            self._box.insertWidget(index, banner)
            banner.show()
        hidden = len(self._items) - len(shown)
        self._more.setText(self.tr("另有 {n} 条告警").format(n=hidden) if hidden else "")
        self._more.setVisible(bool(hidden))
        self.setVisible(bool(self._items))
        self.changed.emit()
