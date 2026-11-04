"""趋势曲线：范围带、虚线基准、面积、折线与端点。

用 QPainter 绘制，不依赖 pyqtgraph，便于离屏截图和高 DPI 渲染一致。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.ui.theme.fonts import mono_font
from gelinspect.ui.theme.tokens import current_tokens


class Sparkline(QWidget):
    """带范围带的趋势曲线。

    Y 轴取 ``y_range``，``band`` 是允许范围（如 37 ± 1 ℃），用淡底标出；
    折线末尾画实心端点。
    """

    def __init__(
        self,
        values: Sequence[float] = (),
        *,
        y_range: tuple[float, float] = (35.6, 38.4),
        band: tuple[float, float] | None = (36.0, 38.0),
        ticks: Sequence[float] = (38.0, 37.0, 36.0),
        x_labels: tuple[str, str] = ("60 分钟前", "现在"),
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建曲线。

        Args:
            values: 按时间升序的采样值。
            y_range: Y 轴显示范围（下限，上限）。
            band: 允许范围带，``None`` 不画。
            ticks: Y 轴刻度，同时画中间刻度的虚线基准。
            x_labels: 左右两端的时间说明。
            compact: 紧凑模式，不画坐标文字。
            parent: 父控件。
        """
        super().__init__(parent)
        self._values: list[float] = list(values)
        self._y_range = y_range
        self._band = band
        self._ticks = tuple(ticks)
        self._x_labels = x_labels
        self._compact = compact
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(60 if compact else 132)
        self.setMinimumWidth(160)
        self._update_accessible()

    def set_values(self, values: Sequence[float]) -> None:
        """更新采样值并重绘。

        Args:
            values: 按时间升序的采样值。
        """
        self._values = list(values)
        self._update_accessible()
        self.update()

    def values(self) -> list[float]:
        """返回当前采样值。

        Returns:
            采样值副本。
        """
        return list(self._values)

    def _update_accessible(self) -> None:
        """刷新无障碍描述。"""
        if self._values:
            self.setAccessibleName(
                f"趋势曲线，最近值 {self._values[-1]:.1f}，"
                f"范围 {min(self._values):.1f} 到 {max(self._values):.1f}"
            )
        else:
            self.setAccessibleName("趋势曲线，暂无数据")

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            与设计稿 640×132 一致的建议尺寸。
        """
        return QSize(640, 60 if self._compact else 132)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制曲线。

        Args:
            event: 绘制事件。
        """
        tokens = current_tokens()
        c = tokens.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left = 4.0 if self._compact else 44.0
        bottom_pad = 4.0 if self._compact else 24.0
        plot = QRectF(left, 4, self.width() - left - 4, self.height() - 4 - bottom_pad)
        lo, hi = self._y_range

        def y_of(value: float) -> float:
            return float(plot.bottom() - (value - lo) / (hi - lo) * plot.height())

        if self._band is not None:
            top, bot = y_of(self._band[1]), y_of(self._band[0])
            p.fillRect(QRectF(plot.left(), top, plot.width(), bot - top), QColor(c.plot_band))
        if len(self._ticks) >= 3 and not self._compact:
            guide = QPen(QColor(c.plot_guide), 1)
            guide.setDashPattern([3, 4])
            p.setPen(guide)
            gy = y_of(self._ticks[len(self._ticks) // 2])
            p.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))

        if len(self._values) >= 2:
            step = plot.width() / (len(self._values) - 1)
            pts = [QPointF(plot.left() + i * step, y_of(v)) for i, v in enumerate(self._values)]
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            area = QPainterPath(line)
            area.lineTo(QPointF(pts[-1].x(), plot.bottom()))
            area.lineTo(QPointF(pts[0].x(), plot.bottom()))
            area.closeSubpath()
            fill = QColor(c.primary)
            fill.setAlphaF(0.12)
            p.fillPath(area, fill)
            pen = QPen(QColor(c.primary), 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.primary))
            end = pts[-1]
            p.drawEllipse(end, 4, 4)

        if not self._compact:
            p.setFont(mono_font(11))
            p.setPen(QColor(c.ink_muted))
            for tick in self._ticks:
                ty = y_of(tick)
                p.drawText(
                    QRectF(0, ty - 8, left - 6, 16),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    f"{tick:g}",
                )
            label_rect = QRectF(plot.left(), plot.bottom() + 4, plot.width(), 16)
            p.drawText(label_rect, int(Qt.AlignmentFlag.AlignLeft), self._x_labels[0])
            p.drawText(label_rect, int(Qt.AlignmentFlag.AlignRight), self._x_labels[1])
        p.end()
