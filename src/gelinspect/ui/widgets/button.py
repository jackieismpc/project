"""按钮：primary / secondary / danger，触控高度 ≥44px。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.util import repolish


class ButtonVariant(StrEnum):
    """按钮样式。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    DANGER = "danger"


class Button(QPushButton):
    """按钮。键盘 Tab 聚焦时显示 3px 焦点环，鼠标点击不抢焦点。"""

    def __init__(
        self,
        text: str,
        variant: ButtonVariant | str = ButtonVariant.SECONDARY,
        *,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """创建按钮。

        Args:
            text: 按钮文字。
            variant: 样式。
            icon: Lucide 图标名，可选。
            parent: 父控件。
        """
        super().__init__(text, parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(current_tokens().space.touch)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self._icon_name = icon
        self.set_variant(variant)

    def set_variant(self, variant: ButtonVariant | str) -> None:
        """切换样式。

        Args:
            variant: 新样式。
        """
        value = ButtonVariant(variant)
        self.setProperty("variant", value.value)
        if self._icon_name:
            color = {"primary": "on_accent", "danger": "danger"}.get(value.value, "ink")
            self.setIcon(IconLoader.icon(self._icon_name, 18, color))
            self.setIconSize(QSize(18, 18))
        repolish(self)
