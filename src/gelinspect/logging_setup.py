"""日志：滚动文件日志与未捕获异常的兜底。

日志写到 ``<data_dir>/logs/gelinspect.log``，单个文件上限 1 MB，保留 5 份。
界面线程、工作线程里未捕获的异常，以及 Qt 自己的警告都会进入日志，
不会只留在控制台。
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "gelinspect.log"
"""日志文件名。"""

MAX_LOG_BYTES = 1_000_000
"""单个日志文件上限。"""

BACKUP_COUNT = 5
"""保留的历史日志份数。"""

_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"
_HANDLER_TAG = "gelinspect-file-handler"

CrashReporter = Callable[[BaseException, Path], None]
"""未捕获异常的界面提示，参数为异常和日志文件路径。"""

_log = logging.getLogger("gelinspect")
_crash_reporter: CrashReporter | None = None
_log_path: Path | None = None


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """配置根日志：滚动文件加控制台警告。可重复调用，会替换旧的文件处理器。

    Args:
        log_dir: 日志目录，不存在时创建。
        level: 文件日志级别。

    Returns:
        日志文件路径。

    Raises:
        OSError: 目录无法创建或文件无法打开。
    """
    global _log_path
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _HANDLER_TAG:
            root.removeHandler(handler)
            handler.close()
    file_handler = RotatingFileHandler(
        path, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.set_name(_HANDLER_TAG)
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler.setLevel(level)
    root.addHandler(file_handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _log_path = path
    return path


def shutdown_logging() -> None:
    """移除并关闭本模块安装的文件处理器，释放日志文件。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _HANDLER_TAG:
            root.removeHandler(handler)
            handler.close()


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
