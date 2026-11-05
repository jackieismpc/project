"""导出密码对话框：开启了导出加密时，每次导出前设置一次密码（输入两遍）。

密码只在内存里传给服务层，不保存、不写日志和审计。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from gelinspect.services.encrypted_zip import MIN_PASSWORD_LENGTH
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label

PasswordProvider = Callable[[QWidget], str | None]
"""取得导出密码的函数：参数是父窗口，返回密码，用户取消时返回 None。测试替换它。"""


class ExportPasswordDialog(QDialog):
    """设置导出密码。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建对话框。

        Args:
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(6)
        self._title = Label("", "h2")
        self._intro = Label("", "body")
        self._intro.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        touch = current_tokens().space.touch
        self._password_label = Label("", "lab")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(touch)
        self._confirm_label = Label("", "lab")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setMinimumHeight(touch)
        for widget in (self._password_label, self.password, self._confirm_label, self.confirm):
            layout.addWidget(widget)
        self._note = Label("", "meta")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)
        self.error_label = Label("", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = Button("", ButtonVariant.SECONDARY)
        self.ok_button = Button("", ButtonVariant.PRIMARY)
        for button in (self.cancel_button, self.ok_button):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.cancel_button.setAutoDefault(False)
        self.ok_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._submit)
        self.confirm.returnPressed.connect(self._submit)
        self._error: Callable[[], str] | None = None
        self.retranslate()

    def value(self) -> str:
        """输入的密码。

        Returns:
            密码文字。
        """
        return self.password.text()

    def _submit(self) -> None:
        if len(self.password.text()) < MIN_PASSWORD_LENGTH:
            self._show(lambda: self.tr("导出密码至少 {min} 个字符").format(min=MIN_PASSWORD_LENGTH))
            return
        if self.password.text() != self.confirm.text():
            self._show(lambda: self.tr("两次输入的密码不一致"))
            return
        self.accept()

    def _show(self, factory: Callable[[], str]) -> None:
        self._error = factory
        self.error_label.setText(factory())
        self.error_label.setVisible(True)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("设置导出密码"))
        self._title.setText(self.tr("设置导出密码"))
        self._intro.setText(
            self.tr("已开启导出加密：文件将打成 AES-256 加密的 ZIP，打开时需要这个密码。")
        )
        self._password_label.setText(self.tr("导出密码"))
        self._confirm_label.setText(self.tr("再输入一次"))
        self._note.setText(
            self.tr(
                "软件不保存这个密码，忘记后无法找回。请用 7-Zip、Keka 等工具解压，"
                "系统自带的解压不支持 AES 加密。"
            )
        )
        self.cancel_button.setText(self.tr("取消"))
        self.ok_button.setText(self.tr("导出"))
        for button in (self.cancel_button, self.ok_button):
            button.setAccessibleName(button.text())
        if self._error is not None:
            self.error_label.setText(self._error())

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


def ask_export_password(parent: QWidget) -> str | None:
    """弹出对话框设置导出密码。

    Args:
        parent: 父窗口。

    Returns:
        密码；用户取消时为 None。
    """
    dialog = ExportPasswordDialog(parent)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.value()
        return None
    finally:
        dialog.deleteLater()
