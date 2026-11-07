"""用户与权限页：列表、新建、停用和启用、重置密码、改角色、权限矩阵、最后一个管理员的保护。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from gelinspect.app_context import AppContext
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import PERMISSION_ROLES, Permission
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.ui.main_window import MainWindow
from gelinspect.ui.pages.users_dialogs import CredentialDialog, NewUserDialog, RoleDialog
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
    assert rows["admin"]["role"] == "管理员" and str(rows["admin"]["name"]).endswith("（我）")
    assert rows["analyst"]["role"] == "分析员" and rows["analyst"]["state"] == "使用中"
    # 角色-权限矩阵：和 PERMISSION_ROLES 一一对应，只展示
    matrix = page.matrix_table.model.rows
    assert len(matrix) == len(Permission) == len(PERMISSION_ROLES)
    for row, roles in zip(matrix, PERMISSION_ROLES.values(), strict=True):
        for role in Role:
            assert row[role.value] == ("✓" if role in roles else "")
    assert [c.title for c in page.matrix_table.model.columns] == [
        "权限",
        "管理员",
        "实验员",
        "分析员",
    ]


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
        dialog.role_box.setCurrentIndex(dialog.role_box.findData(Role.ANALYST.value))
        assert dialog.submit()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(NewUserDialog, "exec", fill)
    issued = page.request_new_user()
    assert issued is not None and usernames(page) == ["admin", "analyst", "experimenter", "newbie"]
    assert seen_credentials == [("newbie", issued.temporary_password)]
    session = app_context.auth.login("newbie", issued.temporary_password)
    assert session.must_change_password and session.role is Role.ANALYST
    assert not any(issued.temporary_password in line for line in audit_lines(app_context))
    assert page.selected() is not None and page.selected().user.username == "newbie"  # type: ignore[union-attr]


def test_the_new_user_dialog_shows_why_it_cannot_save_and_stays_open(
    qapp: QApplication, window: MainWindow
) -> None:
    page = open_users(qapp, window)
    dialog = NewUserDialog(
        lambda u, d, r, p: window.services.users.create_user(
            window.session, username=u, display_name=d, role=r, password=p
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
    assert not page.reset_button.isEnabled() and page.role_button.isEnabled()
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
    record = page.selected()
    assert record is not None
    dialog = RoleDialog(
        record.user.username,
        record.user.role,
        lambda role: window.services.users.change_role(window.session, record.user.user_id, role),
        parent=page,
    )
    dialog.role_box.setCurrentIndex(dialog.role_box.findData(Role.ANALYST.value))
    assert not dialog.submit()
    assert "最后一个管理员" in dialog.error.text() and dialog.error.isVisibleTo(dialog)
    assert app_context.database.users.get_by_username("admin").role is Role.ADMIN  # type: ignore[union-attr]
    assert window.session is not None and window.session.role is Role.ADMIN


def test_changing_the_role_takes_effect_in_the_current_session(
    qapp: QApplication, window: MainWindow, app_context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_users(qapp, window)
    # 先再造一个管理员，才允许把自己降级
    second = app_context.users.create_user(
        window.session, username="admin2", display_name="二号", role=Role.ADMIN
    )
    page.refresh()
    assert second.user.role is Role.ADMIN
    page.select_user("admin")
    changed: list[bool] = []
    page.sessionChanged.connect(lambda: changed.append(True))

    def demote(dialog: RoleDialog) -> int:
        dialog.role_box.setCurrentIndex(dialog.role_box.findData(Role.ANALYST.value))
        assert dialog.submit()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(RoleDialog, "exec", demote)
    assert page.request_change_role()
    assert changed == [True]
    # 当前会话立即是分析员：入口、权限、所在页面都跟着变
    session = window.session
    assert session is not None and session.role is Role.ANALYST
    assert not session.can(Permission.USER_MANAGE) and session.can(Permission.RESULT_REVIEW)
    assert window.current_route == "dashboard"
    items = {item.entry.key: item for item in window.shell.nav.items()}
    assert not items["users"].isEnabledTo(window.shell.nav)
    assert items["audit"].isEnabledTo(window.shell.nav)
    assert not window.navigate("users")
