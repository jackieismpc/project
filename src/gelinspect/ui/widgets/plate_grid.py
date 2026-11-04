"""8×10 孔板 PlateGrid、图例 PlateLegend 与暗底 8 连管 TubeStrip。

管号一律通过 :mod:`gelinspect.domain.plate` 换算：竖向编号，第 1 列（Row01）是 1–8，
第 2 列（Row02）是 9–16，依此类推。孔用 QPainter 自绘，开启抗锯齿，随 Qt 的高 DPI
缩放保持圆滑。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFontMetrics,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.domain.plate import (
    HOLES_PER_ROW,
    ROW_COUNT,
    TUBE_COUNT,
    position_to_tube,
    row_label,
    tube_to_position,
)
from gelinspect.ui.theme.fonts import mono_font, sans_font
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.marks import (
    STATE_LABELS,
    WellState,
    paint_focus_ring,
    paint_well,
)


class PlateMode(Enum):
    """PlateGrid 的交互模式。"""

    DISPLAY = "display"
    """显示：点击发出 ``tubeClicked``。"""
    SELECT = "select"
    """选择：点击切换选中，发出 ``selectionChanged``。"""


_LABEL_H = 18
_GAP_Y = {PlateMode.DISPLAY: 6, PlateMode.SELECT: 4}
_PREFERRED_D = {PlateMode.DISPLAY: 38, PlateMode.SELECT: 44}
_COL_GAP = 6


def well_accessible_name(tube_no: int, state: WellState, *, selected: bool = False) -> str:
    """生成孔的无障碍名称，与设计稿的 ``aria-label`` 一致。

    Args:
        tube_no: 管号，1–80。
        state: 显示状态。
        selected: 选择模式下是否已选中。

    Returns:
        例如 ``7 号管，待复核``。
    """
    suffix = "，已选中" if selected else ""
    return f"{tube_no} 号管，{STATE_LABELS[state]}{suffix}"


class PlateGrid(QWidget):
    """8×10 孔板。

    Signals:
        tubeClicked: 显示模式下点击（或在焦点孔上按空格、回车），参数为管号。
        selectionChanged: 选择模式下选中集合变化，参数为升序的管号列表。
        selectionLimitReached: 已达到选择上限时再选，参数为上限。
    """

    tubeClicked = Signal(int)  # noqa: N815
    selectionChanged = Signal(list)  # noqa: N815
    selectionLimitReached = Signal(int)  # noqa: N815

    def __init__(self, mode: PlateMode = PlateMode.DISPLAY, parent: QWidget | None = None) -> None:
        """创建孔板。

        Args:
            mode: 初始交互模式。
            parent: 父控件。
        """
        super().__init__(parent)
        self._mode = mode
        self._states: dict[int, WellState] = {}
        self._selected: set[int] = set()
        self._countdowns: dict[int, tuple[str, bool]] = {}
        self._current = 1
        self._hover: int | None = None
        self._pressed: int | None = None
        self._kb_focus = False
        self._limit: int | None = None
        self._show_countdown = True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._update_accessible()

    # ------------------------------------------------------------------ 状态

    def mode(self) -> PlateMode:
        """返回当前模式。

        Returns:
            当前 :class:`PlateMode`。
        """
        return self._mode

    def set_mode(self, mode: PlateMode) -> None:
        """切换模式。

        Args:
            mode: 新模式。
        """
        if mode is self._mode:
            return
        self._mode = mode
        self.updateGeometry()
        self.update()

    def set_state(self, tube_no: int, state: WellState) -> None:
        """设置单个孔的显示状态。

        Args:
            tube_no: 管号，1–80。
            state: 显示状态。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        tube_to_position(tube_no)
        self._states[tube_no] = state
        self._update_accessible()
        self.update()

    def set_states(self, states: Mapping[int, WellState]) -> None:
        """整体替换全部孔的状态，未出现的管号视为空孔。

        Args:
            states: 管号到状态的映射。

        Raises:
            InvalidPlatePositionError: 某个管号超出范围。
        """
        for tube_no in states:
            tube_to_position(tube_no)
        self._states = dict(states)
        self._update_accessible()
        self.update()

    def state(self, tube_no: int) -> WellState:
        """查询孔的显示状态。

        Args:
            tube_no: 管号，1–80。

        Returns:
            显示状态，未设置时为 ``EMPTY``。
        """
        return self._states.get(tube_no, WellState.EMPTY)

    def set_countdown(self, row_index: int, text: str, *, active: bool = False) -> None:
        """设置一排的倒计时文字。

        Args:
            row_index: 排号，1–10。
            text: 文字，例如 ``12:48``、``已读数``；空串表示清除。
            active: 是否为当前活动排（主色加粗）。

        Raises:
            InvalidPlatePositionError: 排号超出范围。
        """
        row_label(row_index)
        if text:
            self._countdowns[row_index] = (text, active)
        else:
            self._countdowns.pop(row_index, None)
        self.update()

    def set_countdowns_visible(self, visible: bool) -> None:
        """是否保留倒计时行（选择模式通常关闭）。

        Args:
            visible: 是否显示。
        """
        self._show_countdown = visible
        self.updateGeometry()
        self.update()

    # ------------------------------------------------------------------ 选择

    def selected_tubes(self) -> list[int]:
        """返回选中的管号。

        Returns:
            升序管号列表。
        """
        return sorted(self._selected)

    def set_selected(self, tubes: Iterable[int]) -> None:
        """设置选中集合，超出上限的部分被丢弃，只在变化时发信号。

        Args:
            tubes: 管号集合。

        Raises:
            InvalidPlatePositionError: 某个管号超出范围。
        """
        wanted = sorted(set(tubes))
        for tube_no in wanted:
            tube_to_position(tube_no)
        if self._limit is not None:
            wanted = wanted[: self._limit]
        self._apply_selection(set(wanted))

    def clear_selection(self) -> None:
        """清空选择。"""
        self._apply_selection(set())

    def set_selection_limit(self, limit: int | None) -> None:
        """设置最多可选管数。

        Args:
            limit: 上限，``None`` 表示不限。
        """
        self._limit = limit

    def _apply_selection(self, new: set[int]) -> None:
        """写入选中集合并按需发信号。

        Args:
            new: 新的选中集合。
        """
        if new == self._selected:
            return
        self._selected = new
        self._update_accessible()
        self.update()
        self.selectionChanged.emit(sorted(new))

    def _toggle(self, tube_no: int) -> None:
        """切换某个孔的选中状态。

        Args:
            tube_no: 管号。
        """
        if tube_no in self._selected:
            self._apply_selection(self._selected - {tube_no})
        elif self._limit is not None and len(self._selected) >= self._limit:
            self.selectionLimitReached.emit(self._limit)
        else:
            self._apply_selection(self._selected | {tube_no})

    # ------------------------------------------------------------------ 几何

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            宽度不小于设计稿的 500px。
        """
        d = _PREFERRED_D[self._mode]
        gap = _GAP_Y[self._mode]
        height = self._header_height() + HOLES_PER_ROW * d + (HOLES_PER_ROW - 1) * gap + 2
        return QSize(max(500, ROW_COUNT * (d + _COL_GAP)), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小尺寸，孔缩到约 26px。

        Returns:
            最小尺寸。
        """
        gap = _GAP_Y[self._mode]
        height = self._header_height() + HOLES_PER_ROW * 26 + (HOLES_PER_ROW - 1) * gap + 2
        return QSize(ROW_COUNT * (26 + _COL_GAP), height)

    def _header_height(self) -> int:
        """列头区域高度。

        Returns:
            行标签加（可选）倒计时行的高度。
        """
        height = _LABEL_H + 6
        if self._show_countdown:
            height += _LABEL_H + 6
        return height

    def _metrics(self) -> tuple[float, float, float, float]:
        """计算当前尺寸下的孔径与间距。

        Returns:
            ``(col_w, diameter, stride_y, top)``：列宽、孔径、竖向步长、第一行孔的上沿。
        """
        gap = _GAP_Y[self._mode]
        col_w = self.width() / ROW_COUNT
        top = float(self._header_height())
        by_height = (self.height() - top - 2 - (HOLES_PER_ROW - 1) * gap) / HOLES_PER_ROW
        diameter = max(16.0, min(col_w - _COL_GAP, by_height, _PREFERRED_D[self._mode] * 1.5))
        return col_w, diameter, diameter + gap, top

    def well_rect(self, tube_no: int) -> QRectF:
        """返回某个孔的外接矩形（控件坐标）。

        Args:
            tube_no: 管号，1–80。

        Returns:
            孔的外接正方形。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        pos = tube_to_position(tube_no)
        col_w, d, stride, top = self._metrics()
        x = (pos.row_index - 1) * col_w + (col_w - d) / 2
        y = top + (pos.hole_index - 1) * stride
        return QRectF(x, y, d, d)

    def tube_at(self, point: QPointF) -> int | None:
        """命中检测。

        Args:
            point: 控件坐标。

        Returns:
            命中的管号，未命中孔为 ``None``。
        """
        col_w, d, stride, top = self._metrics()
        row = int(point.x() // col_w) + 1
        if not 1 <= row <= ROW_COUNT or point.y() < top:
            return None
        hole = int((point.y() - top) // stride) + 1
        if not 1 <= hole <= HOLES_PER_ROW:
            return None
        tube_no = position_to_tube(row, hole)
        rect = self.well_rect(tube_no)
        dx, dy = point.x() - rect.center().x(), point.y() - rect.center().y()
        return tube_no if dx * dx + dy * dy <= (d / 2 + 3) ** 2 else None

    # ------------------------------------------------------------------ 交互

    def _activate(self, tube_no: int) -> None:
        """按当前模式触发一次点击。

        Args:
            tube_no: 管号。
        """
        self._current = tube_no
        if self._mode is PlateMode.SELECT:
            self._toggle(tube_no)
        else:
            self.tubeClicked.emit(tube_no)
        self._update_accessible()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """更新悬停孔与光标。

        Args:
            event: 鼠标事件。
        """
        tube = self.tube_at(event.position())
        if tube != self._hover:
            self._hover = tube
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if tube else Qt.CursorShape.ArrowCursor
            )
            self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        """清除悬停。

        Args:
            event: 离开事件。
        """
        self._hover = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """记录按下的孔，鼠标操作不显示键盘焦点环。

        Args:
            event: 鼠标事件。
        """
        self._kb_focus = False
        self._pressed = (
            self.tube_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        )
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """在按下的同一孔上松开时触发点击。

        Args:
            event: 鼠标事件。
        """
        tube = self.tube_at(event.position())
        pressed, self._pressed = self._pressed, None
        if tube is not None and tube == pressed:
            self._activate(tube)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        """键盘（Tab）进入时显示焦点环。

        Args:
            event: 焦点事件。
        """
        self._kb_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        """失焦时去掉焦点环。

        Args:
            event: 焦点事件。
        """
        self._kb_focus = False
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """方向键移动焦点孔，空格或回车触发点击，Home/End 跳到首尾。

        Args:
            event: 键盘事件。
        """
        pos = tube_to_position(self._current)
        row, hole = pos.row_index, pos.hole_index
        key = event.key()
        if key == Qt.Key.Key_Left:
            row = max(1, row - 1)
        elif key == Qt.Key.Key_Right:
            row = min(ROW_COUNT, row + 1)
        elif key == Qt.Key.Key_Up:
            hole = max(1, hole - 1)
        elif key == Qt.Key.Key_Down:
            hole = min(HOLES_PER_ROW, hole + 1)
        elif key == Qt.Key.Key_Home:
            row, hole = 1, 1
        elif key == Qt.Key.Key_End:
            row, hole = ROW_COUNT, HOLES_PER_ROW
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._kb_focus = True
            self._activate(self._current)
            return
        else:
            super().keyPressEvent(event)
            return
        self._kb_focus = True
        self._current = position_to_tube(row, hole)
        self._update_accessible()
        self.update()

    def current_tube(self) -> int:
        """返回键盘焦点所在的管号。

        Returns:
            管号，1–80。
        """
        return self._current

    def set_current_tube(self, tube_no: int) -> None:
        """移动键盘焦点孔。

        Args:
            tube_no: 管号，1–80。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        tube_to_position(tube_no)
        self._current = tube_no
        self._update_accessible()
        self.update()

    def _update_accessible(self) -> None:
        """刷新无障碍名称与描述（读屏软件读出当前孔）。"""
        self.setAccessibleName("孔板 8 乘 10")
        tube = self._current
        self.setAccessibleDescription(
            well_accessible_name(tube, self.state(tube), selected=tube in self._selected)
        )

    # ------------------------------------------------------------------ 绘制

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制列头、倒计时与 80 个孔。

        Args:
            event: 绘制事件。
        """
        tokens = current_tokens()
        c = tokens.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        col_w, d, _, _ = self._metrics()
        label_font = mono_font(12, medium=True)
        for row in range(1, ROW_COUNT + 1):
            cell = QRectF((row - 1) * col_w, 0, col_w, _LABEL_H)
            p.setFont(label_font)
            p.setPen(QColor(c.ink))
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), row_label(row))
            if self._show_countdown and row in self._countdowns:
                text, active = self._countdowns[row]
                p.setFont(mono_font(12, medium=active))
                p.setPen(QColor(c.primary if active else c.ink_muted))
                fm = QFontMetrics(p.font())
                elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(col_w))
                p.drawText(
                    QRectF((row - 1) * col_w, _LABEL_H + 6, col_w, _LABEL_H),
                    int(Qt.AlignmentFlag.AlignCenter),
                    elided,
                )
        num_font = mono_font(max(10, min(15, round(d * 0.32))))
        selectable = self._mode is PlateMode.SELECT
        for tube_no in range(1, TUBE_COUNT + 1):
            paint_well(
                p,
                self.well_rect(tube_no),
                self.state(tube_no),
                tokens,
                text=str(tube_no),
                font=num_font,
                selected=selectable and tube_no in self._selected,
                selectable=selectable,
                hovered=tube_no == self._hover,
            )
        if self.hasFocus() and self._kb_focus:
            paint_focus_ring(p, self.well_rect(self._current), tokens)
        p.end()


class PlateLegend(QWidget):
    """孔板图例：色块加文字，一行排开。"""

    def __init__(
        self,
        states: Sequence[WellState] = (
            WellState.POSITIVE,
            WellState.NEGATIVE,
            WellState.REVIEW,
            WellState.READING,
            WellState.INCUBATING,
        ),
        parent: QWidget | None = None,
    ) -> None:
        """创建图例。

        Args:
            states: 要显示的状态，按顺序排列。
            parent: 父控件。
        """
        super().__init__(parent)
        self._states = list(states)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("图例：" + "、".join(STATE_LABELS[s] for s in self._states))

    def _item_widths(self) -> list[float]:
        """各图例项宽度（圆点 14 + 间距 6 + 文字）。

        Returns:
            宽度列表。
        """
        fm = QFontMetrics(sans_font(12))
        return [14 + 6 + fm.horizontalAdvance(STATE_LABELS[s]) for s in self._states]

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            容纳全部图例项的尺寸。
        """
        widths = self._item_widths()
        return QSize(int(sum(widths) + 16 * max(0, len(widths) - 1)) + 2, 18)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """最小尺寸。

        Returns:
            同建议尺寸。
        """
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制图例。

        Args:
            event: 绘制事件。
        """
        tokens = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(sans_font(12))
        x = 0.0
        for state, width in zip(self._states, self._item_widths(), strict=True):
            paint_well(p, QRectF(x, 2, 14, 14), state, tokens)
            p.setPen(QColor(tokens.colors.ink_muted))
            p.drawText(
                QRectF(x + 20, 0, width - 20 + 4, self.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                STATE_LABELS[state],
            )
            x += width + 16
        p.end()


def _tube_path(rect: QRectF, radius: float) -> QPainterPath:
    """上方平直、下方圆底的试管轮廓。

    Args:
        rect: 试管外框。
        radius: 底部圆角半径。

    Returns:
        闭合路径。
    """
    r = min(radius, rect.width() / 2)
    path = QPainterPath()
    path.moveTo(rect.left(), rect.top())
    path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom() - r)
    path.arcTo(QRectF(rect.right() - 2 * r, rect.bottom() - 2 * r, 2 * r, 2 * r), 0, -90)
    path.lineTo(rect.left() + r, rect.bottom())
    path.arcTo(QRectF(rect.left(), rect.bottom() - 2 * r, 2 * r, 2 * r), 270, -90)
    path.closeSubpath()
    return path


class TubeStrip(QWidget):
    """暗底 8 连管：视频卡中一排试管的示意画面。

    浅色背板上画 8 支玻璃管，管内按比例填充凝胶色；底部两行说明用等宽小字。
    """

    def __init__(
        self,
        levels: Sequence[float] = (),
        *,
        caption_left: str = "",
        caption_right: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """创建试管条。

        Args:
            levels: 8 支管的凝胶高度比例（0–1）。
            caption_left: 左下说明，例如 ``Row02 · 翻转后``。
            caption_right: 右下说明，例如 ``4000×3000 · 15 fps``。
            parent: 父控件。
        """
        super().__init__(parent)
        self._levels = [0.0] * HOLES_PER_ROW
        self._caption = (caption_left, caption_right)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18 + 12 + 112 + 12 + 10 + 16 + 10)
        self.set_levels(levels)
        self.setAccessibleName("当前一排 8 支试管的实时画面")

    def set_levels(self, levels: Sequence[float]) -> None:
        """设置 8 支管的凝胶高度。

        Args:
            levels: 比例序列，不足 8 个的尾部按 0 处理，超出部分忽略；值截断到 0–1。
        """
        padded = [*levels, *([0.0] * HOLES_PER_ROW)][:HOLES_PER_ROW]
        self._levels = [max(0.0, min(1.0, v)) for v in padded]
        self.update()

    def levels(self) -> list[float]:
        """返回当前凝胶高度。

        Returns:
            长度为 8 的比例列表。
        """
        return list(self._levels)

    def set_captions(self, left: str, right: str) -> None:
        """更新底部说明。

        Args:
            left: 左侧文字。
            right: 右侧文字。
        """
        self._caption = (left, right)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """建议尺寸。

        Returns:
            宽 360 的建议尺寸。
        """
        return QSize(360, self.height())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制暗底、背板、试管和说明。

        Args:
            event: 绘制事件。
        """
        c = current_tokens().colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.video_bg))
        p.drawRoundedRect(outer, 6, 6)

        strip = QRectF(14, 18, outer.width() - 28, 12 + 112 + 12)
        grad = QLinearGradient(strip.topLeft(), strip.bottomLeft())
        grad.setColorAt(0, QColor(c.strip_top))
        grad.setColorAt(1, QColor(c.strip_bottom))
        p.setBrush(grad)
        p.drawRoundedRect(strip, 3, 3)

        inner = strip.adjusted(8, 12, -8, -12)
        gap = 6.0
        tube_w = (inner.width() - gap * (HOLES_PER_ROW - 1)) / HOLES_PER_ROW
        for i, level in enumerate(self._levels):
            rect = QRectF(inner.left() + i * (tube_w + gap), inner.top(), tube_w, inner.height())
            path = _tube_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14)
            glass = QColor(255, 255, 255)
            glass.setAlphaF(0.35)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glass)
            p.drawPath(path)
            if level > 0:
                gel = QColor(c.gel)
                gel.setAlphaF(0.62)
                p.save()
                p.setClipPath(path)
                height = rect.height() * level
                p.fillRect(QRectF(rect.left(), rect.bottom() - height, rect.width(), height), gel)
                p.restore()
            p.setPen(QPen(QColor(c.tube_edge), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        p.setFont(mono_font(12))
        p.setPen(QColor(c.video_text))
        caption = QRectF(14, strip.bottom() + 10, outer.width() - 28, 16)
        p.drawText(
            caption,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._caption[0],
        )
        p.drawText(
            caption,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            self._caption[1],
        )
        p.end()
