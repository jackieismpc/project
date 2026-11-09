"""命令行入口与启动流程：一键启动、首次初始化、错误对话框、日志和兜底。"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from PySide6.QtWidgets import QApplication
from sqlalchemy.exc import OperationalError

from gelinspect import __version__, startup
from gelinspect.__main__ import build_parser, main
from gelinspect.domain.enums import AuditAction
from gelinspect.interfaces.storage import AuditQuery, DatabaseVersionError, PageRequest
from gelinspect.logging_setup import (
    BACKUP_COUNT,
    LOG_FILE_NAME,
    MAX_LOG_BYTES,
    configure_logging,
    install_exception_hooks,
    shutdown_logging,
)
from gelinspect.services.config import DEFAULT_CONFIG, ConfigError
from gelinspect.startup import (
    EXIT_CONFIG,
    EXIT_DATABASE,
    EXIT_ERROR,
    EXIT_OK,
    StartupFailure,
    describe_startup_error,
    run_gui,
)
from tests.keyed_db import open_keyed

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def restore_global_state() -> Iterator[None]:
    """启动流程会安装异常钩子和日志处理器，测试后还原。"""
    hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    sys.excepthook, threading.excepthook = hook, thread_hook
    shutdown_logging()


class Presenter:
    def __init__(self) -> None:
        self.failures: list[tuple[StartupFailure, Path | None]] = []

    def __call__(self, failure: StartupFailure, log_path: Path | None) -> None:
        self.failures.append((failure, log_path))


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    data: dict[str, Any] = {**DEFAULT_CONFIG, "data_dir": str(tmp_path / "data")}
    data.update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 命令行


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"gelinspect {__version__}"


def test_version_does_not_import_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from gelinspect.__main__ import main\n"
            "try:\n    main(['--version'])\nexcept SystemExit: pass\n"
            "print('PySide6' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_no_arguments_opens_the_window_with_the_default_simulation_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(startup, "run_gui", fake_run)
    assert main([]) == 0
    assert calls == [{"config_path": None, "data_dir": None, "smoke_test": False, "demo": False}]


def test_config_option_and_simulate_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr(startup, "run_gui", fake_run)
    assert main(["--config", str(tmp_path / "x.yaml")]) == 7
    assert calls[-1]["config_path"] == tmp_path / "x.yaml"
    assert main(["--simulate"]) == 7
    assert calls[-1]["config_path"] is None
    with pytest.raises(SystemExit):
        main(["--simulate", "--config", "x.yaml"])


def test_smoke_flags_are_hidden_from_help() -> None:
    help_text = build_parser().format_help()
    assert "--smoke-test" not in help_text and "--diagnose" not in help_text
    assert "--config" in help_text and "--simulate" in help_text


# ---------------------------------------------------------------- 首次启动与再次启动


def test_first_start_creates_data_dir_database_logs_and_demo_accounts(
    qapp: QApplication, tmp_path: Path
) -> None:
    data = tmp_path / "fresh" / "data"
    assert not data.exists()
    code = run_gui(data_dir=data, smoke_test=True, app=qapp)
    assert code == EXIT_OK
    assert (data / "gelinspect.db").is_file()
    assert (data / "logs" / LOG_FILE_NAME).is_file()
    db = open_keyed(data)
    try:
        assert [u.username for u in db.users.list_all()] == ["admin", "analyst", "experimenter"]
        assert len(db.templates.list_all()) == 5
        # 冒烟测试登录并退出了一次：登录、退出各一条审计
        events = db.audit.query(AuditQuery(actor="admin"), PageRequest(limit=50)).items
        actions = [r.event.action for r in events]
        assert AuditAction.LOGIN in actions and AuditAction.LOGOUT in actions
        assert db.audit.verify_chain().ok
    finally:
        db.close()
    log = (data / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "首次启动" in log and "冒烟测试通过" in log


def test_second_start_does_not_initialize_again(qapp: QApplication, tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert run_gui(data_dir=data, smoke_test=True, app=qapp) == EXIT_OK
    db = open_keyed(data)
    users_before = sorted(u.user_id for u in db.users.list_all())
    template_events = len(
        db.audit.query(AuditQuery(action=AuditAction.TEMPLATE_CHANGED), PageRequest()).items
    )
    user_events = len(
        db.audit.query(AuditQuery(action=AuditAction.USER_CHANGED), PageRequest()).items
    )
    db.close()
    assert run_gui(data_dir=data, smoke_test=True, app=qapp) == EXIT_OK
    db = open_keyed(data)
    try:
        assert sorted(u.user_id for u in db.users.list_all()) == users_before
        assert (
            len(
                db.audit.query(AuditQuery(action=AuditAction.TEMPLATE_CHANGED), PageRequest()).items
            )
            == template_events
        )
        assert (
            len(db.audit.query(AuditQuery(action=AuditAction.USER_CHANGED), PageRequest()).items)
            == user_events
        )
        assert db.audit.verify_chain().ok
    finally:
        db.close()
    log = (data / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert log.count("首次启动") == 1


def test_changed_demo_password_survives_a_restart(qapp: QApplication, tmp_path: Path) -> None:
    """再次启动不会把演示账号的密码重置回去。"""
    data = tmp_path / "data"
    assert run_gui(data_dir=data, smoke_test=True, app=qapp) == EXIT_OK
    from gelinspect.bootstrap import Argon2Hasher

    db = open_keyed(data)
    user = db.users.get_by_username("analyst")
    assert user is not None
    db.users.set_password_hash(
        user.user_id, Argon2Hasher().hash("Changed-1234"), must_change_password=False
    )
    db.close()
    assert run_gui(data_dir=data, smoke_test=True, app=qapp) == EXIT_OK
    db = open_keyed(data)
    try:
        credentials = db.users.get_credentials("analyst")
        assert credentials is not None
        assert Argon2Hasher().verify(credentials.password_hash, "Changed-1234")
    finally:
        db.close()


def test_real_config_has_no_default_accounts(qapp: QApplication, tmp_path: Path) -> None:
    path = write_config(tmp_path)
    data = yaml.safe_load(path.read_text())
    data.pop("bootstrap")
    path.write_text(yaml.safe_dump(data))
    assert run_gui(config_path=path, smoke_test=True, app=qapp) == EXIT_OK
    db = open_keyed(tmp_path / "data")
    try:
        assert db.users.list_all() == []  # 真实配置：不创建任何账号，更没有默认密码
        assert len(db.templates.list_all()) == 5
    finally:
        db.close()


def test_non_smoke_run_enters_the_event_loop_and_cleans_up(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[bool] = []

    def fake_exec(_self: QApplication) -> int:
        entered.append(any(w.isVisible() for w in QApplication.topLevelWidgets()))
        for widget in QApplication.topLevelWidgets():
            widget.close()
        return 0

    monkeypatch.setattr(QApplication, "exec", fake_exec)
    monkeypatch.setattr(startup, "_install_sigint", lambda _app: None)
    assert run_gui(config_path=write_config(tmp_path), app=qapp) == 0
    assert entered == [True]


# ---------------------------------------------------------------- 错误对话框与日志


def test_bad_config_file_shows_a_clear_dialog_and_logs(qapp: QApplication, tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("analysis: [not, a, mapping\n", encoding="utf-8")
    presenter = Presenter()
    code = run_gui(config_path=path, data_dir=tmp_path / "data", present_error=presenter, app=qapp)
    assert code == EXIT_CONFIG
    ((failure, log_path),) = presenter.failures
    assert failure.title == "配置有误，无法启动"
    assert "bad.yaml" in failure.message and "--config" in failure.message
    assert log_path is not None and log_path.is_file()
    assert "配置有误" in log_path.read_text(encoding="utf-8")


def test_unknown_field_in_config_is_reported(qapp: QApplication, tmp_path: Path) -> None:
    presenter = Presenter()
    path = write_config(tmp_path, nonsense=1)
    assert (
        run_gui(
            config_path=path, data_dir=tmp_path / "logs-only", present_error=presenter, app=qapp
        )
        == EXIT_CONFIG
    )
    assert "nonsense" in presenter.failures[0][0].message


def test_missing_simulation_package_is_explained(qapp: QApplication, tmp_path: Path) -> None:
    presenter = Presenter()
    path = write_config(tmp_path, bootstrap={"hook": "simulation_gone.demo:DemoAccounts"})
    code = run_gui(config_path=path, present_error=presenter, app=qapp)
    assert code == EXIT_CONFIG
    message = presenter.failures[0][0].message
    assert "bootstrap.hook" in message and "simulation_gone" in message


def test_missing_simulation_backend_is_explained(qapp: QApplication, tmp_path: Path) -> None:
    presenter = Presenter()
    path = write_config(tmp_path, device={"backend": "simulation_gone.sim_device:SimDevice"})
    assert run_gui(config_path=path, present_error=presenter, app=qapp) == EXIT_CONFIG
    assert "device.backend" in presenter.failures[0][0].message
    assert not (tmp_path / "data" / "gelinspect.db").exists()  # 配置有误时不留下数据库


def test_locked_database_shows_the_in_use_dialog(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """另一个进程占着数据库：真实地让 SQLite 报 database is locked。"""
    from gelinspect.infrastructure.db import database as database_module

    monkeypatch.setattr(database_module, "BUSY_TIMEOUT_MS", 50)
    data = tmp_path / "data"
    data.mkdir()
    holder = sqlite3.connect(data / "gelinspect.db", isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        presenter = Presenter()
        code = run_gui(config_path=write_config(tmp_path), present_error=presenter, app=qapp)
    finally:
        holder.close()
    assert code == EXIT_DATABASE
    ((failure, _),) = presenter.failures
    assert failure.title == "数据库被占用"
    assert "gelinspect.db" in failure.message and "另一个" in failure.message


def test_error_mapping_covers_the_known_failures() -> None:
    locked = OperationalError("x", {}, sqlite3.OperationalError("database is locked"))
    assert describe_startup_error(locked).exit_code == EXIT_DATABASE
    assert describe_startup_error(DatabaseVersionError("新版本")).title == "数据库版本不兼容"
    assert describe_startup_error(PermissionError("denied")).exit_code == EXIT_DATABASE
    assert describe_startup_error(ConfigError("坏了")).exit_code == EXIT_CONFIG
    unknown = describe_startup_error(RuntimeError("boom"))
    assert unknown.exit_code == EXIT_ERROR and "boom" in unknown.details


def test_error_messages_follow_the_ui_language(qapp: QApplication) -> None:
    from gelinspect.ui.i18n import Language, LanguageManager, MemoryLanguageSettings

    manager = LanguageManager(qapp, settings=MemoryLanguageSettings())
    try:
        manager.set_language(Language.EN, persist=False)
        failure = describe_startup_error(ConfigError("bad"))
        assert failure.title == "Invalid configuration"
        assert "Fix the configuration file" in failure.message
        manager.set_language(Language.ZH_TW, persist=False)
        failure = describe_startup_error(ConfigError("bad"))
        assert failure.title == "設定有誤，無法啟動"
        assert "設定檔" in failure.message
    finally:
        manager.set_language(Language.ZH_CN, persist=False)


def test_error_dialog_is_built_with_details_and_log_path(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLabel

    from gelinspect.ui.widgets.dialog import ErrorDialog

    dialog = ErrorDialog("标题", "说明", details="Traceback…", log_path="/x/gelinspect.log")
    assert dialog.details_view is not None and "Traceback" in dialog.details_view.toPlainText()
    assert any("/x/gelinspect.log" in label.text() for label in dialog.findChildren(QLabel))
    assert dialog.close_button.text() == "关闭"


def test_log_is_rolling_and_utf8(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")
    handlers = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "name", "") == "gelinspect-file-handler"
    ]
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler.maxBytes == MAX_LOG_BYTES and handler.backupCount == BACKUP_COUNT  # type: ignore[attr-defined]
    logging.getLogger("gelinspect.test").warning("中文日志 ✓")
    handler.flush()
    assert "中文日志 ✓" in path.read_text(encoding="utf-8")
    configure_logging(tmp_path / "logs")  # 重复配置不会叠加处理器
    assert (
        len(
            [
                h
                for h in logging.getLogger().handlers
                if getattr(h, "name", "") == "gelinspect-file-handler"
            ]
        )
        == 1
    )


def test_log_rolls_over_when_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gelinspect import logging_setup

    monkeypatch.setattr(logging_setup, "MAX_LOG_BYTES", 2000)
    path = configure_logging(tmp_path / "logs")
    for i in range(100):
        logging.getLogger("gelinspect.roll").warning("填满日志 %d %s", i, "x" * 60)
    assert (path.parent / f"{LOG_FILE_NAME}.1").is_file()


def test_uncaught_exceptions_are_logged_and_reported(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")
    reported: list[tuple[BaseException, Path]] = []
    install_exception_hooks(lambda e, p: reported.append((e, p)))
    try:
        raise ValueError("主线程里没有捕获")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    def work() -> None:
        raise RuntimeError("工作线程里没有捕获")

    thread = threading.Thread(target=work, name="worker-x")
    thread.start()
    thread.join()
    text = path.read_text(encoding="utf-8")
    assert "主线程里没有捕获" in text and "Traceback" in text
    assert "线程 worker-x 中未捕获的异常" in text and "工作线程里没有捕获" in text
    assert [type(e) for e, _ in reported] == [ValueError]  # 工作线程的只写日志，避免弹窗风暴
    assert reported[0][1] == path


def test_a_failing_reporter_cannot_break_the_hook(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")

    def broken(_e: BaseException, _p: Path) -> None:
        raise RuntimeError("提示框也出错了")

    install_exception_hooks(broken)
    try:
        raise ValueError("原始错误")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    assert "显示错误提示失败" in path.read_text(encoding="utf-8")


def test_the_console_script_starts_cleanly_in_a_subprocess(tmp_path: Path) -> None:
    """真正的进程：走 python -m gelinspect --smoke-test，返回码 0。"""
    env = {"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "gelinspect", "--smoke-test", "--data-dir", str(tmp_path / "d")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "d" / "gelinspect.db").is_file()
