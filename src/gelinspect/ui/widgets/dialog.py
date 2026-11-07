"""确认对话框：标题、说明、取消与确认按钮。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label


class ConfirmDialog(QDialog):
    """确认对话框。危险操作（如停止试验）使用 ``danger=True``，确认钮变红。"""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_text: str | None = None,
        cancel_text: str | None = None,
        danger: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            title: 标题。
            message: 说明文字。
            confirm_text: 确认按钮文字，``None`` 使用“确认”。
            cancel_text: 取消按钮文字，``None`` 使用“取消”。
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
        self.cancel_button = Button(cancel_text or self.tr("取消"), ButtonVariant.SECONDARY)
        self.confirm_button = Button(
            confirm_text or self.tr("确认"),
            ButtonVariant.DANGER if danger else ButtonVariant.PRIMARY,
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
        confirm_text: str | None = None,
        cancel_text: str | None = None,
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


class ErrorDialog(QDialog):
    """错误对话框：标题、说明、可展开的技术细节、日志位置和一个“关闭”按钮。

    用于启动失败和未捕获异常。技术细节不翻译（来自异常和日志），说明文字由调用方翻译。
    """

    def __init__(
        self,
        title: str,
        message: str,
        *,
        details: str = "",
        log_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            title: 标题。
            message: 面向用户的说明，包含怎么办。
            details: 技术细节（异常信息），可为空。
            log_path: 日志文件位置，可为空。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(Label(title, "h2"))
        body = Label(message, "body")
        body.setWordWrap(True)
        layout.addWidget(body)
        self.details_view: QPlainTextEdit | None = None
        if details:
            view = QPlainTextEdit(details)
            view.setReadOnly(True)
            view.setMinimumHeight(90)
            view.setMaximumHeight(160)
            view.setAccessibleName(self.tr("技术细节"))
            layout.addWidget(view)
            self.details_view = view
        if log_path:
            where = Label(self.tr("日志文件：") + log_path, "meta")
            where.setWordWrap(True)
            where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(where)
        layout.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = Button(self.tr("关闭"), ButtonVariant.PRIMARY)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
