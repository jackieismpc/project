"""可以留空的日期输入：留空表示「不限」，点开日历从当前月份选。"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QDateEdit, QWidget

_EMPTY = QDate(2000, 1, 1)
"""代表「没有填」的日期：显示为空白。"""


class OptionalDateEdit(QDateEdit):
    """带日历弹窗、可以留空的日期输入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建输入框，初始为空。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setMinimumDate(_EMPTY)
        self.setSpecialValueText(" ")
        self.setMinimumHeight(44)
        self.setDate(_EMPTY)

    def value(self) -> date | None:
        """当前填的日期。

        Returns:
            日期；留空时为 None。
        """
        current = self.date()
        return None if current == _EMPTY else date(current.year(), current.month(), current.day())

    def set_value(self, value: date | None) -> None:
        """设置日期。

        Args:
            value: 日期，None 表示清空。
        """
        self.setDate(_EMPTY if value is None else QDate(value.year, value.month, value.day))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """点开日历前，空白时把日历翻到当前月份（否则会停在 2000 年）。

        Args:
            event: 鼠标事件。
        """
        calendar = self.calendarWidget()
        if self.value() is None and calendar is not None:
            today = QDate.currentDate()
            calendar.setCurrentPage(today.year(), today.month())
        super().mousePressEvent(event)
