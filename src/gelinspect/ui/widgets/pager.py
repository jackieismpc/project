"""分页条：上一页、下一页和“共 N 条 · 第 x/y 页”。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label


def page_count(total: int, page_size: int) -> int:
    """总页数，没有记录时也算 1 页（显示空状态的那一页）。

    Args:
        total: 总条数。
        page_size: 每页条数。

    Returns:
        不小于 1 的页数。
    """
    return max(1, -(-total // page_size))


class Pager(QWidget):
    """分页条。只显示和发信号，翻页的查询由页面做。

    Signals:
        pageRequested: 用户点了上一页或下一页，参数为目标页（从 0 起）。
    """

    pageRequested = Signal(int)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建分页条。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._page = 0
        self._total = 0
        self._page_size = 1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.summary = Label("", "meta")
        self.prev_button = Button("", ButtonVariant.SECONDARY)
        self.next_button = Button("", ButtonVariant.SECONDARY)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        layout.addWidget(self.prev_button)
        layout.addWidget(self.next_button)
        self.prev_button.clicked.connect(lambda: self.pageRequested.emit(self._page - 1))
        self.next_button.clicked.connect(lambda: self.pageRequested.emit(self._page + 1))
        self.retranslate()
        self._sync()

    @property
    def page(self) -> int:
        """当前页（从 0 起）。"""
        return self._page

    @property
    def pages(self) -> int:
        """总页数（至少 1）。"""
        return page_count(self._total, self._page_size)

    def set_state(self, *, page: int, total: int, page_size: int) -> None:
        """设置当前页和总数。

        Args:
            page: 当前页（从 0 起）。
            total: 满足条件的总条数。
            page_size: 每页条数。
        """
        self._page = page
        self._total = total
        self._page_size = page_size
        self._sync()

    def _sync(self) -> None:
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page + 1 < self.pages)
        self.summary.setText(
            self.tr("共 {total} 条 · 第 {page}/{pages} 页").format(
                total=self._total, page=self._page + 1, pages=self.pages
            )
        )

    def retranslate(self) -> None:
        """按当前语言刷新文字。"""
        self.prev_button.setText(self.tr("上一页"))
        self.next_button.setText(self.tr("下一页"))
        self._sync()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
