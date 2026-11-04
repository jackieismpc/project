"""卡片容器：8px 圆角、1px 细线、无阴影。"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.label import Label


class Card(QFrame):
    """带可选标题的卡片。

    内容加到 :attr:`body`；标题行右侧可通过 :attr:`header` 追加控件。

    Attributes:
        body: 内容区纵向布局。
        header: 标题行横向布局，没有标题时为 ``None``。
    """

    def __init__(
        self,
        title: str | None = None,
        *,
        role: str = "card",
        padded: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """创建卡片。

        Args:
            title: 标题，``None`` 表示不显示标题行。
            role: 样式角色，``card``（默认）或 ``tile``、``video``。
            padded: 是否使用卡片内边距（18×16）。
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", role)
        space = current_tokens().space
        self.body = QVBoxLayout(self)
        if padded:
            self.body.setContentsMargins(space.card_x, space.card_y, space.card_x, space.card_y)
        else:
            self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(space.md)
        self.header: QHBoxLayout | None = None
        self._title: Label | None = None
        if title is not None:
            self.header = QHBoxLayout()
            self.header.setSpacing(space.sm)
            self._title = Label(title, "h2")
            self.header.addWidget(self._title)
            self.header.addStretch(1)
            self.body.addLayout(self.header)
            self.setAccessibleName(title)

    def set_title(self, title: str) -> None:
        """修改标题文字。

        Args:
            title: 新标题。

        Raises:
            RuntimeError: 创建时没有标题行。
        """
        if self._title is None:
            raise RuntimeError("该卡片没有标题行")
        self._title.setText(title)
        self.setAccessibleName(title)
