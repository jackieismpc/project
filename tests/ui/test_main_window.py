"""主窗口：登录、锁屏、权限禁用与导航。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from gelinspect.app_context import AppContext, build_app_context
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.auth import LoginPolicy
from gelinspect.services.config import parse_config
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.main_window import MainWindow, Screen
from gelinspect.ui.pages.auth_pages import ChangePasswordDialog, LoginMode
from gelinspect.ui.pages.dashboard import DashboardPage
from gelinspect.ui.pages.placeholder import PlaceholderPage
from gelinspect.ui.routes import PAGE_ROUTES, ROUTES, route_for
from gelinspect.ui.widgets.app_shell import MemoryShellSettings
from tests.ui.conftest import DEMO_PASSWORDS, make_config, screen_of, settle, sign_in


def audit_details(context: AppContext, action: AuditAction, actor: str) -> list[str]:
    page = context.database.audit.query(AuditQuery(action=action, actor=actor), PageRequest())
    return [r.event.detail for r in sorted(page.items, key=lambda r: r.seq)]


def test_starts_on_the_login_page_with_demo_accounts(window: MainWindow) -> None:
    assert screen_of(window) is Screen.LOGIN
    assert window.session is None
    assert [r.account.username for r in window.login_page._demo_rows] == [
        "admin",
        "experimenter",
        "analyst",
    ]
    assert window.shell.toolbar.button("new_experiment").isEnabled() is False


def test_demo_row_fills_the_form(window: MainWindow) -> None:
    window.login_page._demo_rows[1].click()
    assert window.login_page.username.text() == "experimenter"
    assert window.login_page.password.text() == DEMO_PASSWORDS["experimenter"]


def test_successful_login_shows_dashboard_and_user(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    assert screen_of(window) is Screen.SHELL
    assert window.session is not None and window.session.role is Role.EXPERIMENTER
    assert isinstance(window.page("dashboard"), DashboardPage)
    assert window.shell.toolbar._user.text() == "Jordan Li · 实验员"
    assert audit_details(app_context, AuditAction.LOGIN, "experimenter") == ["登录成功"]


def test_failed_login_shows_a_clear_error_and_audits(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    window.login_page.username.setText("admin")
    window.login_page.password.setText("wrong-password")
    window.login_page.submit_button.click()
    settle(qapp)
    assert screen_of(window) is Screen.LOGIN
    assert window.login_page.error_label.isVisible()
    assert "4" in window.login_page.error_label.text()  # 还可以尝试 4 次
    assert window.login_page.password.text() == ""
    assert len(audit_details(app_context, AuditAction.LOGIN_FAILED, "admin")) == 1


def test_login_requires_both_fields(qapp: QApplication, window: MainWindow) -> None:
    window.login_page.submit_button.click()
    assert window.login_page.error_label.text() == "请输入用户名"
    window.login_page.username.setText("admin")
    window.login_page.submit_button.click()
    assert window.login_page.error_label.text() == "请输入密码"


def test_lockout_disables_the_form_with_a_countdown(
    qapp: QApplication, tmp_path: Path, language: LanguageManager
) -> None:
    config = make_config(tmp_path, security={"max_login_attempts": 3, "lockout_minutes": 2})
    context = build_app_context(parse_config(config))
    win = MainWindow(
        context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    win.show()
    try:
        for _ in range(3):
            win.login_page.username.setText("analyst")
            win.login_page.password.setText("bad-password")
            win.login_page.submit_button.click()
        settle(qapp)
        page = win.login_page
        assert page.is_locked_out()
        assert not page.submit_button.isEnabled() and not page.password.isEnabled()
        assert "02:0" in page.error_label.text() or "01:5" in page.error_label.text()
        # 锁定期间，正确的密码也不能登录
        page.password.setEnabled(True)
        page.submit_button.setEnabled(True)
        page.username.setText("analyst")
        page.password.setText(DEMO_PASSWORDS["analyst"])
        page.submit_button.click()
        assert screen_of(win) is Screen.LOGIN
        assert any("锁定" in d for d in audit_details(context, AuditAction.LOGIN_FAILED, "analyst"))
    finally:
        win.bridge.close()
        win.close()
        context.close()


def test_lock_policy_comes_from_the_config(tmp_path: Path) -> None:
    config = parse_config(make_config(tmp_path, security={"max_login_attempts": 7}))
    context = build_app_context(config)
    try:
        assert context.auth.policy == LoginPolicy(max_attempts=7, lockout_s=300.0)
    finally:
        context.close()


@pytest.mark.parametrize(
    ("username", "enabled", "disabled"),
    [
        (
            "admin",
            {"dashboard", "templates", "audit", "users", "settings", "reports", "history", "about"},
            {"new"},
        ),
        (
            "experimenter",
            {"dashboard", "new", "templates", "reports", "history", "reagents", "about"},
            {"audit", "users", "settings"},
        ),
        (
            "analyst",
            {"dashboard", "audit", "history", "reports", "templates"},
            {"new", "users", "settings"},
        ),
    ],
)
def test_entries_follow_the_role(
    qapp: QApplication, window: MainWindow, username: str, enabled: set[str], disabled: set[str]
) -> None:
    sign_in(qapp, window, username)
    nav = window.shell.nav
    items = {item.entry.key: item for item in nav.items()}
    for key in enabled:
        assert items[key].isEnabledTo(nav), key
    for key in disabled:
        assert not items[key].isEnabledTo(nav), key
        assert "无此权限" in items[key].toolTip()
    # 快捷栏与权限矩阵一致
    toolbar = window.shell.toolbar
    for action_id, permission in {
        "new_experiment": Permission.EXPERIMENT_CREATE,
        "audit_trail": Permission.AUDIT_VIEW,
        "instrument_settings": Permission.INSTRUMENT_CONFIGURE,
    }.items():
        assert toolbar.action(action_id).isEnabled() is (
            window.session is not None and window.session.can(permission)
        )


def test_navigating_to_a_forbidden_page_is_refused(qapp: QApplication, window: MainWindow) -> None:
    sign_in(qapp, window, "experimenter")
    assert window.navigate("audit") is False
    assert window.current_route == "dashboard"
    assert window.page("audit") is None


def test_quick_actions_navigate_to_their_pages(qapp: QApplication, window: MainWindow) -> None:
    sign_in(qapp, window, "experimenter")
    window.shell.toolbar.action("new_experiment").trigger()
    assert window.current_route == "new"
    window.shell.toolbar.action("templates").trigger()
    assert window.current_route == "templates"
    window.shell.toolbar.action("print_report").trigger()
    assert window.current_route == "reports"


def test_every_designed_page_has_a_route_and_a_working_page(
    qapp: QApplication, window: MainWindow
) -> None:
    # 设计稿的 12 个页面：登录 + 11 个外壳页面（含单管详情），再加「关于」
    keys = {r.key for r in ROUTES}
    assert {
        "login", "dashboard", "new", "monitor", "tube", "history",
        "templates", "reagents", "reports", "audit", "users", "settings", "about",
    } <= keys  # fmt: skip
    # 管理员和实验员合起来能进入的页面，逐个打开
    reached: set[str] = set()
    for username in ("admin", "experimenter"):
        sign_in(qapp, window, username)
        for route in PAGE_ROUTES:
            if window.navigate(route.key):
                reached.add(route.key)
                page = window.page(route.key)
                assert page is not None
                if route.phase is not None:
                    assert isinstance(page, PlaceholderPage)
                else:
                    assert not isinstance(page, PlaceholderPage), route.key
        window.logout()
    assert reached == {r.key for r in PAGE_ROUTES}


def test_placeholder_page_names_its_phase(qapp: QApplication, window: MainWindow) -> None:
    sign_in(qapp, window, "experimenter")
    window.navigate("monitor")
    page = window.page("monitor")
    assert isinstance(page, PlaceholderPage)
    assert page.badge.text() == "建设中"
    assert "P9" in page.headline.text()
    assert route_for("monitor").phase == "P9"


def test_dashboard_shows_live_instrument_data(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    from tests.ui.conftest import wait_for

    sign_in(qapp, window, "experimenter")
    dashboard = window.page("dashboard")
    assert isinstance(dashboard, DashboardPage)
    assert wait_for(qapp, lambda: window.shell.toolbar._instrument._text.text() == "仪器已连接")
    assert "37." in dashboard.temperature._value.text()
    assert dashboard.recent_table.is_empty_state_visible()


def test_user_menu_logout_returns_to_login_and_audits(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "analyst")
    window.shell.toolbar._user_actions["logout"].trigger()
    settle(qapp)
    assert screen_of(window) is Screen.LOGIN and window.session is None
    assert window.page("dashboard") is None
    assert audit_details(app_context, AuditAction.LOGOUT, "analyst") == ["退出登录"]
    assert not window.shell.toolbar.action("audit_trail").isEnabled()


def test_change_password_dialog_updates_credentials(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    from gelinspect.services.auth import InvalidCredentialsError
    from gelinspect.ui.pages.auth_pages import ChangePasswordDialog

    sign_in(qapp, window, "admin")
    session = window.session
    assert session is not None
    dialog = ChangePasswordDialog(
        lambda old, new: app_context.auth.change_password(session, old, new)
    )
    dialog.old.setText("wrong")
    dialog.new.setText("Another-Pass1")
    dialog.confirm.setText("mismatch")
    dialog.ok_button.click()
    assert "不一致" in dialog.error_label.text()
    dialog.confirm.setText("Another-Pass1")
    dialog.ok_button.click()
    assert dialog.error_label.isVisibleTo(dialog)  # 当前密码错误
    assert dialog.result() == 0
    dialog.old.setText(DEMO_PASSWORDS["admin"])
    dialog.ok_button.click()
    assert dialog.result() == 1
    with pytest.raises(InvalidCredentialsError):
        app_context.auth.login("admin", DEMO_PASSWORDS["admin"])
    app_context.auth.login("admin", "Another-Pass1")


# ---------------------------------------------------------------- 锁屏


def test_idle_lock_keeps_the_session_and_unlock_restores_it(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    sign_in(qapp, window, "experimenter")
    window.navigate("templates")
    window.lock_screen()
    assert screen_of(window) is Screen.LOCK
    assert window.lock_page.mode() is LoginMode.LOCKED
    assert window.session is not None and window.session.locked
    assert not window.shell.toolbar.action("new_experiment").isEnabled()
    window.lock_page.password.setText("wrong-password")
    window.lock_page.submit_button.click()
    assert screen_of(window) is Screen.LOCK
    assert window.lock_page.error_label.isVisible()
    window.lock_page.password.setText(DEMO_PASSWORDS["experimenter"])
    window.lock_page.submit_button.click()
    assert screen_of(window) is Screen.SHELL
    assert window.current_route == "templates"
    assert window.shell.toolbar.action("new_experiment").isEnabled()
    assert audit_details(app_context, AuditAction.SCREEN_LOCKED, "experimenter")
    assert audit_details(app_context, AuditAction.SCREEN_UNLOCKED, "experimenter")


def test_idle_watcher_triggers_the_lock(qapp: QApplication, window: MainWindow) -> None:
    from gelinspect.ui.idle import IdleWatcher

    now = [0.0]
    window.idle.stop()
    window.idle = IdleWatcher(900.0, clock=lambda: now[0], parent=window)
    window.idle.idle.connect(window.lock_screen)
    sign_in(qapp, window, "analyst")
    window.idle.start()
    now[0] = 899.0
    assert window.idle.check() is False and screen_of(window) is Screen.SHELL
    now[0] = 901.0
    assert window.idle.check() is True
    assert screen_of(window) is Screen.LOCK


def test_idle_activity_resets_the_timer() -> None:
    from gelinspect.ui.idle import IdleWatcher

    now = [0.0]
    watcher = IdleWatcher(10.0, clock=lambda: now[0])
    watcher.start()
    now[0] = 9.0
    watcher.touch()
    now[0] = 18.0
    assert watcher.check() is False
    now[0] = 20.0
    assert watcher.check() is True
    assert not watcher.active
    zero = IdleWatcher(0.0, clock=lambda: now[0])
    zero.start()
    now[0] = 1e9
    assert zero.check() is False
    zero.stop()


def test_default_idle_timeout_is_fifteen_minutes(
    qapp: QApplication, app_context: AppContext, language: LanguageManager
) -> None:
    assert app_context.config.security.idle_lock_minutes == 15
    win = MainWindow(
        app_context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
    )
    assert win.idle._timeout_s == 900
    win.bridge.close()
    win.deleteLater()


def test_lock_screen_during_a_run_does_not_touch_the_engine(
    qapp: QApplication, window: MainWindow, app_context: AppContext
) -> None:
    from tests.services.engine_helpers import ready_experiment

    sign_in(qapp, window, "experimenter")
    session = window.session
    assert session is not None
    app_context.database.experiments.save(ready_experiment(range(1, 9), "EXP-LOCK"))
    app_context.engine.start("EXP-LOCK", session.user)
    window.lock_screen()
    assert screen_of(window) is Screen.LOCK
    assert app_context.engine.active_experiment_id == "EXP-LOCK"
    # 锁屏时关窗要先解锁
    assert window.confirm_close() is False
    app_context.engine.stop(session.user)
    app_context.engine.wait(30.0)


# ---------------------------------------------------------------- 创建管理员


def test_setup_page_when_the_config_has_no_bootstrap_hook(
    qapp: QApplication,
    tmp_path: Path,
    language: LanguageManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    config.pop("bootstrap")
    context = build_app_context(parse_config(config))
    win = MainWindow(
        context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    win.show()
    try:
        # 真实配置：没有任何默认账号，也没有演示账号提示
        assert context.database.users.list_all() == []
        assert context.demo_accounts == ()
        assert screen_of(win) is Screen.SETUP
        assert win.login_page._demo_rows == []
        page = win.setup_page
        page.username.setText("root")
        page.password.setText("long-enough1")
        page.confirm.setText("different")
        page.submit_button.click()
        assert "不一致" in page.error_label.text()
        page.confirm.setText("long-enough1")
        page.submit_button.click()
        settle(qapp)
        assert screen_of(win) is Screen.LOGIN
        assert [u.username for u in context.database.users.list_all()] == ["root"]
        # 密码是管理员自己设的，直接生效：首次登录不弹改密对话框，直接进入系统
        win.login_page.username.setText("root")
        win.login_page.password.setText("long-enough1")
        win.login_page.submit_button.click()
        assert screen_of(win) is Screen.SHELL
        assert win.session is not None and not win.session.must_change_password
        assert win.shell.toolbar.action("audit_trail").isEnabled()
    finally:
        win.bridge.close()
        win.close()
        context.close()


def test_forced_password_change_still_applies_to_an_account_marked_for_it(
    qapp: QApplication,
    window: MainWindow,
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """别人代设初始密码的账号（must_change_password）首次登录仍要改密；取消等于退出登录。"""
    from gelinspect.domain.entities import User
    from gelinspect.infrastructure.db.passwords import hash_password

    app_context.database.users.add(
        User("u-delegated", "delegated", "代设账号", Role.ADMIN),
        password_hash=hash_password("Initial-pass1"),
        must_change_password=True,
    )
    monkeypatch.setattr(ChangePasswordDialog, "exec", lambda self: 0)
    window.login_page.username.setText("delegated")
    window.login_page.password.setText("Initial-pass1")
    window.login_page.submit_button.click()
    settle(qapp)
    assert screen_of(window) is Screen.LOGIN and window.session is None

    def change_then_accept(self: ChangePasswordDialog) -> int:
        assert self.cancel_button.text() == "退出登录"
        self.old.setText("Initial-pass1")
        self.new.setText("Fresh-pass-9")
        self.confirm.setText("Fresh-pass-9")
        self.ok_button.click()
        return int(self.result())

    monkeypatch.setattr(ChangePasswordDialog, "exec", change_then_accept)
    window.login_page.username.setText("delegated")
    window.login_page.password.setText("Initial-pass1")
    window.login_page.submit_button.click()
    settle(qapp)
    assert screen_of(window) is Screen.SHELL
    assert window.session is not None and not window.session.must_change_password
