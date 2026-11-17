"""权限变化必须同时收紧菜单、已打开页面、对话框及打印路径。"""

from pathlib import Path
from typing import NoReturn

from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from gelinspect.app_context import AppContext
from gelinspect.domain.entities import User
from gelinspect.domain.enums import Role
from gelinspect.domain.permissions import Permission
from gelinspect.domain.permissions import PermissionGroup as G
from gelinspect.ui.main_window import MainWindow, Screen
from gelinspect.ui.pages.reports_page import ReportsPage
from gelinspect.ui.pages.users_dialogs import NewUserDialog
from gelinspect.ui.pages.users_page import UsersPage
from tests.services.reporting.test_rendering import big_experiment
from tests.ui.conftest import DEMO_PASSWORDS, screen_of, settle, sign_in


def test_management_menu_hidden_even_with_all_ordinary_groups(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    user = app_context.database.users.get_by_username("experimenter")
    assert user is not None
    app_context.users.set_permission_groups(admin, user.user_id, frozenset(G))
    sign_in(qapp, window, "experimenter")
    item = next(i for i in window.shell.nav.items() if i.entry.key == "users")
    assert item.isHidden() and not window.navigate("users")
    assert window.session is not None
    assert not window.session.can(Permission.USER_MANAGE)


def test_admin_creation_ui_has_no_privileged_category_and_default_no_grants(
    qtbot: QtBot, qapp: QApplication, window: MainWindow
) -> None:
    sign_in(qapp, window, "admin")
    assert window.navigate("users")
    page = window.page("users")
    assert isinstance(page, UsersPage)

    def submit(*args: object) -> NoReturn:
        raise AssertionError("not submitting")

    dialog = NewUserDialog(submit, parent=page)
    qtbot.addWidget(dialog)
    assert [dialog.role_box.itemData(i) for i in range(dialog.role_box.count())] == [
        Role.USER.value
    ]
    assert dialog.permissions.groups() == frozenset()
    assert len(dialog.permissions.boxes) == 6


def test_grant_and_revoke_close_stale_dialog_and_refresh_current_session_ui(
    qtbot: QtBot, qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    assert window.navigate("new")
    session = window.session
    assert session is not None
    modal = QDialog(window)
    qtbot.addWidget(modal)
    modal.setModal(True)
    modal.show()
    settle(qapp)
    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    app_context.users.set_permission_groups(admin, session.user.user_id, frozenset({G.VIEW}))
    window._sync_permissions()
    assert not modal.isVisible()
    assert window.current_route == "dashboard"
    assert not window.navigate("new")
    assert session.can(Permission.EXPERIMENT_VIEW) and not session.can(Permission.EXPERIMENT_RUN)
    app_context.users.set_permission_groups(
        admin, session.user.user_id, frozenset({G.TEMPLATE, G.EXPERIMENT})
    )
    window._sync_permissions()
    assert window.navigate("templates") and window.navigate("new")


def test_disabling_signed_in_user_returns_to_login(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    session = window.session
    assert session is not None
    admin = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    app_context.users.set_active(admin, session.user.user_id, False)
    window._sync_permissions()
    assert window.session is None and screen_of(window) is Screen.LOGIN
    assert not session.permissions()


def test_developer_menu_and_company_permission_are_available_without_default_account(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    assert all(u.role is not Role.DEVELOPER for u in app_context.database.users.list_all())
    app_context.database.users.add(
        User("dev", "developer", "开发人员", Role.DEVELOPER),
        password_hash=app_context.auth.hash_password("Developer-Test1"),
        must_change_password=False,
    )
    window._on_login_submitted("developer", "Developer-Test1")
    settle(qapp)
    assert window.navigate("users") and window.navigate("settings")
    item = next(i for i in window.shell.nav.items() if i.entry.key == "users")
    assert not item.isHidden()
    assert window.session is not None
    assert window.session.can(Permission.COMPANY_CONFIGURE)


def test_read_only_report_cannot_write_pdf_by_direct_print_call(
    qapp: QApplication, window: MainWindow, app_context: AppContext, tmp_path: Path
) -> None:
    experiment = big_experiment(18)
    app_context.database.experiments.save(experiment)
    session = app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    worker = app_context.database.users.get_by_username("experimenter")
    assert worker is not None
    app_context.users.set_permission_groups(session, worker.user_id, frozenset({G.VIEW}))
    sign_in(qapp, window, "experimenter")
    assert window.open_report(experiment.experiment_id)
    page = window.page("reports")
    assert isinstance(page, ReportsPage)
    assert page.current_preview is not None and page.preview_widget.pageCount() > 0
    assert not page.print_button.isEnabled() and not page.export_button.isEnabled()
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    path = tmp_path / "unauthorized.pdf"
    printer.setOutputFileName(str(path))
    assert page.print_to(printer) == 0
    assert not path.exists()
    app_context.users.set_permission_groups(session, worker.user_id, frozenset())
    page._paint_preview(printer)
    assert page._preview is None and page.preview_widget.isHidden()
