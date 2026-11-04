"""带角色的文字标签，样式由全局 QSS 的 ``role`` 属性决定。"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from gelinspect.ui.widgets.util import repolish


class Label(QLabel):
    """按角色着样式的 ``QLabel``。

    角色（``role``）见 ``theme/stylesheet.py``：``h1``、``h2``、``lab``、``meta``、
    ``val``、``body``、``readout``、``stat``、``hero``、``ok``、``warn``、``danger``。
    读数、管号、批号应设置 ``mono=True``。
    """

    def __init__(
        self,
        text: str = "",
        role: str = "body",
        *,
        mono: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建标签。

        Args:
            text: 文字。
            role: 样式角色。
            mono: 是否使用等宽字体。
            parent: 父控件。
        """
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setProperty("mono", mono)
        # 纵向只取需要的高度，多余空间留给内容控件。
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def set_role(self, role: str, *, mono: bool | None = None) -> None:
        """切换样式角色。

        Args:
            role: 新角色。
            mono: 是否等宽，``None`` 表示不变。
        """
        self.setProperty("role", role)
        if mono is not None:
            self.setProperty("mono", mono)
        repolish(self)
