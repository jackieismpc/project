"""孔板缩略图：96 个 16px 小孔，每排一竖列，工作台的“进行中的试验”使用。"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from gelinspect.domain.plate import HOLES_PER_ROW, ROW_COUNT, TUBE_COUNT, tube_to_position
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.marks import WellState, paint_well, state_label

_DOT = 16
_GAP = 4


class MiniPlate(QWidget):
    """不可交互的孔板缩略图。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建缩略图。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._states: dict[int, WellState] = {}
        width = ROW_COUNT * _DOT + (ROW_COUNT - 1) * _GAP
        height = HOLES_PER_ROW * _DOT + (HOLES_PER_ROW - 1) * _GAP
        self.setFixedSize(QSize(width, height))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_states(self, states: Mapping[int, WellState]) -> None:
        """整体替换孔状态，未出现的管号为空孔。

        Args:
            states: 管号到状态的映射。
        """
        self._states = dict(states)
        counts: dict[WellState, int] = {}
        for state in self._states.values():
            counts[state] = counts.get(state, 0) + 1
        self.setAccessibleName(
            self.tr("孔板缩略图：")
            + self.tr("、").join(f"{state_label(s)} {n}" for s, n in counts.items())
        )
        self.update()

    def state(self, tube_no: int) -> WellState:
        """查询孔状态。

        Args:
            tube_no: 管号。

        Returns:
            显示状态，未设置为 ``EMPTY``。
        """
        return self._states.get(tube_no, WellState.EMPTY)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """绘制全部小孔。

        Args:
            event: 绘制事件。
        """
        tokens = current_tokens()
        p = QPainter(self)
        for tube_no in range(1, TUBE_COUNT + 1):
            pos = tube_to_position(tube_no)
            rect = QRectF(
                (pos.row_index - 1) * (_DOT + _GAP),
                (pos.hole_index - 1) * (_DOT + _GAP),
                _DOT,
                _DOT,
            )
            paint_well(p, rect, self.state(tube_no), tokens)
        p.end()
