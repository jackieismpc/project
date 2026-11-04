"""下划线标签栏：最小高度 44px，选中项主色加粗加 3px 下划线。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QTabBar, QWidget

from gelinspect.ui.widgets.util import repolish


class Tabs(QFrame):
    """标签栏。内容区由使用方自行切换（例如 ``QStackedWidget``）。"""

    currentChanged = Signal(int)  # noqa: N815

    def __init__(self, labels: Sequence[str] = (), parent: QWidget | None = None) -> None:
        """创建标签栏。

        Args:
            labels: 初始标签文字。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "tabs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._bar = QTabBar()
        self._bar.setExpanding(False)
        self._bar.setDrawBase(False)
        self._bar.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bar.installEventFilter(self)
        self._bar.currentChanged.connect(self.currentChanged)
        layout.addWidget(self._bar)
        layout.addStretch(1)
        for label in labels:
            self.add_tab(label)

    def add_tab(self, label: str) -> int:
        """追加一个标签。

        Args:
            label: 标签文字。

        Returns:
            新标签的序号。
        """
        return self._bar.addTab(label)

    def count(self) -> int:
        """返回标签数。

        Returns:
            标签数量。
        """
        return self._bar.count()

    def current(self) -> int:
        """返回选中序号。

        Returns:
            当前序号，没有标签时为 -1。
        """
        return self._bar.currentIndex()

    def set_current(self, index: int) -> None:
        """选中某个标签。

        Args:
            index: 标签序号。
        """
        self._bar.setCurrentIndex(index)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """键盘聚焦时给选中标签加底色，鼠标点击不触发。

        Args:
            watched: 被监视对象。
            event: 事件。

        Returns:
            始终为 ``False``，不拦截事件。
        """
        if watched is self._bar and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            focused = event.type() == QEvent.Type.FocusIn
            self._bar.setProperty("kbfocus", focused)
            repolish(self._bar)
        return False
