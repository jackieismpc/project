"""日志：滚动归档文件日志与未捕获异常的兜底。

日志写到 ``<data_dir>/logs/gelinspect.log``，单个文件上限 1 MB；滚动文件按时间戳归档且不自动删除。
界面线程、工作线程里未捕获的异常，以及 Qt 自己的警告都会进入日志，
不会只留在控制台。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import types
import uuid
from collections.abc import Callable
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

LOG_FILE_NAME = "gelinspect.log"
"""日志文件名。"""

MAX_LOG_BYTES = 1_000_000
"""单个日志文件上限。"""

BACKUP_COUNT = 5
"""兼容标准handler属性；归档不按份数自动删除。"""

_FORMAT = (
    "%(asctime)s %(levelname)-8s run=%(run_id)s thread=%(threadName)s logger=%(name)s: %(message)s"
)
_HANDLER_TAG = "gelinspect-file-handler"
_DAILY_HANDLER_TAG = "gelinspect-diagnostic-daily-handler"

CrashReporter = Callable[[BaseException, Path], None]
"""未捕获异常的界面提示，参数为异常和日志文件路径。"""

_log = logging.getLogger("gelinspect")
_crash_reporter: CrashReporter | None = None
_log_path: Path | None = None
_previous_qt_handler: object = None
_qt_handler_installed = False
_run_id = ""


class _RunFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id
        return True


class _LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        timestamp = datetime.fromtimestamp(record.created).astimezone()
        return timestamp.isoformat(timespec="milliseconds")


def current_run_id() -> str:
    """当前进程日志会话标识。"""
    return _run_id


class _ArchiveRotatingFileHandler(RotatingFileHandler):
    """按容量切分日志并永久保留归档文件。"""

    def doRollover(self) -> None:  # noqa: N802
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        source = Path(self.baseFilename)
        if source.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            destination = source.with_name(f"{source.name}.{stamp}")
            suffix = 1
            while destination.exists():
                destination = source.with_name(f"{source.name}.{stamp}-{suffix}")
                suffix += 1
            os.replace(source, destination)
        if not self.delay:
            self.stream = self._open()


class _DailyDiagnosticHandler(logging.Handler):
    """按记录本地日期追加独立诊断日志，不自动删除。"""

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d")
            with (self.directory / f"{day}.log").open("a", encoding="utf-8") as stream:
                stream.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def _qt_message(kind: QtMsgType, _context: object, message: str) -> None:
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    logging.getLogger("gelinspect.qt").log(levels.get(kind, logging.WARNING), "%s", message)


def configure_logging(
    log_dir: Path, *, level: int = logging.INFO, diagnostic_dir: Path | None = None
) -> Path:
    """配置根日志：滚动文件加控制台警告。可重复调用，会替换旧的文件处理器。

    Args:
        log_dir: 日志目录，不存在时创建。
        level: 文件日志级别。
        diagnostic_dir: 按日诊断日志目录；默认位于数据目录的 diagnostics/logs。

    Returns:
        日志文件路径。

    Raises:
        OSError: 目录无法创建或文件无法打开。
    """
    global _log_path, _previous_qt_handler, _qt_handler_installed, _run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    _run_id = uuid.uuid4().hex
    path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) in (_HANDLER_TAG, _DAILY_HANDLER_TAG):
            root.removeHandler(handler)
            handler.close()
    file_handler = _ArchiveRotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.set_name(_HANDLER_TAG)
    file_handler.setFormatter(_LocalTimeFormatter(_FORMAT))
    file_handler.addFilter(_RunFilter())
    file_handler.setLevel(level)
    root.addHandler(file_handler)
    daily = _DailyDiagnosticHandler(diagnostic_dir or log_dir.parent / "diagnostics" / "logs")
    daily.set_name(_DAILY_HANDLER_TAG)
    daily.setFormatter(_LocalTimeFormatter(_FORMAT))
    daily.addFilter(_RunFilter())
    daily.setLevel(level)
    root.addHandler(daily)
    if not _qt_handler_installed:
        _previous_qt_handler = qInstallMessageHandler(_qt_message)
        _qt_handler_installed = True
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _log_path = path
    return path


def shutdown_logging() -> None:
    """移除并关闭本模块安装的文件处理器，释放日志文件。"""
    global _previous_qt_handler, _qt_handler_installed
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) in (_HANDLER_TAG, _DAILY_HANDLER_TAG):
            root.removeHandler(handler)
            handler.close()
    if _qt_handler_installed:
        qInstallMessageHandler(_previous_qt_handler)  # type: ignore[arg-type]
        _previous_qt_handler = None
        _qt_handler_installed = False


def install_exception_hooks(reporter: CrashReporter | None = None) -> None:
    """安装未捕获异常的兜底：写日志，并可选地提示用户。

    覆盖主线程（``sys.excepthook``）和工作线程（``threading.excepthook``）。

    Args:
        reporter: 提示用户的回调，在抛出异常的线程上调用；回调自己要保证线程安全。
    """
    global _crash_reporter
    _crash_reporter = reporter
    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception


def _handle_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: types.TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _log.critical("未捕获的异常", exc_info=(exc_type, exc, tb))
    reporter = _crash_reporter
    if reporter is not None and _log_path is not None:
        try:
            reporter(exc, _log_path)
        except Exception:
            _log.exception("显示错误提示失败")


def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None or args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    _log.critical(
        "线程 %s 中未捕获的异常",
        name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
