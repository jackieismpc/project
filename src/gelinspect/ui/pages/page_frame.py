"""页面的公共骨架：可滚动的内容列，以及“标签加输入”的小块。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from gelinspect.ui.widgets.label import Label


def build_page_column(owner: QWidget) -> tuple[QVBoxLayout, QScrollArea]:
    """给页面装上滚动区，并返回放内容的纵向布局（边距 24/20，间距 14）。

    Args:
        owner: 页面本身。

    Returns:
        ``(内容布局, 滚动区)``。
    """
    owner.setProperty("role", "root")
    outer = QVBoxLayout(owner)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(scroll.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(scroll)
    page = QWidget()
    page.setProperty("role", "root")
    scroll.setWidget(page)
    column = QVBoxLayout(page)
    column.setContentsMargins(24, 20, 24, 28)
    column.setSpacing(14)
    return column, scroll


def labeled(label: Label, field: QWidget) -> QVBoxLayout:
    """标签在上、输入在下的一个字段。

    Args:
        label: 标签。
        field: 输入控件。

    Returns:
        放好两者的布局。
    """
    box = QVBoxLayout()
    box.setSpacing(4)
    box.addWidget(label)
    box.addWidget(field)
    return box
