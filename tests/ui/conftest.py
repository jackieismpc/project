"""界面测试夹具：离屏平台并加载主题与字体。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gelinspect.ui.theme.stylesheet import apply_theme  # noqa: E402


@pytest.fixture(autouse=True)
def themed(qapp: QApplication) -> None:
    apply_theme(qapp)


# ---------------------------------------------------------------------------
# P7：主窗口、登录与多语言测试共用的夹具

import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from gelinspect.app_context import AppContext, build_app_context  # noqa: E402
from gelinspect.services.config import DEFAULT_CONFIG, parse_config  # noqa: E402
from gelinspect.ui.i18n import (  # noqa: E402
    Language,
    LanguageManager,
    MemoryLanguageSettings,
)
from gelinspect.ui.main_window import MainWindow, Screen  # noqa: E402
from gelinspect.ui.widgets.app_shell import MemoryShellSettings  # noqa: E402
from simulation.demo_accounts import DEMO_ACCOUNTS  # noqa: E402

DEMO_PASSWORDS = {a.username: a.password for a in DEMO_ACCOUNTS}

WORKER_PREFIXES = ("experiment-engine", "engine-", "video-", "instrument-monitor")


def worker_threads() -> list[str]:
    """本项目创建的后台线程名。"""
    return [t.name for t in threading.enumerate() if t.name.startswith(WORKER_PREFIXES)]


def settle(app: QApplication, rounds: int = 4) -> None:
    """处理排队的事件和布局请求，让语言切换等效果落到界面上。"""
    for _ in range(rounds):
        app.processEvents()


def wait_for(app: QApplication, predicate: Callable[[], object], timeout_s: float = 10.0) -> bool:
    """一边处理事件一边等条件成立。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def make_config(tmp_path: Path, **overrides: object) -> dict[str, object]:
    """默认的模拟配置，数据目录放到临时目录。"""
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    config["data_dir"] = str(tmp_path / "data")
    config.update(overrides)
    return config


@pytest.fixture
def app_context(tmp_path: Path) -> Iterator[AppContext]:
    """用模拟后端装配好的应用，退出时有序关闭并确认没有线程残留。"""
    context = build_app_context(parse_config(make_config(tmp_path)))
    yield context
    context.close()
    deadline = time.monotonic() + 8.0
    while worker_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker_threads() == []


@pytest.fixture
def language(qapp: QApplication) -> Iterator[LanguageManager]:
    """内存持久化的语言管理器，测试结束时切回简体中文。"""
    manager = LanguageManager(qapp, settings=MemoryLanguageSettings())
    yield manager
    manager.set_language(Language.ZH_CN, persist=False)


@pytest.fixture
def window(
    qapp: QApplication, app_context: AppContext, language: LanguageManager
) -> Iterator[MainWindow]:
    """显示出来的主窗口（1280×800），关闭动画。"""
    win = MainWindow(
        app_context.services(),
        language,
        shell_settings=MemoryShellSettings(),
        animations_enabled=False,
        idle_timeout_s=0,
    )
    win.resize(1280, 800)
    win.show()
    settle(qapp)
    yield win
    win.bridge.close()
    win.close()
    win.deleteLater()
    settle(qapp)


def sign_in(app: QApplication, win: MainWindow, username: str) -> None:
    """用演示账号在登录页登录。"""
    win.login_page.username.setText(username)
    win.login_page.password.setText(DEMO_PASSWORDS[username])
    win.login_page.submit_button.click()
    settle(app)


def _no_modal(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("测试中不应弹出模态对话框（会阻塞），请在测试里显式替换它")


@pytest.fixture(autouse=True)
def forbid_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """模态对话框的 exec 会一直阻塞测试：默认让它立刻失败，需要的测试自己替换。"""
    from gelinspect.ui.pages.auth_pages import ChangePasswordDialog
    from gelinspect.ui.widgets.dialog import ConfirmDialog, ErrorDialog

    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(_no_modal))
    monkeypatch.setattr(ErrorDialog, "exec", _no_modal)
    monkeypatch.setattr(ChangePasswordDialog, "exec", _no_modal)


def screen_of(win: MainWindow) -> Screen:
    """当前界面（经函数返回，避免类型检查器按之前的断言收窄属性）。"""
    return win.current_screen


@pytest.fixture(autouse=True)
def delete_leftover_widgets(qapp: QApplication) -> Iterator[None]:
    """测试结束后销毁所有顶层控件。

    全局样式表每次重新套用都要重新润色所有存活的控件，窗口泄漏会让之后的每个测试越来越慢。
    """
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
