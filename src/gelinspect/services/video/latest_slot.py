"""latest-wins 单槽工作线程：慢消费者只会丢帧，不会拖慢生产者。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar, cast

T = TypeVar("T")

logger = logging.getLogger("gelinspect.services.video")


class LatestSlotWorker(Generic[T]):  # noqa: UP046
    """只保留最新一项的工作线程。

    ``submit`` 永不阻塞：槽里已有未处理的项时直接覆盖并计数。工作线程按顺序取走最新项交给
    ``handler``；``handler`` 抛出的异常交给 ``on_error``，不会终止线程。可选的 ``tick`` 按固定周期
    在同一线程里调用。
    """

    def __init__(
        self,
        name: str,
        handler: Callable[[T], None],
        on_error: Callable[[Exception], None],
        *,
        tick: Callable[[], None] | None = None,
        tick_interval_s: float = 0.25,
    ) -> None:
        """创建工作线程对象，不启动。

        Args:
            name: 线程名。
            handler: 处理单项的函数。
            on_error: 处理 ``handler`` 或 ``tick`` 抛出的异常。
            tick: 周期性任务。
            tick_interval_s: ``tick`` 的周期。
        """
        self._name = name
        self._handler = handler
        self._on_error = on_error
        self._tick = tick
        self._tick_interval = tick_interval_s
        self._cond = threading.Condition()
        self._item: T | None = None
        self._has_item = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._replaced = 0
        self._handled = 0

    @property
    def replaced(self) -> int:
        """被新项覆盖而丢弃的项数。"""
        with self._cond:
            return self._replaced

    @property
    def handled(self) -> int:
        """已交给 handler 处理的项数。"""
        with self._cond:
            return self._handled

    def start(self) -> None:
        """启动线程，已启动时无操作。"""
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._item = None
            self._has_item = False
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float) -> bool:
        """停止线程，丢弃槽中未处理的项。

        Args:
            timeout_s: 等待线程退出的最长时间。

        Returns:
            线程是否已退出。
        """
        with self._cond:
            thread = self._thread
            self._stopping = True
            self._item = None
            self._has_item = False
            self._cond.notify_all()
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout_s)
        alive = thread.is_alive()
        if not alive:
            with self._cond:
                if self._thread is thread:
                    self._thread = None
        return not alive

    def submit(self, item: T) -> bool:
        """放入最新项，非阻塞。

        Args:
            item: 待处理的项。

        Returns:
            True 表示覆盖了一个尚未处理的旧项。
        """
        with self._cond:
            replaced = self._has_item
            if replaced:
                self._replaced += 1
            self._item = item
            self._has_item = True
            self._cond.notify()
            return replaced

    def _run(self) -> None:
        next_tick = time.monotonic() + self._tick_interval
        while True:
            item: T | None = None
            have = False
            with self._cond:
                while not self._has_item and not self._stopping:
                    if self._tick is None:
                        self._cond.wait()
                    else:
                        remaining = next_tick - time.monotonic()
                        if remaining <= 0:
                            break
                        self._cond.wait(remaining)
                if self._stopping:
                    return
                if self._has_item:
                    item, self._item, self._has_item = self._item, None, False
                    have = True
            if have:
                try:
                    self._handler(cast("T", item))
                except Exception as exc:
                    self._safe_error(exc)
                with self._cond:
                    self._handled += 1
            if self._tick is not None and time.monotonic() >= next_tick:
                next_tick = time.monotonic() + self._tick_interval
                try:
                    self._tick()
                except Exception as exc:
                    self._safe_error(exc)

    def _safe_error(self, exc: Exception) -> None:
        try:
            self._on_error(exc)
        except Exception:
            logger.exception("错误处理函数本身抛出异常（已忽略）")
