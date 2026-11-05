"""细进度条：8px 高，主色填充。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.ui.theme.tokens import current_tokens


class ProgressBar(QWidget):
    """进度条（0–1）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建进度条。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._value = 0.0
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @property
    def value(self) -> float:
        """当前进度（0–1）。"""
        return self._value

    def set_value(self, value: float) -> None:
        """设置进度。

        Args:
            value: 0–1，超出范围时截断。
        """
        self._value = max(0.0, min(1.0, value))
        self.setAccessibleName(f"{round(self._value * 100)}%")
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            尺寸。
        """
        return QSize(200, 8)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制轨道和填充。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.track))
        p.drawRoundedRect(QRectF(self.rect()), 4, 4)
        p.setBrush(QColor(c.primary))
        p.drawRoundedRect(QRectF(0, 0, self.width() * self._value, self.height()), 4, 4)
        p.end()
