"""把耗时的读盘和解码放到后台线程，结果回到主线程。

界面不能自己创建线程，这里用 Qt 的线程池。任务函数在线程池里执行（只能调用服务，
不能碰控件），完成后通过排队信号在主线程调用回调。每次提交带一个令牌，调用方用
:meth:`BackgroundRunner.cancel` 或再次提交让旧结果作废，过期的结果直接丢弃。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

_log = logging.getLogger(__name__)


class _Job(QRunnable):
    def __init__(self, token: int, fn: Callable[[], Any], runner: BackgroundRunner) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token = token
        self._fn = fn
        self._runner = runner

    def run(self) -> None:
        try:
            outcome: tuple[int, object, Exception | None] = (self._token, self._fn(), None)
        except Exception as exc:
            outcome = (self._token, None, exc)
        try:
            self._runner._finished.emit(outcome)  # noqa: SLF001
        except RuntimeError:
            _log.debug("后台任务完成时，接收方已经销毁，结果丢弃")


class BackgroundRunner(QObject):
    """后台任务执行器。必须在主线程创建，回调在主线程执行。"""

    _finished = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        """创建执行器。

        Args:
            parent: 父对象，随它销毁。
        """
        super().__init__(parent)
        self._counter = itertools.count(1)
        self._callbacks: dict[
            int, tuple[Callable[[Any], None], Callable[[Exception], None] | None]
        ] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._finished.connect(self._deliver)

    def submit(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> int:
        """提交任务。

        Args:
            fn: 在线程池里执行的函数。
            on_done: 成功后在主线程调用，参数为返回值。
            on_error: 失败后在主线程调用，参数为异常；为 None 时只记日志。

        Returns:
            令牌，可传给 :meth:`cancel`。
        """
        token = next(self._counter)
        self._callbacks[token] = (on_done, on_error)
        self._pool.start(_Job(token, fn, self))
        return token

    def cancel(self, token: int | None = None) -> None:
        """让任务的结果作废（任务本身会跑完，但回调不再执行）。

        Args:
            token: 要取消的令牌；为 None 取消全部。
        """
        if token is None:
            self._callbacks.clear()
        else:
            self._callbacks.pop(token, None)

    def pending(self) -> int:
        """还没有回调的任务数。

        Returns:
            任务数。
        """
        return len(self._callbacks)

    def wait(self, timeout_ms: int = 5000) -> bool:
        """等线程池里的任务跑完（不处理回调），关闭窗口和测试清理时使用。

        Args:
            timeout_ms: 时限。

        Returns:
            是否已全部结束。
        """
        return self._pool.waitForDone(timeout_ms)

    @Slot(object)
    def _deliver(self, outcome: object) -> None:
        token, value, error = cast("tuple[int, object, Exception | None]", outcome)
        callbacks = self._callbacks.pop(token, None)
        if callbacks is None:
            return
        on_done, on_error = callbacks
        if error is not None:
            _log.warning("后台任务失败：%r", error)
            if on_error is not None:
                on_error(error)
            return
        on_done(value)
