"""用户与权限页：账号列表、新建、停用和启用、重置密码、改角色，以及只读的角色权限矩阵。

需要 ``user_manage``（管理员）。规则在服务层（``UserService``）：不能停用自己，不能停用或降级
最后一个管理员；改角色后该账号已登录的会话立即采用新角色；重置密码后对方首次登录必须改密。
本页只收集输入、显示结果，每个操作都会写审计。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QWidget

from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import PERMISSION_ROLES
from gelinspect.interfaces.storage import StorageBusyError
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AuthError, Session
from gelinspect.services.errors import ServiceError
from gelinspect.services.user_service import CredentialIssue, UserRecord
from gelinspect.ui.labels import (
    describe_auth_error,
    describe_service_error,
    describe_storage_busy,
    permission_label,
    role_label,
)
from gelinspect.ui.pages.page_frame import build_page_column
from gelinspect.ui.pages.users_dialogs import CredentialDialog, NewUserDialog, RoleDialog
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.data_table import Column, DataTable
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.label import Label
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)


class UsersPage(QWidget):
    """用户与权限页。

    Signals:
        sessionChanged: 当前登录用户自己的角色被改了，主窗口据此刷新入口和权限。
    """

    sessionChanged = Signal()  # noqa: N815

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            services: 界面服务。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._session: Session | None = None
        self._records: list[UserRecord] = []

        column, _scroll = build_page_column(self)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.title = Label("", "h1")
        self.hint = Label("", "meta")
        self.new_button = Button("", ButtonVariant.PRIMARY, icon="plus")
        top.addWidget(self.title)
        top.addWidget(self.hint)
        top.addStretch(1)
        top.addWidget(self.new_button)
        column.addLayout(top)

        self.users_card = Card(" ")
        self.users_table = DataTable(self._user_columns(), fit_content=True)
        self.users_card.body.addWidget(self.users_table)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.reset_button = Button("", ButtonVariant.SECONDARY, icon="key-round")
        self.role_button = Button("", ButtonVariant.SECONDARY, icon="pencil")
        self.toggle_button = Button("", ButtonVariant.SECONDARY)
        self.rule_note = Label("", "meta")
        self.rule_note.setWordWrap(True)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.role_button)
        actions.addWidget(self.toggle_button)
        actions.addStretch(1)
        self.users_card.body.addLayout(actions)
        self.users_card.body.addWidget(self.rule_note)
        column.addWidget(self.users_card)

        self.matrix_card = Card(" ")
        self.matrix_table = DataTable(self._matrix_columns(), fit_content=True)
        self.matrix_card.body.addWidget(self.matrix_table)
        self.matrix_note = Label("", "meta")
        self.matrix_note.setWordWrap(True)
        self.matrix_card.body.addWidget(self.matrix_note)
        column.addWidget(self.matrix_card)
        column.addStretch(1)

        self.new_button.clicked.connect(self.request_new_user)
        self.reset_button.clicked.connect(self.request_reset_password)
        self.role_button.clicked.connect(self.request_change_role)
        self.toggle_button.clicked.connect(self.request_toggle_active)
        self.users_table.selectionChanged.connect(self._update_buttons)
        self.retranslate()

    # ------------------------------------------------------------------ 数据

    def set_session(self, session: Session | None) -> None:
        """设置当前会话并刷新。

        Args:
            session: 会话。
        """
        self._session = session
        self.refresh()

    def records(self) -> list[UserRecord]:
        """列表里的用户。

        Returns:
            用户记录列表。
        """
        return list(self._records)

    def refresh(self) -> None:
        """重新读取用户列表。"""
        self._records = []
        if self._session is not None:
            try:
                self._records = self._services.users.list_users(self._session)
            except (AuthError, StorageBusyError) as error:
                self._fail(error)
        self._render()

    def select_user(self, username: str) -> bool:
        """选中某个用户（测试和对话框之后恢复选中用）。

        Args:
            username: 登录名。

        Returns:
            找到并选中时为 True。
        """
        for row, record in enumerate(self._records):
            if record.user.username == username:
                self.users_table.view.selectRow(row)
                return True
        return False

    def selected(self) -> UserRecord | None:
        """当前选中的用户。

        Returns:
            用户记录；没有选中时为 None。
        """
        rows = self.users_table.selected_rows()
        if not rows:
            index = self.users_table.view.currentIndex()
            rows = [index.row()] if index.isValid() else []
        return self._records[rows[0]] if rows and rows[0] < len(self._records) else None

    def _render(self) -> None:
        me = self._session.user.user_id if self._session is not None else None
        selected = self.selected()
        keep = None if selected is None else selected.user.username
        rows = []
        for record in self._records:
            user = record.user
            state = self.tr("使用中") if user.active else self.tr("已停用")
            if record.must_change_password and user.active:
                state += " · " + self.tr("须改密")
            name = user.display_name + (self.tr("（我）") if user.user_id == me else "")
            rows.append(
                {
                    "username": user.username,
                    "name": name,
                    "role": role_label(user.role),
                    "state": state,
                    **({} if user.active else {"_tone": "warning"}),
                }
            )
        self.users_table.set_rows(rows)
        if keep is not None:
            self.select_user(keep)
        self.matrix_table.set_rows(
            [
                {
                    "permission": permission_label(permission),
                    **{role.value: "✓" if role in roles else "" for role in Role},
                }
                for permission, roles in PERMISSION_ROLES.items()
            ]
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        record = self.selected()
        me = self._session.user.user_id if self._session is not None else None
        has = record is not None
        is_self = record is not None and record.user.user_id == me
        self.role_button.setEnabled(has)
        self.reset_button.setEnabled(has and not is_self)
        self.reset_button.setToolTip(
            self.tr("自己的密码请用用户菜单里的「修改密码」") if is_self else ""
        )
        self.toggle_button.setEnabled(has and not is_self)
        self.toggle_button.setToolTip(self.tr("不能停用自己的账号") if is_self else "")
        active = record is None or record.user.active
        self.toggle_button.setText(self.tr("停用") if active else self.tr("启用"))

    # ------------------------------------------------------------------ 操作

    def request_new_user(self) -> CredentialIssue | None:
        """打开新建用户对话框；成功后显示临时密码。

        Returns:
            新账号和临时密码；取消时为 None。
        """
        session = self._session

        def submit(
            username: str, display_name: str, role: Role, password: str | None
        ) -> CredentialIssue:
            return self._services.users.create_user(
                session, username=username, display_name=display_name, role=role, password=password
            )

        dialog = NewUserDialog(submit, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.issued is None:
            return None
        issued = dialog.issued
        self.refresh()
        self.select_user(issued.user.username)
        self._show_credentials(issued)
        return issued

    def request_reset_password(self) -> CredentialIssue | None:
        """确认后重置选中用户的密码，显示临时密码。

        Returns:
            用户和临时密码；取消或失败时为 None。
        """
        record = self.selected()
        if record is None:
            return None
        confirmed = ConfirmDialog.ask(
            self,
            self.tr("重置密码？"),
            self.tr(
                "将为 {name} 生成一个临时密码。对方已登录的会话会立即失效，下次登录必须先修改密码。"
            ).format(name=record.user.username),
            confirm_text=self.tr("重置"),
            danger=True,
        )
        if not confirmed:
            return None
        try:
            issued = self._services.users.reset_password(self._session, record.user.user_id)
        except (AuthError, ServiceError, StorageBusyError) as error:
            self._fail(error)
            return None
        self.refresh()
        self._show_credentials(issued)
        return issued

    def request_change_role(self) -> bool:
        """打开改角色对话框；成功后刷新。

        Returns:
            已修改时为 True。
        """
        record = self.selected()
        session = self._session
        if record is None or session is None:
            return False

        def submit(role: Role) -> object:
            return self._services.users.change_role(session, record.user.user_id, role)

        dialog = RoleDialog(record.user.username, record.user.role, submit, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.refresh()
        self.select_user(record.user.username)
        if record.user.user_id == session.user.user_id:
            self.sessionChanged.emit()
        return True

    def request_toggle_active(self) -> bool:
        """确认后停用或启用选中的用户。

        Returns:
            已改变时为 True。
        """
        record = self.selected()
        if record is None:
            return False
        target_active = not record.user.active
        if not target_active:
            confirmed = ConfirmDialog.ask(
                self,
                self.tr("停用账号？"),
                self.tr("{name} 将不能再登录，已登录的会话立即失效。可以随时重新启用。").format(
                    name=record.user.username
                ),
                confirm_text=self.tr("停用"),
                danger=True,
            )
            if not confirmed:
                return False
        try:
            self._services.users.set_active(self._session, record.user.user_id, target_active)
        except (AuthError, ServiceError, StorageBusyError) as error:
            self._fail(error)
            return False
        self.refresh()
        self.select_user(record.user.username)
        self._toast(
            self.tr("已启用 {name}") if target_active else self.tr("已停用 {name}"),
            record.user.username,
        )
        return True

    def _show_credentials(self, issued: CredentialIssue) -> None:
        CredentialDialog(issued.user.username, issued.temporary_password, parent=self).exec()

    # ------------------------------------------------------------------ 提示

    def _toast(self, template: str, name: str) -> None:
        Toast.show_message(self.window(), template.format(name=name), ToastKind.SUCCESS)

    def _fail(self, error: Exception) -> None:
        _log.info("用户管理操作失败：%s", error)
        if isinstance(error, ServiceError):
            text = describe_service_error(error)
        elif isinstance(error, AuthError):
            text = describe_auth_error(error)
        else:
            text = describe_storage_busy()
        Toast.show_message(self.window(), text, ToastKind.WARNING)

    # ------------------------------------------------------------------ 文字

    def _user_columns(self) -> list[Column]:
        return [
            Column("username", self.tr("登录名"), mono=True),
            Column("name", self.tr("显示名称")),
            Column("role", self.tr("角色")),
            Column("state", self.tr("状态")),
        ]

    def _matrix_columns(self) -> list[Column]:
        return [
            Column("permission", self.tr("权限")),
            *(Column(role.value, role_label(role)) for role in Role),
        ]

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.title.setText(self.tr("用户与权限"))
        self.hint.setText(self.tr("改动立即生效，并记入审计追踪"))
        self.new_button.setText(self.tr("新建用户"))
        self.users_card.set_title(self.tr("账号"))
        self.reset_button.setText(self.tr("重置密码"))
        self.role_button.setText(self.tr("修改角色…"))
        self.rule_note.setText(
            self.tr(
                "不能停用自己，也不能停用或降级最后一个管理员。重置密码后，对方首次登录必须先改密。"
            )
        )
        self.users_table.set_columns(self._user_columns())
        self.users_table.set_empty_text(self.tr("还没有账号"))
        self.matrix_card.set_title(self.tr("角色与权限"))
        self.matrix_note.setText(
            self.tr(
                "只读。权限矩阵是软件的一部分，调整需要发布新版本；它也是界面入口和服务层检查的依据。"
            )
        )
        self.matrix_table.set_columns(self._matrix_columns())
        self._render()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
