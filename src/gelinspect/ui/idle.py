"""空闲检测：一段时间没有鼠标、键盘或触摸输入就发出 ``idle`` 信号，用于自动锁屏。"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

_INPUT_EVENTS = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.KeyPress,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
    }
)

CHECK_INTERVAL_MS = 1000
"""检查空闲的周期。"""


class IdleWatcher(QObject):
    """监视应用级输入事件，空闲超时后发出 :attr:`idle` 一次。

    Signals:
        idle: 空闲时间超过设定值。发出后需要 :meth:`start` 或 :meth:`touch` 才会再次计时。
    """

    idle = Signal()

    def __init__(
        self,
        timeout_s: float,
        *,
        clock: Callable[[], float] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """创建监视器，不立即开始。

        Args:
            timeout_s: 空闲多少秒后触发；不大于 0 表示永不触发。
            clock: 返回单调时间（秒）的函数，测试注入。
            parent: 父对象。
        """
        super().__init__(parent)
        self._timeout_s = timeout_s
        self._clock = clock or time.monotonic
        self._last = self._clock()
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check)

    @property
    def active(self) -> bool:
        """是否正在监视。"""
        return self._active

    def start(self) -> None:
        """开始监视，从现在起重新计时。"""
        self._last = self._clock()
        if self._active:
            return
        self._active = True
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        if self._timeout_s > 0:
            self._timer.start()

    def stop(self) -> None:
        """停止监视。"""
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def touch(self) -> None:
        """记录一次活动（手动重置计时）。"""
        self._last = self._clock()

    def check(self) -> bool:
        """检查是否已空闲超时，超时则停止监视并发出 :attr:`idle`。

        Returns:
            是否触发了 ``idle``。
        """
        if not self._active or self._timeout_s <= 0:
            return False
        if self._clock() - self._last < self._timeout_s:
            return False
        self.stop()
        self.idle.emit()
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """有输入事件就重置计时，不拦截事件。

        Args:
            watched: 事件目标。
            event: 事件。

        Returns:
            始终为 False。
        """
        if event.type() in _INPUT_EVENTS:
            self._last = self._clock()
        return False
