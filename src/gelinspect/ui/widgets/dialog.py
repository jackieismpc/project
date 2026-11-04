"""确认对话框：标题、说明、取消与确认按钮。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label


class ConfirmDialog(QDialog):
    """确认对话框。危险操作（如停止试验）使用 ``danger=True``，确认钮变红。"""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_text: str = "确认",
        cancel_text: str = "取消",
        danger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            title: 标题。
            message: 说明文字。
            confirm_text: 确认按钮文字。
            cancel_text: 取消按钮文字。
            danger: 是否为危险操作。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(Label(title, "h2"))
        body = Label(message, "body")
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = Button(cancel_text, ButtonVariant.SECONDARY)
        self.confirm_button = Button(
            confirm_text, ButtonVariant.DANGER if danger else ButtonVariant.PRIMARY
        )
        for button in (self.cancel_button, self.confirm_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.setAutoDefault(False)
        self.confirm_button.setDefault(True)

    @staticmethod
    def ask(
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        confirm_text: str = "确认",
        cancel_text: str = "取消",
        danger: bool = False,
    ) -> bool:
        """弹出对话框并等待结果。

        Args:
            parent: 父窗口。
            title: 标题。
            message: 说明文字。
            confirm_text: 确认按钮文字。
            cancel_text: 取消按钮文字。
            danger: 是否为危险操作。

        Returns:
            用户点击确认时为 ``True``。
        """
        dialog = ConfirmDialog(
            title,
            message,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            danger=danger,
            parent=parent,
        )
        return dialog.exec() == QDialog.DialogCode.Accepted
