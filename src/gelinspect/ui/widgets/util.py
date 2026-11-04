"""组件内部的小工具。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


def repolish(widget: QWidget) -> None:
    """动态属性变化后让 QSS 重新生效。

    Args:
        widget: 需要刷新样式的控件。
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
