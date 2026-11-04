"""状态徽标：圆角胶囊。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from gelinspect.ui.widgets.util import repolish


class BadgeTone(StrEnum):
    """徽标色调。"""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    REVIEW = "review"
    NEUTRAL = "neutral"


class StatusBadge(QLabel):
    """状态徽标，例如「第 2 排读数中」。"""

    def __init__(
        self,
        text: str,
        tone: BadgeTone | str = BadgeTone.INFO,
        parent: QWidget | None = None,
    ) -> None:
        """创建徽标。

        Args:
            text: 文字。
            tone: 色调。
            parent: 父控件。
        """
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self.set_tone(tone)

    def set_tone(self, tone: BadgeTone | str) -> None:
        """切换色调。

        Args:
            tone: 新色调。
        """
        self.setProperty("tone", BadgeTone(tone).value)
        repolish(self)

    def set_text(self, text: str) -> None:
        """修改文字并同步无障碍名称。

        Args:
            text: 新文字。
        """
        self.setText(text)
        self.setAccessibleName(text)
