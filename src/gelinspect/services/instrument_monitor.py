"""仪器状态监视：空闲时定期探测设备，供顶部状态栏和工作台显示。

探测由 :meth:`ExperimentEngine.probe_device` 完成，它保证不会与运行中的试验同时访问设备。
试验运行期间探测返回 ``None``，状态改由引擎的温度事件提供。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from gelinspect.interfaces.device import DeviceStatus

_log = logging.getLogger(__name__)

THREAD_NAME = "instrument-monitor"
"""监视线程名。"""


@dataclass(frozen=True, slots=True)
class InstrumentStatus:
    """仪器状态。

    Attributes:
        connected: 通信是否正常。
        temperature_c: 温度，℃，读不到时为 None。
        at: 读取时间。
    """

    connected: bool
    temperature_c: float | None
    at: datetime

    @classmethod
    def from_device(cls, status: DeviceStatus) -> InstrumentStatus:
        """由设备状态生成。

        Args:
            status: 设备状态快照。

        Returns:
            仪器状态。
        """
        return cls(status.connected, status.temperature_c, status.read_at)


class DeviceProbe(Protocol):
    """能探测设备的对象（试验引擎）。"""

    def probe_device(self) -> DeviceStatus | None:
        """探测设备；试验运行中或无法探测时返回 None。"""
        ...


class InstrumentMonitor:
    """后台线程，每隔一段时间探测一次设备并回调。回调在监视线程上执行。"""

    def __init__(
        self,
        probe: DeviceProbe,
        on_status: Callable[[InstrumentStatus], None] | None = None,
        *,
        interval_s: float = 2.0,
    ) -> None:
        """创建监视器，不启动线程。

        Args:
            probe: 探测对象。
            on_status: 状态回调，在监视线程上调用；抛出的异常只记录日志。之后可用
                :meth:`add_listener` 追加。
            interval_s: 探测间隔，秒。
        """
        self._probe = probe
        self._listeners: list[Callable[[InstrumentStatus], None]] = []
        if on_status is not None:
            self._listeners.append(on_status)
        self._last: InstrumentStatus | None = None
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """追加状态监听者，在监视线程上调用。

        Args:
            listener: 回调。
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[InstrumentStatus], None]) -> None:
        """移除状态监听者，不存在时无操作。

        Args:
            listener: 回调。
        """
        if listener in self._listeners:
            self._listeners.remove(listener)

    @property
    def last(self) -> InstrumentStatus | None:
        """最近一次探测到的状态，从未探测到时为 None。"""
        return self._last

    def start(self) -> None:
        """启动监视线程，已启动时无操作。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> bool:
        """停止监视线程并等待退出，可重复调用。

        Args:
            timeout_s: 等待时限，秒。

        Returns:
            线程已退出（或从未启动）时为 True。
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_s)
        return not thread.is_alive()

    def poll_once(self) -> InstrumentStatus | None:
        """探测一次并回调（测试和启动时立即刷新用）。

        Returns:
            探测到的状态；试验运行中或探测失败时为 None。
        """
        try:
            status = self._probe.probe_device()
        except Exception:
            _log.exception("探测设备失败")
            return None
        if status is None:
            return None
        result = InstrumentStatus.from_device(status)
        self._last = result
        for listener in list(self._listeners):
            try:
                listener(result)
            except Exception:
                _log.exception("仪器状态回调失败")
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval_s)
