"""读数类卡片：StatCard 与大号温度读数的 ReadoutCard。"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.sparkline import Sparkline


class StatCard(Card):
    """标签加数值的统计卡片，数值使用等宽字体。"""

    def __init__(
        self,
        label: str,
        value: str,
        *,
        unit: str = "",
        note: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """创建统计卡片。

        Args:
            label: 左上角说明。
            value: 数值文字。
            unit: 单位，跟在数值后。
            note: 底部说明。
            parent: 父控件。
        """
        super().__init__(parent=parent)
        self._label = Label(label, "lab")
        self._value = Label("", "stat", mono=True)
        self._note = Label(note, "meta")
        self.body.setSpacing(2)
        self.body.addWidget(self._label)
        self.body.addWidget(self._value)
        self.body.addWidget(self._note)
        self._note.setVisible(bool(note))
        self.set_value(value, unit)

    def set_value(self, value: str, unit: str = "") -> None:
        """更新数值。

        Args:
            value: 数值文字。
            unit: 单位。
        """
        self._value.setText(f"{value} {unit}".strip())
        self.setAccessibleName(f"{self._label.text()} {self._value.text()}")


class ReadoutCard(Card):
    """大号读数卡片，可带趋势曲线（如检测室温度）。"""

    def __init__(
        self,
        label: str,
        value: str,
        unit: str,
        *,
        note: str = "",
        note_role: str = "meta",
        sparkline: Sparkline | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """创建读数卡片。

        Args:
            label: 顶部说明。
            value: 读数文字，例如 ``37.0``。
            unit: 单位，例如 ``℃``。
            note: 读数下方说明。
            note_role: 说明的样式角色。
            sparkline: 右侧趋势曲线。
            parent: 父控件。
        """
        super().__init__(parent=parent)
        self._label_text = label
        row = QHBoxLayout()
        row.setSpacing(current_tokens().space.xl)
        left = QVBoxLayout()
        left.setSpacing(0)
        self._label = Label(label, "lab")
        self._value = Label("", "hero", mono=True)
        # 设计稿 line-height 1.1：避免大字号默认行高留出过多空白。
        self._value.setFixedHeight(round(current_tokens().type.hero * 1.1))
        self._note = Label(note, note_role)
        left.addWidget(self._label)
        left.addWidget(self._value)
        left.addWidget(self._note)
        row.addLayout(left)
        self.sparkline = sparkline
        if sparkline is not None:
            row.addWidget(sparkline, 1, Qt.AlignmentFlag.AlignBottom)
        else:
            row.addStretch(1)
        self.body.addLayout(row)
        self._note.setVisible(bool(note))
        self.set_value(value, unit)

    def set_label(self, label: str) -> None:
        """修改顶部说明（语言切换时使用）。

        Args:
            label: 新说明。
        """
        self._label_text = label
        self._label.setText(label)

    def set_value(self, value: str, unit: str) -> None:
        """更新读数。

        Args:
            value: 读数文字。
            unit: 单位。
        """
        unit_px = current_tokens().type.hero_unit
        self._value.setText(
            f'{escape(value)}<span style="font-size:{unit_px}px"> {escape(unit)}</span>'
        )
        self._value_text = (value, unit)
        self.setAccessibleName(f"{self._label_text} {value} {unit}")

    def set_note(self, note: str) -> None:
        """更新读数下方说明。

        Args:
            note: 说明文字，支持简单富文本。
        """
        self._note.setText(note)
        self._note.setVisible(bool(note))
