"""向导步骤胶囊：已完成、当前、未到达三种样式。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.ui.theme.fonts import mono_font, sans_font
from gelinspect.ui.theme.tokens import current_tokens

_HEIGHT = 40
_GAP = 8
_NO = 24


class StepIndicator(QWidget):
    """步骤条，每步一个胶囊，左侧是序号圆。"""

    def __init__(
        self, steps: Sequence[str], current: int = 0, parent: QWidget | None = None
    ) -> None:
        """创建步骤条。

        Args:
            steps: 各步名称。
            current: 当前步骤序号（从 0 开始）。
            parent: 父控件。
        """
        super().__init__(parent)
        self._steps = list(steps)
        self._current = current
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._refresh_accessible()

    def current(self) -> int:
        """返回当前步骤序号。

        Returns:
            从 0 开始的序号。
        """
        return self._current

    def set_current(self, index: int) -> None:
        """切换当前步骤。

        Args:
            index: 从 0 开始的序号。
        """
        self._current = max(0, min(index, len(self._steps) - 1))
        self._refresh_accessible()
        self.update()

    def _refresh_accessible(self) -> None:
        """刷新无障碍名称。"""
        if self._steps:
            self.setAccessibleName(
                self.tr("步骤 {no} / {total}：{name}").format(
                    no=self._current + 1, total=len(self._steps), name=self._steps[self._current]
                )
            )

    def set_steps(self, steps: Sequence[str]) -> None:
        """替换步骤文字（语言切换后由使用方调用）。

        Args:
            steps: 新的步骤名称，数量应与创建时一致。
        """
        self._steps = list(steps)
        self._current = max(0, min(self._current, len(self._steps) - 1))
        self._refresh_accessible()
        self.updateGeometry()
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新无障碍名称。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self._refresh_accessible()
        super().changeEvent(event)

    def _widths(self) -> list[float]:
        """计算每个胶囊的宽度。

        Returns:
            各胶囊宽度。
        """
        fm = QFontMetrics(sans_font(13))
        return [8 + _NO + 8 + fm.horizontalAdvance(s) + 14 + 2 for s in self._steps]

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            容纳全部胶囊的尺寸。
        """
        widths = self._widths()
        total = sum(widths) + _GAP * max(0, len(widths) - 1)
        return QSize(int(total) + 1, _HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小尺寸，与建议尺寸相同。

        Returns:
            最小尺寸。
        """
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制胶囊。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 0.0
        for i, (name, width) in enumerate(zip(self._steps, self._widths(), strict=True)):
            pill = QRectF(x, 0, width, _HEIGHT)
            if i == self._current:
                bg, fg, no_fg = QColor(c.primary), QColor(c.on_accent), QColor(c.primary)
            elif i < self._current:
                bg, fg, no_fg = QColor(c.primary_soft), QColor(c.primary_hover), QColor(c.ink)
            else:
                bg, fg, no_fg = QColor(c.step_idle), QColor(c.ink_muted), QColor(c.ink_muted)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(pill, _HEIGHT / 2, _HEIGHT / 2)
            circle = QRectF(x + 8, (_HEIGHT - _NO) / 2, _NO, _NO)
            p.setBrush(QColor(c.surface))
            p.drawEllipse(circle)
            p.setFont(mono_font(12))
            p.setPen(no_fg)
            p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), str(i + 1))
            font = sans_font(13)
            if i == self._current:
                font.setWeight(font.Weight.Medium)
            p.setFont(font)
            p.setPen(fg)
            p.drawText(
                QRectF(x + 8 + _NO + 8, 0, width - 8 - _NO - 8, _HEIGHT),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                name,
            )
            x += width + _GAP
        p.end()
