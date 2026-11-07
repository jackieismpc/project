"""登录页、锁屏页、创建管理员页和修改密码对话框。

页面只负责输入和显示，不调用服务：提交的内容由信号交给主窗口，错误提示用工厂函数传入，
这样语言切换后已显示的错误也会换成当前语言。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from functools import partial

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gelinspect.interfaces.bootstrap import DemoAccount
from gelinspect.services.auth import AuthError
from gelinspect.ui.labels import describe_auth_error, role_label
from gelinspect.ui.theme.tokens import current_tokens
from gelinspect.ui.widgets.button import Button, ButtonVariant
from gelinspect.ui.widgets.card import Card
from gelinspect.ui.widgets.icons import IconLoader
from gelinspect.ui.widgets.label import Label

CARD_WIDTH = 440
"""登录卡片宽度（px）。"""

ErrorFactory = Callable[[], str]
"""生成当前语言的错误文字，语言切换后重新调用。"""


class LoginMode(StrEnum):
    """登录页的两种用法。"""

    LOGIN = "login"
    """登录：用户名可编辑。"""
    LOCKED = "locked"
    """锁屏：用户名固定，只输入密码解锁。"""


def _field(placeholder_echo: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setMinimumHeight(current_tokens().space.touch)
    if placeholder_echo:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class _LanguageRow(QWidget):
    """卡片底部的语言切换：每种语言一个文字按钮，当前语言加粗。"""

    languageRequested = Signal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建语言行。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}
        icon = Label()
        icon.setPixmap(IconLoader.pixmap("languages", 16, "ink_muted"))
        self._layout.addStretch(1)
        self._layout.addWidget(icon)
        self._buttons_layout = QHBoxLayout()
        self._buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._buttons_layout.setSpacing(8)
        self._layout.addLayout(self._buttons_layout)
        self._layout.addStretch(1)

    def set_options(self, options: Sequence[tuple[str, str]], current: str) -> None:
        """设置可选语言。

        Args:
            options: ``(语言代码, 语言自己的名字)``。
            current: 当前语言代码。
        """
        for button in self._buttons.values():
            self._buttons_layout.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()
        for code, name in options:
            button = QPushButton(name)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setCheckable(True)
            button.setChecked(code == current)
            button.setProperty("role", "link")
            button.clicked.connect(lambda _=False, c=code: self.languageRequested.emit(c))
            self._buttons[code] = button
            self._buttons_layout.addWidget(button)


class _AuthFrame(QWidget):
    """居中卡片的页面骨架：品牌行、标题、表单区、语言行。"""

    languageRequested = Signal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建骨架。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self.setProperty("role", "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        column = QVBoxLayout()
        column.setSpacing(14)
        self.card = Card()
        self.card.setFixedWidth(CARD_WIDTH)
        self.card.body.setContentsMargins(28, 26, 28, 22)
        self.card.body.setSpacing(10)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        mark = Label()
        mark.setPixmap(IconLoader.pixmap("shield-check", 32, "primary"))
        brand.addWidget(mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.brand_title = Label("", "h1")
        self.brand_sub = Label("GelInspect", "meta")
        names.addWidget(self.brand_title)
        names.addWidget(self.brand_sub)
        brand.addLayout(names, 1)
        self.card.body.addLayout(brand)
        self.card.body.addSpacing(6)
        self.heading = Label("", "h2")
        self.card.body.addWidget(self.heading)
        self.form = QVBoxLayout()
        self.form.setSpacing(6)
        self.card.body.addLayout(self.form)
        self.languages = _LanguageRow()
        self.languages.languageRequested.connect(self.languageRequested)
        self.card.body.addSpacing(4)
        self.card.body.addWidget(self.languages)
        column.addWidget(self.card)
        self.extras = QVBoxLayout()
        column.addLayout(self.extras)
        row.addLayout(column)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        self.setMinimumWidth(CARD_WIDTH + 48)

    def set_language_options(self, options: Sequence[tuple[str, str]], current: str) -> None:
        """设置底部语言切换。

        Args:
            options: ``(语言代码, 语言自己的名字)``。
            current: 当前语言代码。
        """
        self.languages.set_options(options, current)

    def labelled(self, label: Label, edit: QWidget) -> None:
        """向表单区添加“标签 + 输入框”。

        Args:
            label: 标签。
            edit: 输入控件。
        """
        label.setContentsMargins(0, 6, 0, 0)
        self.form.addWidget(label)
        self.form.addWidget(edit)

    def retranslate_brand(self) -> None:
        """刷新品牌行文字。"""
        self.brand_title.setText(self.tr("凝胶智检系统"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新品牌行，子类在其后刷新自己的文字。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate_brand()
        super().changeEvent(event)


class _DemoRow(QPushButton):
    """演示账号的一行，点击把账号和密码填入登录框。"""

    def __init__(self, account: DemoAccount, parent: QWidget | None = None) -> None:
        """创建一行。

        Args:
            account: 演示账号。
            parent: 父控件。
        """
        super().__init__(parent)
        self.account = account
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)
        self.name = Label(account.username, "body", mono=True)
        self.password = Label(account.password, "meta", mono=True)
        self.role = Label("", "meta")
        for label in (self.name, self.password, self.role):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.name)
        layout.addWidget(self.password, 1)
        layout.addWidget(self.role)
        self.retranslate()

    def retranslate(self) -> None:
        """刷新角色名和提示。"""
        role = role_label(self.account.role)
        self.role.setText(role)
        self.setToolTip(self.tr("点击填入登录框"))
        self.setAccessibleName(
            self.tr("演示账号 {user}，{role}").format(user=self.account.username, role=role)
        )

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)


class LoginPage(_AuthFrame):
    """登录页，也用作锁屏页。

    Signals:
        submitted: 点了登录（或解锁），参数为用户名和密码。
        logoutRequested: 锁屏模式下点了“退出登录”。
    """

    submitted = Signal(str, str)
    logoutRequested = Signal()  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建登录页。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._mode = LoginMode.LOGIN
        self._locked_user_label = ""
        self._error_factory: ErrorFactory | None = None
        self._lock_remaining = 0
        self._demo_rows: list[_DemoRow] = []
        self._demo_card: Card | None = None

        self._user_label = Label("", "lab")
        self.username = _field()
        self.username.setAccessibleName("username")
        self._password_label = Label("", "lab")
        self.password = _field(True)
        self.labelled(self._user_label, self.username)
        self.labelled(self._password_label, self.password)
        self.error_label = Label("", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self.form.addWidget(self.error_label)
        self.form.addSpacing(6)
        self.submit_button = Button("", ButtonVariant.PRIMARY)
        self.submit_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form.addWidget(self.submit_button)
        self.logout_button = Button("", ButtonVariant.SECONDARY)
        self.logout_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form.addWidget(self.logout_button)
        self.logout_button.setVisible(False)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)

        self.submit_button.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self.password.setFocus)
        self.logout_button.clicked.connect(self.logoutRequested)
        self.retranslate()

    # ------------------------------------------------------------------ 公共接口

    def mode(self) -> LoginMode:
        """当前模式。

        Returns:
            :class:`LoginMode`。
        """
        return self._mode

    def set_mode(self, mode: LoginMode, *, username: str = "", user_label: str = "") -> None:
        """切换登录或锁屏模式。

        Args:
            mode: 模式。
            username: 锁屏模式下固定的登录名。
            user_label: 锁屏模式下显示的用户（显示名加角色）。
        """
        self._mode = mode
        locked = mode is LoginMode.LOCKED
        self._locked_user_label = user_label
        self.username.setReadOnly(locked)
        self.username.setText(username if locked else "")
        self.username.setVisible(not locked)
        self._user_label.setVisible(not locked)
        self.logout_button.setVisible(locked)
        if self._demo_card is not None:
            self._demo_card.setVisible(not locked and bool(self._demo_rows))
        self.clear_error()
        self.password.clear()
        self.retranslate()
        self.focus_input()

    def focus_input(self) -> None:
        """把焦点放到第一个需要输入的框。"""
        if self._mode is LoginMode.LOCKED:
            self.password.setFocus()
        else:
            self.username.setFocus()

    def set_demo_accounts(self, accounts: Sequence[DemoAccount]) -> None:
        """显示“演示账号”提示；空序列表示不显示（真实配置）。

        Args:
            accounts: 演示账号。
        """
        if self._demo_card is not None:
            self.extras.removeWidget(self._demo_card)
            self._demo_card.deleteLater()
            self._demo_card = None
        self._demo_rows = []
        if not accounts:
            return
        card = Card(role="tile")
        card.setFixedWidth(CARD_WIDTH)
        self._demo_title = Label("", "lab")
        card.body.addWidget(self._demo_title)
        card.body.setSpacing(6)
        for account in accounts:
            row = _DemoRow(account)
            row.clicked.connect(lambda _=False, a=account: self._fill(a))
            card.body.addWidget(row)
            self._demo_rows.append(row)
        self.extras.addWidget(card)
        self._demo_card = card
        card.setVisible(self._mode is LoginMode.LOGIN)
        self.retranslate()

    def show_error(self, factory: ErrorFactory | str) -> None:
        """显示错误；传入工厂函数时，语言切换后会重新生成文字。

        Args:
            factory: 错误文字，或生成当前语言错误文字的函数。
        """
        self._error_factory = (lambda: factory) if isinstance(factory, str) else factory
        self._render_error()

    def show_auth_error(self, error: AuthError) -> None:
        """显示账号错误，并在被锁定时开始倒计时。

        Args:
            error: 服务层异常。
        """
        self.show_error(partial(describe_auth_error, error))
        retry = error.params.get("retry_after_s")
        if retry is not None:
            self.start_lockout(int(retry) + 1)

    def clear_error(self) -> None:
        """清除错误提示。"""
        self._error_factory = None
        self.error_label.setVisible(False)
        self.error_label.setText("")

    def start_lockout(self, seconds: int) -> None:
        """锁定期间禁用输入并显示倒计时。

        Args:
            seconds: 锁定秒数。
        """
        self._lock_remaining = max(0, seconds)
        self._set_inputs_enabled(self._lock_remaining == 0)
        if self._lock_remaining > 0:
            self._countdown.start()
            self._render_error()

    def is_locked_out(self) -> bool:
        """是否正处于失败次数过多的锁定倒计时中。

        Returns:
            锁定中为 True。
        """
        return self._lock_remaining > 0

    def reset(self) -> None:
        """清空密码和错误（退出登录后回到登录页时调用）。"""
        self.password.clear()
        self.clear_error()
        self._countdown.stop()
        self._lock_remaining = 0
        self._set_inputs_enabled(True)

    # ------------------------------------------------------------------ 内部

    def _fill(self, account: DemoAccount) -> None:
        if self._lock_remaining > 0:
            return
        self.username.setText(account.username)
        self.password.setText(account.password)
        self.submit_button.setFocus()

    def _submit(self) -> None:
        if self._lock_remaining > 0:
            return
        name = self.username.text().strip()
        if self._mode is LoginMode.LOGIN and not name:
            self.show_error(lambda: self.tr("请输入用户名"))
            self.username.setFocus()
            return
        if not self.password.text():
            self.show_error(lambda: self.tr("请输入密码"))
            self.password.setFocus()
            return
        self.submitted.emit(name, self.password.text())

    def _tick(self) -> None:
        self._lock_remaining -= 1
        if self._lock_remaining <= 0:
            self._countdown.stop()
            self._lock_remaining = 0
            self._set_inputs_enabled(True)
            self.clear_error()
            return
        self._render_error()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (self.username, self.password, self.submit_button):
            widget.setEnabled(enabled)

    def _render_error(self) -> None:
        if self._lock_remaining > 0:
            minutes, seconds = divmod(self._lock_remaining, 60)
            text = self.tr("失败次数过多，已暂时锁定，{time} 后可再试").format(
                time=f"{minutes:02d}:{seconds:02d}"
            )
        elif self._error_factory is not None:
            text = self._error_factory()
        else:
            return
        self.error_label.setText(text)
        self.error_label.setVisible(True)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.retranslate_brand()
        self._user_label.setText(self.tr("用户名"))
        self._password_label.setText(self.tr("密码"))
        if self._mode is LoginMode.LOCKED:
            self.heading.setText(self.tr("屏幕已锁定"))
            self.brand_sub.setText(self._locked_user_label or "GelInspect")
            self.submit_button.setText(self.tr("解锁"))
            self.logout_button.setText(self.tr("退出登录"))
            self.password.setPlaceholderText(self.tr("输入密码解锁，试验仍在后台运行"))
        else:
            self.heading.setText(self.tr("登录"))
            self.brand_sub.setText("GelInspect")
            self.submit_button.setText(self.tr("登录"))
            self.password.setPlaceholderText("")
        self.submit_button.setAccessibleName(self.submit_button.text())
        self.logout_button.setAccessibleName(self.logout_button.text())
        if self._demo_card is not None:
            self._demo_title.setText(self.tr("演示账号（模拟配置，点击填入）"))
        self._render_error()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class SetupPage(_AuthFrame):
    """首次启动（真实配置）的“创建管理员”页。

    Signals:
        submitted: 点了创建，参数为登录名、显示名称、密码。
    """

    submitted = Signal(str, str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建页面。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._error_factory: ErrorFactory | None = None
        self._notes = Label("", "meta")
        self._notes.setWordWrap(True)
        self.form.addWidget(self._notes)
        self._user_label = Label("", "lab")
        self.username = _field()
        self.username.setText("admin")
        self._name_label = Label("", "lab")
        self.display_name = _field()
        self._pw_label = Label("", "lab")
        self.password = _field(True)
        self._confirm_label = Label("", "lab")
        self.confirm = _field(True)
        self.labelled(self._user_label, self.username)
        self.labelled(self._name_label, self.display_name)
        self.labelled(self._pw_label, self.password)
        self.labelled(self._confirm_label, self.confirm)
        self.error_label = Label("", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self.form.addWidget(self.error_label)
        self.form.addSpacing(6)
        self.submit_button = Button("", ButtonVariant.PRIMARY)
        self.submit_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form.addWidget(self.submit_button)
        self.submit_button.clicked.connect(self._submit)
        self.confirm.returnPressed.connect(self._submit)
        self.retranslate()

    def show_error(self, factory: ErrorFactory | str) -> None:
        """显示错误。

        Args:
            factory: 错误文字，或生成当前语言错误文字的函数。
        """
        self._error_factory = (lambda: factory) if isinstance(factory, str) else factory
        self._render_error()

    def show_auth_error(self, error: AuthError) -> None:
        """显示账号错误。

        Args:
            error: 服务层异常。
        """
        self.show_error(partial(describe_auth_error, error))

    def _render_error(self) -> None:
        if self._error_factory is None:
            self.error_label.setVisible(False)
            return
        self.error_label.setText(self._error_factory())
        self.error_label.setVisible(True)

    def _submit(self) -> None:
        if self.password.text() != self.confirm.text():
            self.show_error(lambda: self.tr("两次输入的密码不一致"))
            return
        self._error_factory = None
        self._render_error()
        self.submitted.emit(
            self.username.text().strip(), self.display_name.text().strip(), self.password.text()
        )

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.retranslate_brand()
        self.heading.setText(self.tr("创建管理员"))
        self._notes.setText(
            self.tr("这是第一次启动，系统里还没有账号。请创建管理员账号；首次登录后需要修改密码。")
        )
        self._user_label.setText(self.tr("登录名"))
        self._name_label.setText(self.tr("显示名称"))
        self._pw_label.setText(self.tr("密码（至少 8 个字符）"))
        self._confirm_label.setText(self.tr("确认密码"))
        self.submit_button.setText(self.tr("创建管理员"))
        self.submit_button.setAccessibleName(self.submit_button.text())
        self._render_error()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


class ChangePasswordDialog(QDialog):
    """修改密码对话框。强制模式（首次登录）下取消等于退出登录。"""

    def __init__(
        self,
        apply: Callable[[str, str], None],
        *,
        forced: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """创建对话框。

        Args:
            apply: 执行修改的函数 ``(旧密码, 新密码)``，失败时抛 :class:`AuthError`。
            forced: 是否为首次登录强制改密。
            parent: 父窗口。
        """
        super().__init__(parent)
        self._apply = apply
        self._forced = forced
        self._error_factory: ErrorFactory | None = None
        self.setProperty("role", "dialog")
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(6)
        self._title = Label("", "h2")
        self._intro = Label("", "body")
        self._intro.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._intro)
        self._old_label = Label("", "lab")
        self.old = _field(True)
        self._new_label = Label("", "lab")
        self.new = _field(True)
        self._confirm_label = Label("", "lab")
        self.confirm = _field(True)
        for label, edit in (
            (self._old_label, self.old),
            (self._new_label, self.new),
            (self._confirm_label, self.confirm),
        ):
            label.setContentsMargins(0, 6, 0, 0)
            layout.addWidget(label)
            layout.addWidget(edit)
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
        self.retranslate()

    def _submit(self) -> None:
        if self.new.text() != self.confirm.text():
            self._show(lambda: self.tr("两次输入的新密码不一致"))
            return
        try:
            self._apply(self.old.text(), self.new.text())
        except AuthError as error:
            self._show(partial(describe_auth_error, error))
            return
        self.accept()

    def _show(self, factory: ErrorFactory) -> None:
        self._error_factory = factory
        self.error_label.setText(factory())
        self.error_label.setVisible(True)

    def retranslate(self) -> None:
        """按当前语言刷新全部文字。"""
        self.setWindowTitle(self.tr("修改密码"))
        self._title.setText(self.tr("修改密码"))
        if self._forced:
            self._intro.setText(self.tr("这是初始密码，请先设置自己的新密码，之后才能使用系统。"))
            self.cancel_button.setText(self.tr("退出登录"))
        else:
            self._intro.setText(self.tr("请输入当前密码和新密码。"))
            self.cancel_button.setText(self.tr("取消"))
        self._old_label.setText(self.tr("当前密码"))
        self._new_label.setText(self.tr("新密码（至少 8 个字符）"))
        self._confirm_label.setText(self.tr("确认新密码"))
        self.ok_button.setText(self.tr("修改密码"))
        for button in (self.cancel_button, self.ok_button):
            button.setAccessibleName(button.text())
        if self._error_factory is not None:
            self.error_label.setText(self._error_factory())

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """语言切换时刷新文字。

        Args:
            event: 事件。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


__all__ = ["ChangePasswordDialog", "LoginMode", "LoginPage", "SetupPage"]
