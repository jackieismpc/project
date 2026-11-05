"""孔与状态标记的绘制，PlateGrid、图例和表格共用。

状态靠形状区分，不只靠颜色：阳性实心、阴性 3px 空心环、待复核下半填充、
读数中实环淡底、孵育中虚线环、空孔细线。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from gelinspect.domain.enums import Interpretation
from gelinspect.ui.theme.tokens import Tokens


class WellState(Enum):
    """孔的显示状态。"""

    EMPTY = "empty"
    """空孔。"""
    INCUBATING = "incubating"
    """孵育中。"""
    READING = "reading"
    """读数中。"""
    POSITIVE = "positive"
    """阳性。"""
    NEGATIVE = "negative"
    """阴性。"""
    REVIEW = "review"
    """待复核。"""
    ERROR = "error"
    """读数或分析失败（没有结果），需要人工处理。图例不列出，只在故障时出现。"""


STATE_LABELS: dict[WellState, str] = {
    WellState.EMPTY: str(QT_TRANSLATE_NOOP("WellState", "空")),
    WellState.INCUBATING: str(QT_TRANSLATE_NOOP("WellState", "孵育中")),
    WellState.READING: str(QT_TRANSLATE_NOOP("WellState", "读数中")),
    WellState.POSITIVE: str(QT_TRANSLATE_NOOP("WellState", "阳性")),
    WellState.NEGATIVE: str(QT_TRANSLATE_NOOP("WellState", "阴性")),
    WellState.REVIEW: str(QT_TRANSLATE_NOOP("WellState", "待复核")),
    WellState.ERROR: str(QT_TRANSLATE_NOOP("WellState", "异常")),
}
"""状态的源文本（简体中文），翻译用 :func:`state_label`。"""


def state_label(state: WellState) -> str:
    """状态的当前语言名称，用于无障碍名称、图例和表格。

    Args:
        state: 孔状态。

    Returns:
        翻译后的名称。
    """
    return QCoreApplication.translate("WellState", STATE_LABELS[state])


def well_state_from_interpretation(value: Interpretation) -> WellState:
    """把判读结果映射为孔的显示状态。

    Args:
        value: 软件侧判读结果。

    Returns:
        对应的 :class:`WellState`。
    """
    return {
        Interpretation.POSITIVE: WellState.POSITIVE,
        Interpretation.NEGATIVE: WellState.NEGATIVE,
        Interpretation.PENDING_REVIEW: WellState.REVIEW,
    }[value]


def paint_well(
    painter: QPainter,
    rect: QRectF,
    state: WellState,
    tokens: Tokens,
    *,
    text: str | None = None,
    font: QFont | None = None,
    selected: bool = False,
    selectable: bool = False,
    hovered: bool = False,
) -> None:
    """绘制一个孔。

    Args:
        painter: 目标画笔，调用前后状态不变。
        rect: 孔的外接正方形。
        state: 显示状态。
        tokens: 主题 token。
        text: 孔内文字（管号），为空则不画字。
        font: 文字字体。
        selected: 选择模式下是否已选中。
        selectable: 是否处于选择模式（空孔使用选择样式）。
        hovered: 鼠标是否悬停。
    """
    c = tokens.colors
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    border_w = 1.0
    dashed = False
    text_color = QColor(c.ink)
    fill = QColor(c.surface_alt)
    border = QColor(c.border)
    if selected:
        fill, border, text_color = QColor(c.primary), QColor(c.primary), QColor(c.on_accent)
    elif state is WellState.EMPTY:
        text_color = QColor(c.ink_muted)
        if selectable:
            border = QColor(c.well_idle_border)
    elif state is WellState.POSITIVE:
        fill, border, text_color = QColor(c.positive), QColor(c.positive), QColor(c.on_accent)
    elif state is WellState.NEGATIVE:
        fill, border, border_w = QColor(c.surface), QColor(c.negative), 3.0
    elif state is WellState.REVIEW:
        fill, border, border_w = QColor(c.surface), QColor(c.review), 2.0
    elif state is WellState.ERROR:
        fill, border, border_w = QColor(c.danger_bg), QColor(c.danger), 2.0
        text_color = QColor(c.danger)
    elif state is WellState.READING:
        fill, border, border_w = QColor(c.primary_tint), QColor(c.primary), 2.0
        text_color = QColor(c.primary_hover)
    elif state is WellState.INCUBATING:
        fill, border, border_w, dashed = QColor(c.surface), QColor(c.primary), 2.0, True
        text_color = QColor(c.primary_hover)
    if hovered and not selected and state is WellState.EMPTY and selectable:
        border = QColor(c.primary)

    outer = QPainterPath()
    outer.addEllipse(rect)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(outer)
    if state is WellState.REVIEW and not selected:
        painter.save()
        painter.setClipPath(outer)
        painter.setBrush(QColor(c.review_soft))
        painter.drawRect(QRectF(rect.left(), rect.center().y(), rect.width(), rect.height() / 2))
        painter.restore()

    inset = border_w / 2
    ring = rect.adjusted(inset, inset, -inset, -inset)
    pen = QPen(border, border_w)
    if dashed:
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setDashPattern([2.4, 1.6])
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(ring)

    if hovered and not selected:
        halo = QColor(c.primary)
        halo.setAlphaF(0.35)
        painter.setPen(QPen(halo, 2.0))
        painter.drawEllipse(rect.adjusted(-2.5, -2.5, 2.5, 2.5))

    if text:
        if font is not None:
            painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
    painter.restore()


def paint_focus_ring(
    painter: QPainter, rect: QRectF, tokens: Tokens, *, offset: float = 2.0
) -> None:
    """在孔外侧绘制键盘焦点环（3px，偏移 2px）。

    Args:
        painter: 目标画笔，调用前后状态不变。
        rect: 孔的外接正方形。
        tokens: 主题 token。
        offset: 焦点环与孔边缘的距离。
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(tokens.colors.focus), 3.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    grow = offset + 1.5
    painter.drawEllipse(QPointF(rect.center()), rect.width() / 2 + grow, rect.height() / 2 + grow)
    painter.restore()
