"""图形界面的启动流程：日志、语言、主题、组装依赖、主窗口、有序退出。

``uv run gelinspect`` 不带参数就走这里。首次启动自动创建 ``data/``（数据库、日志），
运行迁移并初始化，默认加载 ``simulation/`` 后端。任何启动失败都弹出说明原因的对话框
并写日志，不会只有 traceback。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget
from sqlalchemy.exc import SQLAlchemyError

from gelinspect.app_context import AppContext, build_app_context
from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.db.seed import SYSTEM_ACTOR
from gelinspect.infrastructure.security.integrity import (
    IntegrityReport,
    IntegrityStatus,
    verify_manifest,
)
from gelinspect.interfaces.storage import DatabaseVersionError, StorageError
from gelinspect.logging_setup import (
    configure_logging,
    install_exception_hooks,
    shutdown_logging,
)
from gelinspect.services.config import (
    AppConfig,
    ConfigError,
    default_config_file,
    load_config,
)
from gelinspect.services.runtime_paths import (
    DATA_DIR_ENV,
    USER_CONFIG_FILE,
    RunMode,
    app_root,
    build_info,
    default_data_dir,
    run_mode,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_DATABASE = 3

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartupFailure:
    """启动失败的说明。

    Attributes:
        title: 对话框标题。
        message: 面向用户的说明，包含怎么办。
        details: 技术细节。
        exit_code: 进程退出码。
    """

    title: str
    message: str
    details: str
    exit_code: int


ErrorPresenter = Callable[[StartupFailure, Path | None], None]
"""显示启动失败的函数，测试里替换成记录器。"""


def describe_startup_error(error: BaseException, config: AppConfig | None = None) -> StartupFailure:
    """把启动阶段的异常翻译成用户能处理的说明。

    必须在 ``QApplication`` 创建之后调用，说明文字按当前界面语言翻译。

    Args:
        error: 异常。
        config: 已读取的配置，用来在说明里写出数据库路径。

    Returns:
        失败说明。
    """
    details = "".join(traceback.format_exception_only(type(error), error)).strip()
    db_path = (
        str(config.database_path)
        if config is not None
        else QCoreApplication.translate("Startup", "（数据目录下的数据库文件）")
    )
    if isinstance(error, ConfigError):
        return StartupFailure(
            QCoreApplication.translate("Startup", "配置有误，无法启动"),
            QCoreApplication.translate(
                "Startup",
                "{error}\n\n请修改配置文件后重新启动；不带 --config 参数启动会使用默认的模拟配置。",
            ).format(error=error),
            details,
            EXIT_CONFIG,
        )
    if isinstance(error, DatabaseVersionError):
        return StartupFailure(
            QCoreApplication.translate("Startup", "数据库版本不兼容"),
            QCoreApplication.translate(
                "Startup",
                "数据库 {path} 由更新版本的程序创建，当前版本无法打开。"
                "请升级软件，或改用与本版本匹配的数据目录。",
            ).format(path=db_path),
            details,
            EXIT_DATABASE,
        )
    text = str(error).lower()
    if "locked" in text or "busy" in text:
        return StartupFailure(
            QCoreApplication.translate("Startup", "数据库被占用"),
            QCoreApplication.translate(
                "Startup",
                "数据库 {path} 正被另一个程序占用，可能是另一个正在运行的 GelInspect。"
                "请关闭它们后重新启动。",
            ).format(path=db_path),
            details,
            EXIT_DATABASE,
        )
    if isinstance(error, (SQLAlchemyError, sqlite3.Error, StorageError, OSError)):
        return StartupFailure(
            QCoreApplication.translate("Startup", "无法打开数据库或数据目录"),
            QCoreApplication.translate(
                "Startup",
                "无法使用数据库 {path}。请确认数据目录存在、有读写权限且磁盘未满，然后重新启动。",
            ).format(path=db_path),
            details,
            EXIT_DATABASE,
        )
    return StartupFailure(
        QCoreApplication.translate("Startup", "启动失败"),
        QCoreApplication.translate("Startup", "程序启动时遇到意外错误。请把日志文件发给技术支持。"),
        details,
        EXIT_ERROR,
    )


def show_error_dialog(failure: StartupFailure, log_path: Path | None) -> None:
    """用错误对话框显示启动失败（阻塞到用户关闭）。

    Args:
        failure: 失败说明。
        log_path: 日志文件，用于在对话框里提示位置。
    """
    from gelinspect.ui.widgets.dialog import ErrorDialog

    dialog = ErrorDialog(
        failure.title,
        failure.message,
        details=failure.details,
        log_path=str(log_path) if log_path else "",
    )
    dialog.exec()


class _CrashRelay(QObject):
    """把任意线程里的未捕获异常转到主线程弹出对话框，同一时间只弹一个。"""

    crashed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._showing = False
        self.crashed.connect(self._show)

    def report(self, error: BaseException, log_path: Path) -> None:
        """异常钩子的回调，可在任意线程调用。

        Args:
            error: 异常。
            log_path: 日志文件。
        """
        detail = "".join(traceback.format_exception_only(type(error), error)).strip()
        self.crashed.emit(detail, str(log_path))

    @Slot(str, str)
    def _show(self, detail: str, log_path: str) -> None:
        if self._showing:
            return
        self._showing = True
        try:
            from gelinspect.ui.widgets.dialog import ErrorDialog

            ErrorDialog(
                QCoreApplication.translate("Startup", "程序遇到未处理的错误"),
                QCoreApplication.translate(
                    "Startup",
                    "程序遇到了意外错误，已写入日志。当前操作可能没有完成；建议保存工作后重新启动。",
                ),
                details=detail,
                log_path=log_path,
            ).exec()
        finally:
            self._showing = False


def resolve_log_dir(config: AppConfig | None) -> Path:
    """日志目录：数据目录下的 ``logs``；配置读不出来时用默认数据目录下的 ``logs``。

    Args:
        config: 已读取的配置，可为 None。

    Returns:
        日志目录。
    """
    base = config.data_dir if config is not None else default_data_dir()
    return base / "logs"


def _start_logging(log_dir: Path) -> Path | None:
    try:
        return configure_logging(log_dir)
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "gelinspect-logs"
        print(f"警告：无法写入日志目录 {log_dir}（{exc}），改用 {fallback}", file=sys.stderr)
        try:
            return configure_logging(fallback)
        except OSError:
            return None


def run_gui(
    *,
    config_path: Path | None = None,
    data_dir: Path | None = None,
    smoke_test: bool = False,
    present_error: ErrorPresenter | None = None,
    app: QApplication | None = None,
) -> int:
    """启动图形界面并运行到退出。

    Args:
        config_path: 配置文件，None 时仓库模式用默认的模拟配置，发布模式用数据目录里的
            ``config.yaml`` 或发布包内的默认配置。
        data_dir: 覆盖配置里的数据目录（``--data-dir``）；None 时看环境变量
            ``GELINSPECT_DATA_DIR``。
        smoke_test: 冒烟测试：完整启动、登录、切换语言、关窗退出，不进入事件循环。
        present_error: 显示启动失败的函数，默认弹出错误对话框；冒烟测试不弹窗，只打印到 stderr。
        app: 已有的 ``QApplication``（测试传入）；None 时自己创建。

    Returns:
        进程退出码：0 正常，1 其他错误，2 配置有误，3 数据库问题。
    """
    config: AppConfig | None = None
    config_error: ConfigError | None = None
    if data_dir is None and os.environ.get(DATA_DIR_ENV):
        data_dir = Path(os.environ[DATA_DIR_ENV]).expanduser()
    if run_mode() is RunMode.PACKAGED:
        # 发布包里 .py 迁移脚本由 Alembic 按文件加载：不要往程序目录写 __pycache__。
        sys.dont_write_bytecode = True
        if config_path is None and data_dir is not None:
            candidate = data_dir / USER_CONFIG_FILE
            config_path = candidate if candidate.is_file() else None
    try:
        config = load_config(config_path)
        if data_dir is not None:
            config = config.model_copy(update={"data_dir": data_dir})
    except ConfigError as exc:
        config_error = exc
    log_dir = (
        resolve_log_dir(config) if config is not None or data_dir is None else data_dir / "logs"
    )
    log_path = _start_logging(log_dir)
    _log.info(
        "GelInspect 启动 mode=%s config=%s smoke=%s",
        run_mode().value,
        config_path or default_config_file() or "<内置模拟配置>",
        smoke_test,
    )

    owns_app = app is None
    qapp = app or QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(qapp, QApplication)
    qapp.setApplicationName("GelInspect")
    qapp.setOrganizationName("GelInspect")

    from gelinspect.ui.i18n import LanguageManager, MemoryLanguageSettings, detect_system_language
    from gelinspect.ui.theme.stylesheet import apply_theme
    from gelinspect.ui.widgets.app_shell import MemoryShellSettings

    apply_theme(qapp)
    language = LanguageManager(
        qapp,
        settings=MemoryLanguageSettings() if smoke_test else None,
        default_language=None if smoke_test else detect_system_language(),
    )
    relay = _CrashRelay(qapp)
    install_exception_hooks(None if smoke_test else relay.report)

    def fail(error: BaseException) -> int:
        failure = describe_startup_error(error, config)
        _log.error("启动失败：%s\n%s", failure.title, failure.details, exc_info=error)
        if smoke_test and present_error is None:
            print(f"启动失败：{failure.title}\n{failure.message}", file=sys.stderr)
        else:
            (present_error or show_error_dialog)(failure, log_path)
        return failure.exit_code

    if config_error is not None or config is None:
        return fail(config_error or ConfigError("配置无法读取"))

    context: AppContext | None = None
    try:
        context = build_app_context(config)
    except Exception as exc:
        return fail(exc)

    integrity = check_program_integrity(config)
    if not record_integrity(context, integrity):
        warning = describe_integrity_failure(integrity)
        if smoke_test:
            print(f"{warning.title}\n{warning.message}", file=sys.stderr)
            context.close()
            shutdown_logging()
            return EXIT_ERROR
        (present_error or show_error_dialog)(warning, log_path)

    from gelinspect.ui.main_window import MainWindow

    window: MainWindow | None = None
    try:
        window = MainWindow(
            context.services(log_dir=log_dir),
            language,
            shell_settings=MemoryShellSettings() if smoke_test else None,
            animations_enabled=not smoke_test,
        )
        window.show()
        if smoke_test:
            run_smoke_checks(qapp, window, language)
            window.close()
            qapp.processEvents()
            _log.info("冒烟测试通过")
            return EXIT_OK
        _install_sigint(qapp)
        code = qapp.exec()
        return int(code)
    except Exception as exc:
        _log.exception("运行中出错")
        if smoke_test:
            print(f"冒烟测试失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        return fail(exc)
    finally:
        if window is not None:
            window.bridge.close()
        context.close()
        shutdown_logging()
        if owns_app:
            qapp.processEvents()


def check_program_integrity(config: AppConfig) -> IntegrityReport:
    """校验发布包的程序文件清单。

    仓库模式（开发运行）没有清单，跳过；配置 ``security.integrity_check: false`` 也跳过。

    Args:
        config: 配置。

    Returns:
        校验结果。
    """
    info = build_info()
    if info is None:
        return IntegrityReport(IntegrityStatus.SKIPPED, "开发模式（仓库运行），跳过程序完整性校验")
    if not config.security.integrity_check:
        return IntegrityReport(IntegrityStatus.SKIPPED, "配置关闭了程序完整性校验")
    return verify_manifest(app_root(RunMode.PACKAGED), info.manifest_public_key)


def record_integrity(context: AppContext, report: IntegrityReport) -> bool:
    """记录完整性校验的结果：通过和跳过写日志，不通过时再写审计。

    Args:
        context: 应用依赖。
        report: 校验结果。

    Returns:
        是否通过（跳过也算通过）。
    """
    if report.ok:
        _log.info("%s（%.2f 秒）", report.message, report.elapsed_s)
        return True
    detail = report.summary()
    _log.error("程序完整性校验未通过：%s", detail)
    try:
        context.database.audit.append(
            AuditEvent(
                occurred_at=datetime.now(UTC),
                actor=SYSTEM_ACTOR,
                actor_role=None,
                action=AuditAction.INTEGRITY_CHECKED,
                detail=f"程序完整性校验未通过（{report.status.value}）：{detail}",
            )
        )
    except Exception:  # 审计写不进去不能挡住启动，日志里已有记录
        _log.exception("完整性校验结果写审计失败")
    return False


def describe_integrity_failure(report: IntegrityReport) -> StartupFailure:
    """完整性校验不通过时的提示（不阻止启动）。

    Args:
        report: 校验结果。

    Returns:
        对话框内容。
    """
    return StartupFailure(
        QCoreApplication.translate("Startup", "程序文件校验未通过"),
        QCoreApplication.translate(
            "Startup",
            "程序目录里有文件与发布时的清单不一致，程序可能被改动或损坏。"
            "这件事已写入审计追踪。请联系管理员核实，必要时重新安装软件。",
        ),
        report.summary(limit=20),
        EXIT_ERROR,
    )


def diagnose(config_path: Path | None = None, data_dir: Path | None = None) -> dict[str, object]:
    """不启动界面，报告运行模式、路径和完整性校验结果（``--diagnose``，打包验证和排错用）。

    Args:
        config_path: 配置文件。
        data_dir: 覆盖数据目录。

    Returns:
        可序列化为 JSON 的诊断信息。
    """
    import importlib.util

    info = build_info()
    result: dict[str, object] = {
        "mode": run_mode().value,
        "app_root": str(app_root()),
        "build": None
        if info is None
        else {"version": info.version, "build_id": info.build_id, "variant": info.variant},
        "default_data_dir": str(default_data_dir()),
        "config_file": str(config_path or default_config_file() or "<DEFAULT_CONFIG>"),
        "simulation_importable": importlib.util.find_spec("simulation") is not None,
    }
    try:
        config = load_config(config_path)
        if data_dir is not None:
            config = config.model_copy(update={"data_dir": data_dir})
        result["data_dir"] = str(config.data_dir)
        result["bootstrap_hook"] = config.bootstrap.hook
        result["device_backend"] = config.device.backend
        report = check_program_integrity(config)
        result["integrity"] = {
            "status": report.status.value,
            "message": report.message,
            "checked": report.checked,
            "elapsed_s": round(report.elapsed_s, 3),
            "modified": list(report.modified[:20]),
            "missing": list(report.missing[:20]),
            "added": list(report.added[:20]),
        }
    except ConfigError as exc:
        result["config_error"] = str(exc)
    return result


def print_diagnosis(config_path: Path | None = None, data_dir: Path | None = None) -> int:
    """打印 :func:`diagnose` 的结果。

    Args:
        config_path: 配置文件。
        data_dir: 覆盖数据目录。

    Returns:
        退出码：完整性校验不通过或配置有误时为 1。
    """
    result = diagnose(config_path, data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    integrity = result.get("integrity")
    status = integrity.get("status") if isinstance(integrity, dict) else None
    return 0 if status in ("ok", "skipped") and "config_error" not in result else 1


def _install_sigint(app: QApplication) -> None:
    """让终端里的 Ctrl+C 走正常退出路径（关闭窗口前不确认，直接有序退出）。"""
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer(app)
    timer.start(250)  # 让 Python 有机会处理信号
    timer.timeout.connect(lambda: None)


def run_smoke_checks(app: QApplication, window: QWidget, language: object) -> None:
    """冒烟检查：登录演示账号、浏览全部页面、切换语言。失败抛出 ``RuntimeError``。

    Args:
        app: 应用对象。
        window: 主窗口（``MainWindow``）。
        language: 语言管理器（``LanguageManager``）。
    """
    from gelinspect.ui.i18n import Language, LanguageManager
    from gelinspect.ui.main_window import MainWindow, Screen
    from gelinspect.ui.routes import PAGE_ROUTES

    assert isinstance(window, MainWindow)
    assert isinstance(language, LanguageManager)

    def check(condition: bool, what: str) -> None:
        if not condition:
            raise RuntimeError(what)

    check(window.current_screen in (Screen.LOGIN, Screen.SETUP), "启动后没有显示登录页")
    accounts = window.services.demo_accounts
    if not accounts:
        check(window.current_screen is Screen.SETUP, "真实配置首次启动应显示创建管理员页")
        for target in (Language.ZH_TW, Language.EN, Language.ZH_CN):
            check(language.set_language(target, persist=False), f"切换到 {target.value} 失败")
        return
    account = next((a for a in accounts if a.username == "admin"), accounts[0])
    login = window.login_page
    login.username.setText(account.username)
    login.password.setText(account.password)
    login.submit_button.click()
    app.processEvents()
    check(window.current_screen is Screen.SHELL, "登录演示账号失败")
    check(window.session is not None, "登录后没有会话")
    for route in PAGE_ROUTES:
        window.navigate(route.key)
        app.processEvents()
    window.navigate("dashboard")
    zh = window.shell.nav.items()[0].text()
    check(language.set_language(Language.EN, persist=False), "切换到英文失败")
    app.processEvents()
    en = window.shell.nav.items()[0].text()
    check(en != zh and bool(en), f"切换语言后文字没有变化：{zh!r} → {en!r}")
    check(language.set_language(Language.ZH_TW, persist=False), "切换到繁体中文失败")
    app.processEvents()
    tw = window.shell.nav.items()[0].text()
    check(bool(tw) and tw != en, f"切换到繁体中文后文字没有变化：{en!r} → {tw!r}")
    check(language.set_language(Language.ZH_CN, persist=False), "切换回简体中文失败")
    app.processEvents()
    check(window.shell.nav.items()[0].text() == zh, "切回简体中文后文字没有恢复")
    window.logout()
    check(window.current_screen is Screen.LOGIN, "退出登录后没有回到登录页")
