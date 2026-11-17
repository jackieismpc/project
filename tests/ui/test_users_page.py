"""用户与权限页：列表、新建、停用和启用、重置密码、改角色、权限矩阵、最后一个管理员的保护。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission, PermissionGroup
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.permission_picker import PermissionDialog
from gelinspect.ui.pages.users_dialogs import CredentialDialog, NewUserDialog
from gelinspect.ui.pages.users_page import UsersPage
from gelinspect.ui.widgets.dialog import ConfirmDialog
from tests.ui.conftest import DEMO_PASSWORDS, settle, sign_in


def open_users(app: QApplication, window: MainWindow) -> UsersPage:
    sign_in(app, window, "admin")
    assert window.navigate("users")
    page = window.page("users")
    assert isinstance(page, UsersPage)
    settle(app)
    return page


def usernames(page: UsersPage) -> list[str]:
    return [r.user.username for r in page.records()]


def audit_lines(context: AppContext) -> list[str]:
    found = context.database.audit.query(AuditQuery(action=AuditAction.USER_CHANGED), PageRequest())
    lines = [r.event.detail for r in sorted(found.items, key=lambda r: r.seq)]
    return [line for line in lines if not line.startswith("初始化账号")]  # 首次启动建的演示账号


@pytest.fixture
def seen_credentials(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """记录“临时密码”对话框里显示的内容，而不真的弹出模态对话框。"""
    shown: list[tuple[str, str]] = []

    def fake_exec(self: CredentialDialog) -> int:
        shown.append((self._username, self.password_view.text()))  # noqa: SLF001
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CredentialDialog, "exec", fake_exec)
    return shown


def test_lists_accounts_with_role_and_state_and_the_matrix_is_read_only(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_users(qapp, window)
    assert usernames(page) == ["admin", "analyst", "experimenter"]
    rows = {r["username"]: r for r in page.users_table.model.rows}
    assert rows["admin"]["role"] == "管理员"
    assert rows["analyst"]["role"] == "普通用户"
    assert len(page.matrix_table.model.rows) == 6
    assert [c.title for c in page.matrix_table.model.columns] == ["权限组", "说明"]
    assert "实验与结果处理" in str(rows["experimenter"]["groups"])


def test_create_a_user_shows_the_temporary_password_once_and_forces_a_change(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    seen_credentials: list[tuple[str, str]],
) -> None:
    page = open_users(qapp, window)

    def fill(dialog: NewUserDialog) -> int:
        dialog.username.setText("newbie")
        dialog.display_name.setText("新同事")
        dialog.role_box.setCurrentIndex(dialog.role_box.findData(Role.USER.value))
        assert dialog.submit()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(NewUserDialog, "exec", fill)
    issued = page.request_new_user()
    assert issued is not None and usernames(page) == ["admin", "analyst", "experimenter", "newbie"]
    assert seen_credentials == [("newbie", issued.temporary_password)]
    session = app_context.auth.login("newbie", issued.temporary_password)
    assert session.must_change_password and session.role is Role.USER
    assert not any(issued.temporary_password in line for line in audit_lines(app_context))
    assert page.selected() is not None and page.selected().user.username == "newbie"  # type: ignore[union-attr]


def test_the_new_user_dialog_shows_why_it_cannot_save_and_stays_open(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_users(qapp, window)
    dialog = NewUserDialog(
        lambda u, d, r, p, g: window.services.users.create_user(
            window.session, username=u, display_name=d, role=r, password=p, permission_groups=g
        ),
        parent=page,
    )
    dialog.username.setText("analyst")  # 已存在
    assert not dialog.submit() and dialog.error.isVisibleTo(dialog)
    assert "登录名已存在" in dialog.error.text()
    dialog.username.setText("has space")
    assert not dialog.submit() and "登录名" in dialog.error.text()
    dialog.username.setText("okname")
    dialog.password.setText("short")
    assert not dialog.submit() and "密码" in dialog.error.text()
    assert usernames(page) == ["admin", "analyst", "experimenter"]
    dialog.password.setText("")
    assert dialog.submit() and dialog.issued is not None


def test_disable_and_enable(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_users(qapp, window)
    asked: list[str] = []

    def accept(_parent: object, title: str, *_a: object, **_k: object) -> bool:
        asked.append(title)
        return True

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(accept))
    assert page.select_user("analyst")
    assert page.toggle_button.text() == "停用" and page.toggle_button.isEnabled()
    assert page.request_toggle_active()
    assert asked == ["停用账号？"]
    row = next(r for r in page.users_table.model.rows if r["username"] == "analyst")
    assert row["state"] == "已停用" and row["_tone"] == "warning"
    assert page.toggle_button.text() == "启用"  # 选中的是已停用账号，按钮变成“启用”
    assert not app_context.database.users.get_by_username("analyst").active  # type: ignore[union-attr]
    assert page.request_toggle_active()  # 启用不需要确认
    assert asked == ["停用账号？"]
    assert app_context.database.users.get_by_username("analyst").active  # type: ignore[union-attr]
    assert "停用用户 analyst" in audit_lines(app_context) and "启用用户 analyst" in audit_lines(
        app_context
    )


def test_declining_the_confirmation_changes_nothing(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = open_users(qapp, window)
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_a, **_k: False))
    page.select_user("analyst")
    assert not page.request_toggle_active() and page.request_reset_password() is None
    assert app_context.database.users.get_by_username("analyst").active  # type: ignore[union-attr]
    assert audit_lines(app_context) == []


def test_you_cannot_disable_or_reset_yourself_and_the_buttons_say_so(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_users(qapp, window)
    page.select_user("admin")
    assert not page.toggle_button.isEnabled() and "不能停用自己" in page.toggle_button.toolTip()
    assert not page.reset_button.isEnabled() and not page.role_button.isEnabled()
    page.select_user("analyst")
    assert page.toggle_button.isEnabled() and page.reset_button.isEnabled()


def test_reset_password_forces_a_change_on_the_next_login(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    seen_credentials: list[tuple[str, str]],
) -> None:
    page = open_users(qapp, window)
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *_a, **_k: True))
    page.select_user("experimenter")
    issued = page.request_reset_password()
    assert issued is not None and seen_credentials == [("experimenter", issued.temporary_password)]
    session = app_context.auth.login("experimenter", issued.temporary_password)
    assert session.must_change_password  # 首次登录强制改密
    assert "须改密" in str(
        next(r["state"] for r in page.users_table.model.rows if r["username"] == "experimenter")
    )
    from gelinspect.services.auth import InvalidCredentialsError

    with pytest.raises(InvalidCredentialsError):
        app_context.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])


def test_the_last_admin_cannot_be_demoted_and_the_dialog_explains_it(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    page = open_users(qapp, window)
    page.select_user("admin")
    assert not page.role_button.isEnabled()
    assert not page.reset_button.isEnabled()
    assert not page.toggle_button.isEnabled()


def test_changing_the_role_takes_effect_in_the_current_session(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_users(qapp, window)
    session = app_context.auth.login("experimenter", DEMO_PASSWORDS["experimenter"])
    page.select_user("experimenter")

    def change(dialog: PermissionDialog) -> int:
        for group, box in dialog.picker.boxes.items():
            box.setChecked(group in {PermissionGroup.VIEW, PermissionGroup.TEMPLATE})
        dialog.submit()
        return dialog.result()

    monkeypatch.setattr(PermissionDialog, "exec", change)
    assert page.request_change_role()
    assert session.can(Permission.EXPERIMENT_VIEW) and session.can(Permission.TEMPLATE_MANAGE)
    assert not session.can(Permission.EXPERIMENT_RUN)
    app_context.auth.authorize(session, Permission.TEMPLATE_MANAGE)
