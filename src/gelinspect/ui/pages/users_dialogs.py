"""用户管理的对话框：新建用户、修改角色、显示临时密码。

和模板编辑器一样，对话框不直接调用服务：保存时调用外面传入的函数，函数抛
``AuthError``、``ServiceError`` 时在对话框里显示原因并保持打开，让用户改完重试。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from gelinspect.domain.enums import Role
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.auth import AuthError
from gelinspect.services.errors import ServiceError
from gelinspect.services.user_service import CredentialIssue
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    role_label,
)
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.label import Label

NewUserSubmit = Callable[[str, str, Role, str | None], CredentialIssue]
"""新建用户：登录名、显示名称、角色、初始密码（None 表示自动生成）。"""


def _problem_text(error: Exception) -> str:
    if isinstance(error, AuthError):
        return describe_auth_error(error)
    if isinstance(error, ServiceError):
        return describe_service_error(error)
    if isinstance(error, StorageBusyError):
        return describe_storage_busy()
    raise error


class NewUserDialog(QDialog):
    """新建用户：登录名、显示名称、角色、可选的初始密码。"""

    def __init__(self, submit: NewUserSubmit, parent: QWidget | None = None) -> None:
        """创建对话框。

        Args:
            submit: 保存函数，成功返回新账号和临时密码。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._submit = submit
        self.issued: CredentialIssue | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        self.heading = Label("", "h2")
        layout.addWidget(self.heading)
        self.username_label = Label("", "lab")
        self.username = QLineEdit()
        self.username.setMinimumHeight(44)
        self.display_label = Label("", "lab")
        self.display_name = QLineEdit()
        self.display_name.setMinimumHeight(44)
        self.role_label = Label("", "lab")
        self.role_box = QComboBox()
        self.role_box.setMinimumHeight(44)
        for role in Role:
            self.role_box.addItem("", role.value)
        self.password_label = Label("", "lab")
        self.password = QLineEdit()
        self.password.setMinimumHeight(44)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        for label, field in (
            (self.username_label, self.username),
            (self.display_label, self.display_name),
            (self.role_label, self.role_box),
            (self.password_label, self.password),
        ):
            layout.addWidget(label)
            layout.addWidget(field)
        self.note = Label("", "meta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.error = Label("", "danger")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = Button("", ButtonVariant.SECONDARY)
        self.create_button = Button("", ButtonVariant.PRIMARY)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.create_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.create_button.clicked.connect(self.submit)
        self.retranslate()

    def role(self) -> Role:
        """选中的角色。

        Returns:
            角色。
        """
        return Role(self.role_box.currentData())

    def submit(self) -> bool:
        """保存；失败时在对话框里显示原因。

        Returns:
            成功并已关闭时为 True。
        """
        password = self.password.text()
        try:
            self.issued = self._submit(
                self.username.text(),
                self.display_name.text(),
                self.role(),
                password or None,
            )
        except (AuthError, ServiceError, StorageBusyError) as error:
            self.error.setText(_problem_text(error))
            self.error.setVisible(True)
            return False
        self.accept()
        return True

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("新建用户"))
        self.heading.setText(self.tr("新建用户"))
        self.username_label.setText(self.tr("登录名"))
        self.display_label.setText(self.tr("显示名称"))
        self.role_label.setText(self.tr("角色"))
        for index in range(self.role_box.count()):
            self.role_box.setItemText(index, role_label(Role(self.role_box.itemData(index))))
        self.password_label.setText(self.tr("初始密码（可选）"))
        self.password.setPlaceholderText(self.tr("留空则自动生成临时密码"))
        self.note.setText(self.tr("新账号首次登录必须先修改密码。"))
        self.cancel_button.setText(self.tr("取消"))
        self.create_button.setText(self.tr("创建"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class RoleDialog(QDialog):
    """修改某个用户的角色。"""

    def __init__(
        self,
        username: str,
        current: Role,
        submit: Callable[[Role], object],
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            username: 用户的登录名。
            current: 当前角色。
            submit: 保存函数，失败时抛异常。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._username = username
        self._submit = submit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        self.heading = Label("", "h2")
        layout.addWidget(self.heading)
        self.role_label = Label("", "lab")
        self.role_box = QComboBox()
        self.role_box.setMinimumHeight(44)
        for role in Role:
            self.role_box.addItem("", role.value)
        self.role_box.setCurrentIndex(self.role_box.findData(current.value))
        layout.addWidget(self.role_label)
        layout.addWidget(self.role_box)
        self.note = Label("", "meta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.error = Label("", "danger")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = Button("", ButtonVariant.SECONDARY)
        self.save_button = Button("", ButtonVariant.PRIMARY)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.submit)
        self.retranslate()

    def role(self) -> Role:
        """选中的角色。

        Returns:
            角色。
        """
        return Role(self.role_box.currentData())

    def submit(self) -> bool:
        """保存；失败时在对话框里显示原因。

        Returns:
            成功并已关闭时为 True。
        """
        try:
            self._submit(self.role())
        except (AuthError, ServiceError, StorageBusyError) as error:
            self.error.setText(_problem_text(error))
            self.error.setVisible(True)
            return False
        self.accept()
        return True

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("修改角色"))
        self.heading.setText(self.tr("修改 {name} 的角色").format(name=self._username))
        self.role_label.setText(self.tr("角色"))
        for index in range(self.role_box.count()):
            self.role_box.setItemText(index, role_label(Role(self.role_box.itemData(index))))
        self.note.setText(self.tr("改动立即生效：对方已登录的会话马上按新角色的权限工作。"))
        self.cancel_button.setText(self.tr("取消"))
        self.save_button.setText(self.tr("保存"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class CredentialDialog(QDialog):
    """显示新账号或重置后的临时密码。只显示这一次。"""

    def __init__(self, username: str, password: str, parent: QWidget | None = None) -> None:
        """创建对话框。

        Args:
            username: 用户的登录名。
            password: 临时密码明文。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._username = username
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        self.heading = Label("", "h2")
        layout.addWidget(self.heading)
        self.intro = Label("", "body")
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.password_view = Label(password, "readout", mono=True)
        self.password_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.password_view)
        self.note = Label("", "meta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = Button("", ButtonVariant.SECONDARY, icon="copy")
        self.close_button = Button("", ButtonVariant.PRIMARY)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.password_view.text())
        )
        self.close_button.clicked.connect(self.accept)
        self.retranslate()

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("临时密码"))
        self.heading.setText(self.tr("{name} 的临时密码").format(name=self._username))
        self.intro.setText(self.tr("请当面告知用户。对方首次登录时必须先修改密码。"))
        self.note.setText(self.tr("这个密码只显示这一次，关闭后无法再查看；忘了只能重新重置。"))
        self.copy_button.setText(self.tr("复制"))
        self.close_button.setText(self.tr("关闭"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
