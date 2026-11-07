"""主窗口：登录、锁屏、创建管理员和外壳（快捷栏、侧栏、页面栈）。

窗口自己不碰数据库和引擎线程：登录、权限和读数据走 ``AppServices``，
引擎事件通过 :class:`~gelinspect.ui.bridge.EngineBridge` 转到主线程。

状态：

* ``setup``：系统里没有账号（真实配置首次启动），显示“创建管理员”。
* ``login``：未登录。
* ``shell``：已登录，显示外壳。
* ``lock``：空闲或手动锁屏，试验在后台继续，输入密码解锁。
"""

from __future__ import annotations

import logging
from enum import StrEnum

from PySide6.QtCore import QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QStackedWidget, QWidget

from gelinspect.domain.enums import BatchKind
from gelinspect.services.app_services import AppServices
from gelinspect.services.auth import AccountLockedError, AuthError, Session
from gelinspect.services.engine_types import (
    AlertLevel,
    EngineAlert,
    StateChanged,
    TemperatureChanged,
)
from gelinspect.services.instrument_monitor import InstrumentStatus
from gelinspect.ui.bridge import EngineBridge
from gelinspect.ui.i18n import LanguageManager
from gelinspect.ui.idle import IdleWatcher
from gelinspect.ui.labels import describe_alert, role_label
from gelinspect.ui.pages.about import AboutPage
from gelinspect.ui.pages.audit_page import AuditPage
from gelinspect.ui.pages.auth_pages import ChangePasswordDialog, LoginMode, LoginPage, SetupPage
from gelinspect.ui.pages.dashboard import DashboardPage
from gelinspect.ui.pages.history_page import HistoryPage
from gelinspect.ui.pages.monitor_page import MonitorPage
from gelinspect.ui.pages.new_experiment import NewExperimentPage
from gelinspect.ui.pages.reagents_page import ReagentsPage
from gelinspect.ui.pages.reports_page import ReportsPage
from gelinspect.ui.pages.settings_page import SettingsPage
from gelinspect.ui.pages.templates_page import TemplatesPage
from gelinspect.ui.pages.tube_detail_page import TubeDetailPage
from gelinspect.ui.pages.users_page import UsersPage
from gelinspect.ui.routes import (
    PAGE_ROUTES,
    QUICK_ACTION_ROUTES,
    Route,
    can_enter,
    denied_nav_keys,
    route_for,
    route_title,
)
from gelinspect.ui.widgets.app_shell import AppShell, ShellSettings
from gelinspect.ui.widgets.dialog import ConfirmDialog
from gelinspect.ui.widgets.toast import Toast, ToastKind

_log = logging.getLogger(__name__)

MIN_WINDOW_SIZE = (1024, 700)
"""窗口最小尺寸；设计目标最低分辨率为 1280×800。"""


class Screen(StrEnum):
    """主窗口当前显示的界面。"""

    SETUP = "setup"
    LOGIN = "login"
    SHELL = "shell"
    LOCK = "lock"


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        services: AppServices,
        language: LanguageManager,
        *,
        shell_settings: ShellSettings | None = None,
        animations_enabled: bool = True,
        idle_timeout_s: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """创建主窗口，显示登录页（或创建管理员页）。

        Args:
            services: 界面服务。
            language: 语言管理器。
            shell_settings: 侧栏模式的持久化接口，默认 ``QSettings``。
            animations_enabled: 是否启用侧栏动画；测试和截图关闭。
            idle_timeout_s: 空闲锁屏秒数，默认取配置（分钟）；0 表示不自动锁屏。
            parent: 父控件。
        """
        super().__init__(parent)
        self._services = services
        self._language = language
        self._session: Session | None = None
        self._screen = Screen.LOGIN
        self._pages: dict[str, QWidget] = {}
        self._current_route = "dashboard"
        self._closing = False
        self._pending_report_experiment: str | None = None
        self.resize(1280, 800)
        self.setMinimumSize(*MIN_WINDOW_SIZE)

        self.bridge = EngineBridge(
            services.engine, services.instrument, self, video=services.monitor
        )
        timeout = idle_timeout_s if idle_timeout_s is not None else services.idle_lock_minutes * 60
        self.idle = IdleWatcher(timeout, parent=self)
        self.idle.idle.connect(self.lock_screen)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self.setup_page = SetupPage()
        self.login_page = LoginPage()
        self.lock_page = LoginPage()
        self.page_stack = QStackedWidget()
        self.page_stack.setProperty("role", "root")
        self.shell = AppShell(
            self.page_stack, settings=shell_settings, animations_enabled=animations_enabled
        )
        for widget in (self.setup_page, self.login_page, self.shell, self.lock_page):
            self._stack.addWidget(widget)

        self.setup_page.submitted.connect(self._on_setup_submitted)
        self.login_page.submitted.connect(self._on_login_submitted)
        self.lock_page.submitted.connect(self._on_unlock_submitted)
        self.lock_page.logoutRequested.connect(lambda: self.logout(confirm=False))
        for page in (self.setup_page, self.login_page, self.lock_page):
            page.languageRequested.connect(self._on_language_selected)
        toolbar = self.shell.toolbar
        toolbar.actionTriggered.connect(self._on_quick_action)
        toolbar.languageSelected.connect(self._on_language_selected)
        toolbar.userActionRequested.connect(self._on_user_action)
        self.shell.navigationRequested.connect(self.navigate)
        self.bridge.instrumentChanged.connect(self._on_instrument)
        self.bridge.temperatureChanged.connect(self._on_temperature)
        self.bridge.stateChanged.connect(self._on_state_changed)
        self.bridge.alertRaised.connect(self._on_alert)
        language.languageChanged.connect(self._on_language_changed)

        self.login_page.set_demo_accounts(services.demo_accounts)
        self._apply_language_widgets()
        toolbar.set_user("", "")
        toolbar.set_user_permissions(())
        self.shell.nav.set_denied([r.key for r in PAGE_ROUTES if r.in_nav])
        self.retranslate()
        self.show_start_screen()

    # ------------------------------------------------------------------ 属性

    @property
    def services(self) -> AppServices:
        """界面服务。"""
        return self._services

    @property
    def session(self) -> Session | None:
        """当前会话，未登录时为 None。"""
        return self._session

    @property
    def current_screen(self) -> Screen:
        """当前显示的界面。"""
        return self._screen

    @property
    def current_route(self) -> str:
        """当前页面的路由标识。"""
        return self._current_route

    def page(self, key: str) -> QWidget | None:
        """已创建的页面（页面按需创建）。

        Args:
            key: 路由标识。

        Returns:
            页面，尚未创建时为 None。
        """
        return self._pages.get(key)

    # ------------------------------------------------------------------ 界面切换

    def _show(self, screen: Screen) -> None:
        self._screen = screen
        widget = {
            Screen.SETUP: self.setup_page,
            Screen.LOGIN: self.login_page,
            Screen.SHELL: self.shell,
            Screen.LOCK: self.lock_page,
        }[screen]
        self._stack.setCurrentWidget(widget)
        if isinstance(widget, LoginPage):
            widget.focus_input()

    def show_start_screen(self) -> None:
        """显示登录页；系统里还没有账号时显示创建管理员页。"""
        self.login_page.reset()
        if self._services.auth.needs_first_admin():
            self._show(Screen.SETUP)
        else:
            self._show(Screen.LOGIN)

    # ------------------------------------------------------------------ 登录

    def _on_setup_submitted(self, username: str, display_name: str, password: str) -> None:
        try:
            self._services.auth.create_first_admin(
                username=username, display_name=display_name, password=password
            )
        except AuthError as error:
            self.setup_page.show_auth_error(error)
            return
        self.show_start_screen()
        self.login_page.username.setText(username)
        self.login_page.password.setFocus()

    def _on_login_submitted(self, username: str, password: str) -> None:
        try:
            session = self._services.auth.login(username, password)
        except AuthError as error:
            self.login_page.password.clear()
            self.login_page.show_auth_error(error)
            return
        self.login_page.reset()
        self._enter_session(session)

    def _enter_session(self, session: Session) -> None:
        self._session = session
        user = session.user
        toolbar = self.shell.toolbar
        toolbar.set_user(user.display_name, role_label(user.role))
        self._apply_permissions()
        self._show(Screen.SHELL)
        self.navigate("dashboard")
        if session.must_change_password and not self._force_password_change(session):
            return
        self.idle.start()
        self._services.instrument.start()

    def _force_password_change(self, session: Session) -> bool:
        """首次登录强制改密；取消则退出登录。

        Returns:
            改密成功时为 True。
        """
        dialog = ChangePasswordDialog(
            lambda old, new: self._services.auth.change_password(session, old, new),
            forced=True,
            parent=self,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            self.logout(confirm=False)
            return False
        self._apply_permissions()
        self.navigate(self._current_route)
        return True

    def _apply_permissions(self) -> None:
        session = self._session
        toolbar = self.shell.toolbar
        toolbar.set_user_permissions(session.permissions() if session is not None else ())
        self.shell.nav.set_denied(denied_nav_keys(session))

    def logout(self, *, confirm: bool = True) -> bool:
        """退出登录，回到登录页。试验在后台继续。

        向导里有未完成的内容时先确认。

        Args:
            confirm: 为 False 时不确认（被强制退出，例如取消强制改密）。

        Returns:
            已退出时为 True；用户在确认里选择留下时为 False。
        """
        if confirm and not self._confirm_leave_current():
            return False
        session = self._session
        if session is not None:
            self._services.auth.logout(session)
        self._session = None
        self.idle.stop()
        self._drop_pages()
        self.shell.toolbar.set_user("", "")
        self._apply_permissions()
        self.show_start_screen()
        return True

    def lock_screen(self) -> None:
        """锁屏：试验继续运行，输入密码解锁。没有登录时无操作。"""
        session = self._session
        if session is None or self._screen is not Screen.SHELL:
            return
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.close()
        self._services.auth.lock(session)
        self.idle.stop()
        user = session.user
        self.lock_page.set_mode(
            LoginMode.LOCKED,
            username=user.username,
            user_label=f"{user.display_name} · {role_label(user.role)}",
        )
        self._apply_permissions()
        self._show(Screen.LOCK)

    def _on_unlock_submitted(self, _username: str, password: str) -> None:
        session = self._session
        if session is None:
            self.show_start_screen()
            return
        try:
            self._services.auth.unlock(session, password)
        except AccountLockedError as error:
            self.lock_page.password.clear()
            self.lock_page.show_auth_error(error)
            return
        except AuthError as error:
            self.lock_page.password.clear()
            self.lock_page.show_auth_error(error)
            if error.code.value == "session_invalid":
                self.logout(confirm=False)
            return
        self.lock_page.reset()
        self._apply_permissions()
        self._show(Screen.SHELL)
        self.idle.start()
        self.navigate(self._current_route)

    # ------------------------------------------------------------------ 导航

    def navigate(self, key: str) -> bool:
        """切换到某页面。没有权限时提示并留在原页面。

        Args:
            key: 路由标识。

        Returns:
            成功切换时为 True。
        """
        route = route_for(key)
        session = self._session
        if session is None:
            return False
        if not can_enter(route, session):
            Toast.show_message(
                self,
                self.tr("当前角色无此权限：{page}").format(page=route_title(route)),
                ToastKind.WARNING,
            )
            return False
        if key != self._current_route and not self._confirm_leave_current():
            return False
        page = self._pages.get(key)
        if page is None:
            page = self._create_page(route)
            self._pages[key] = page
            self.page_stack.addWidget(page)
        self.page_stack.setCurrentWidget(page)
        self._current_route = key
        nav_key = key if key in {r.key for r in PAGE_ROUTES if r.in_nav} else "monitor"
        self.shell.nav.set_current(nav_key)
        refreshable = (
            DashboardPage
            | TemplatesPage
            | ReagentsPage
            | NewExperimentPage
            | HistoryPage
            | AuditPage
            | UsersPage
            | SettingsPage
        )
        if isinstance(page, ReportsPage):
            if self._pending_report_experiment is not None:
                page.open_experiment(self._pending_report_experiment)
                self._pending_report_experiment = None
            else:
                page.refresh()
        if isinstance(page, refreshable):
            page.refresh()
        elif isinstance(page, MonitorPage):
            if page.experiment_id is None:
                page.open_experiment(None)
            else:
                page.reload()
        self.setWindowTitle(self._window_title())
        return True

    def _confirm_leave_current(self) -> bool:
        """离开当前页面前的确认：新试验向导里有未完成的内容时要用户确认。"""
        page = self._pages.get(self._current_route)
        if isinstance(page, NewExperimentPage | TubeDetailPage):
            return page.confirm_leave()
        return True

    def _create_page(self, route: Route) -> QWidget:
        if route.key == "dashboard":
            dashboard = DashboardPage(self._services)
            dashboard.set_session(self._session)
            dashboard.navigationRequested.connect(self.navigate)
            dashboard.experimentRequested.connect(self.open_experiment)
            self.bridge.wellUpdated.connect(lambda _e: dashboard.request_running_refresh())
            self.bridge.rowCountdown.connect(lambda _e: dashboard.request_running_refresh())
            self.bridge.instrumentChanged.connect(dashboard.on_instrument)
            self.bridge.temperatureChanged.connect(dashboard.on_temperature)
            self.bridge.stateChanged.connect(lambda _e: dashboard.refresh())
            last = self._services.instrument.last
            if last is not None:
                dashboard.on_instrument(last)
            return dashboard
        if route.key == "about":
            return AboutPage(self._services)
        if route.key == "new":
            wizard = NewExperimentPage(self._services, self.bridge)
            wizard.set_session(self._session)
            wizard.started.connect(self._on_experiment_started)
            return wizard
        if route.key == "templates":
            templates = TemplatesPage(self._services)
            templates.set_session(self._session)
            return templates
        if route.key == "reagents":
            reagents = ReagentsPage(self._services)
            reagents.set_session(self._session)
            return reagents
        if route.key == "monitor":
            monitor = MonitorPage(self._services, self.bridge)
            monitor.set_session(self._session)
            monitor.tubeRequested.connect(self.open_tube)
            monitor.navigationRequested.connect(self.navigate)
            monitor.reportRequested.connect(self.open_report)
            monitor.batchReportRequested.connect(self.open_batch_report)
            return monitor
        if route.key == "reports":
            reports = ReportsPage(self._services)
            reports.set_ui_language(self._language.current.value)
            reports.set_session(self._session)
            reports.navigationRequested.connect(self.navigate)
            reports.signedExperiment.connect(self._on_reviewed)
            return reports
        if route.key == "tube":
            tube = TubeDetailPage(self._services)
            tube.set_session(self._session)
            tube.backRequested.connect(self._back_to_monitor)
            tube.reviewed.connect(self._on_reviewed)
            return tube
        if route.key == "history":
            history = HistoryPage(self._services)
            history.set_session(self._session)
            history.experimentRequested.connect(self.open_experiment)
            return history
        if route.key == "audit":
            audit = AuditPage(self._services)
            audit.set_session(self._session)
            return audit
        if route.key == "users":
            users = UsersPage(self._services)
            users.set_session(self._session)
            users.sessionChanged.connect(self._on_own_role_changed)
            return users
        if route.key == "settings":
            settings = SettingsPage(self._services)
            settings.set_session(self._session)
            return settings
        raise KeyError(f"没有页面：{route.key}")

    def _on_own_role_changed(self) -> None:
        """管理员改了自己的角色：会话已经换成新角色，立即刷新入口，当前页没有权限就回工作台。"""
        session = self._session
        if session is None:
            return
        self.shell.toolbar.set_user(session.user.display_name, role_label(session.user.role))
        self._apply_permissions()
        if not can_enter(route_for(self._current_route), session):
            self.navigate("dashboard")

    def open_experiment(self, experiment_id: str) -> bool:
        """打开试验监控页显示某个试验（运行中的实时显示，其余只读回看）。

        Args:
            experiment_id: 试验标识。

        Returns:
            成功显示时为 True。
        """
        if not self.navigate("monitor"):
            return False
        page = self._pages.get("monitor")
        return isinstance(page, MonitorPage) and page.open_experiment(experiment_id)

    def open_report(self, experiment_id: str) -> bool:
        """打开报告中心显示某个试验的报告。

        Args:
            experiment_id: 试验标识。

        Returns:
            成功显示时为 True。
        """
        self._pending_report_experiment = experiment_id
        if not self.navigate("reports"):
            self._pending_report_experiment = None
            return False
        return True

    def open_batch_report(self, kind: str, batch_no: str) -> bool:
        """打开报告中心显示某个批号的单批次报告。

        Args:
            kind: 批号种类（``BatchKind`` 的值）。
            batch_no: 批号。

        Returns:
            成功显示时为 True。
        """
        if not self.navigate("reports"):
            return False
        page = self._pages.get("reports")
        return isinstance(page, ReportsPage) and page.open_batch(BatchKind(kind), batch_no)

    def open_tube(self, experiment_id: str, tube_no: int) -> bool:
        """打开单管详情页。

        Args:
            experiment_id: 试验标识。
            tube_no: 管号。

        Returns:
            成功显示时为 True。
        """
        if not self.navigate("tube"):
            return False
        page = self._pages.get("tube")
        return isinstance(page, TubeDetailPage) and page.open_tube(experiment_id, tube_no)

    def _back_to_monitor(self) -> None:
        self.navigate("monitor")

    def _on_reviewed(self, experiment_id: str) -> None:
        monitor = self._pages.get("monitor")
        if isinstance(monitor, MonitorPage) and monitor.experiment_id == experiment_id:
            monitor.reload()
        dashboard = self._pages.get("dashboard")
        if isinstance(dashboard, DashboardPage):
            dashboard.refresh()

    def _on_experiment_started(self, experiment_id: str) -> None:
        """向导启动了试验：留在向导页的用户跳到试验监控页。"""
        _log.info("试验 %s 已启动", experiment_id)
        if self._current_route == "new":
            self.open_experiment(experiment_id)

    def _drop_pages(self) -> None:
        for page in self._pages.values():
            if isinstance(page, MonitorPage | TubeDetailPage):
                page.shutdown()
            self.page_stack.removeWidget(page)
            page.deleteLater()
        self._pages.clear()

    def _on_quick_action(self, action_id: str) -> None:
        key = QUICK_ACTION_ROUTES.get(action_id)
        if action_id == "print_report":
            # “打印报告”：带上监控页正在看的试验，到报告中心预览和打印。
            monitor = self._pages.get("monitor")
            if isinstance(monitor, MonitorPage) and monitor.experiment_id is not None:
                self._pending_report_experiment = monitor.experiment_id
        if key is not None:
            self.navigate(key)
            self._pending_report_experiment = None

    def _on_user_action(self, action: str) -> None:
        if action == "logout":
            self.logout()
        elif action == "lock":
            self.lock_screen()
        elif action == "change_password":
            self.change_password()

    def change_password(self) -> None:
        """打开修改密码对话框。"""
        session = self._session
        if session is None:
            return
        dialog = ChangePasswordDialog(
            lambda old, new: self._services.auth.change_password(session, old, new),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            Toast.show_message(self, self.tr("密码已修改"), ToastKind.SUCCESS)

    # ------------------------------------------------------------------ 事件

    def _on_instrument(self, status: InstrumentStatus) -> None:
        self.shell.toolbar.set_instrument_status(status.connected)

    def _on_temperature(self, event: TemperatureChanged) -> None:
        self.shell.toolbar.set_instrument_status(event.celsius is not None)

    def _on_state_changed(self, event: StateChanged) -> None:
        _log.info(
            "试验 %s：%s → %s", event.experiment_id, event.previous.value, event.current.value
        )

    def _on_alert(self, alert: EngineAlert) -> None:
        if self._screen is not Screen.SHELL:
            return
        kind = {
            "info": ToastKind.INFO,
            "warning": ToastKind.WARNING,
            "error": ToastKind.DANGER,
        }[alert.level.value]
        text = describe_alert(
            alert.code, alert.message, alert.row_index, closed=alert.level is AlertLevel.INFO
        )
        Toast.show_message(self, text, kind, 6000)

    def active_experiment_id(self) -> str | None:
        """正在运行的试验。

        Returns:
            试验标识，没有时为 None。
        """
        return self._services.engine.active_experiment_id

    # ------------------------------------------------------------------ 语言

    def _apply_language_widgets(self) -> None:
        options = self._language.options()
        current = self._language.current.value
        self.shell.toolbar.set_language_options(options, current)
        for page in (self.setup_page, self.login_page, self.lock_page):
            page.set_language_options(options, current)

    def _on_language_selected(self, code: str) -> None:
        self._language.set_language(code)

    def _on_language_changed(self, code: str) -> None:
        self._apply_language_widgets()
        reports = self._pages.get("reports")
        if isinstance(reports, ReportsPage):
            reports.set_ui_language(code)
        session = self._session
        if session is not None:
            user = session.user
            self.shell.toolbar.set_user(user.display_name, role_label(user.role))
            if self._screen is Screen.LOCK:
                self.lock_page.set_mode(
                    LoginMode.LOCKED,
                    username=user.username,
                    user_label=f"{user.display_name} · {role_label(user.role)}",
                )
        self.retranslate()

    def retranslate(self) -> None:
        """刷新窗口标题；各页面和组件自己响应 ``LanguageChange``。"""
        self.setWindowTitle(self._window_title())

    def _window_title(self) -> str:
        base = self.tr("凝胶智检系统 GelInspect")
        if self._session is None:
            return base
        return f"{route_title(route_for(self._current_route))} - {base}"

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新窗口标题。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    # ------------------------------------------------------------------ 退出

    def confirm_close(self) -> bool:
        """退出前的确认：有试验在进行时要用户确认；锁屏时必须先解锁。

        Returns:
            可以退出时为 True。
        """
        running = self.active_experiment_id()
        if running is None:
            return True
        if self._screen is Screen.LOCK:
            Toast.show_message(self, self.tr("有试验正在进行，请先解锁再退出"), ToastKind.WARNING)
            return False
        return ConfirmDialog.ask(
            self,
            self.tr("退出并中断试验？"),
            self.tr(
                "试验 {id} 仍在进行。退出会中断它并标记为异常，设备状态需要人工检查，"
                "未读数的排会错过读数时间。确定要退出吗？"
            ).format(id=running),
            confirm_text=self.tr("中断并退出"),
            cancel_text=self.tr("继续试验"),
            danger=True,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """关窗：必要时确认，然后停止监视、断开桥并退出登录。

        引擎、视频管线和数据库由应用启动代码在事件循环结束后有序关闭。

        Args:
            event: 关闭事件。
        """
        if not self._closing and not self.confirm_close():
            event.ignore()
            return
        self._closing = True
        self.idle.stop()
        self.bridge.close()
        session = self._session
        if session is not None:
            try:
                self._services.auth.logout(session)
            except Exception:
                _log.exception("退出时写审计失败")
            self._session = None
        event.accept()


__all__ = ["MainWindow", "Screen"]
