"""可注入的时钟。

模拟设备和模拟视频源通过 :class:`Clock` 读取时间和等待，测试中换成
:class:`ManualClock`，就不需要真实 ``sleep``。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """时钟协议：单调时间、墙钟时间和等待。"""

    def monotonic(self) -> float:
        """返回单调时钟读数，单位秒。"""
        ...

    def now(self) -> datetime:
        """返回带时区的当前时间。"""
        ...

    def sleep(self, seconds: float) -> None:
        """等待指定的真实秒数。

        Args:
            seconds: 等待时长，非正数时立即返回。
        """
        ...


class SystemClock:
    """使用系统时钟的实现。"""

    def monotonic(self) -> float:
        """返回 ``time.monotonic()``。

        Returns:
            单调时钟读数，单位秒。
        """
        return time.monotonic()

    def now(self) -> datetime:
        """返回当前 UTC 时间。

        Returns:
            带时区的当前时间。
        """
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        """真实等待。

        Args:
            seconds: 等待时长，非正数时立即返回。
        """
        if seconds > 0:
            time.sleep(seconds)


class ManualClock:
    """手动推进的时钟，``sleep`` 只推进读数，不真实等待。

    Attributes:
        start: 墙钟时间的起点。
    """

    def __init__(self, start: datetime | None = None) -> None:
        """创建时钟。

        Args:
            start: 墙钟起点，默认 2026-11-04 09:00 UTC。
        """
        self.start = start or datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
        self._elapsed = 0.0

    def monotonic(self) -> float:
        """返回已推进的秒数。

        Returns:
            单调时钟读数，单位秒。
        """
        return self._elapsed

    def now(self) -> datetime:
        """返回起点加上已推进的时间。

        Returns:
            带时区的当前时间。
        """
        return self.start + timedelta(seconds=self._elapsed)

    def sleep(self, seconds: float) -> None:
        """等同于 :meth:`advance`。

        Args:
            seconds: 推进的秒数，非正数时忽略。
        """
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        """推进时钟。

        Args:
            seconds: 推进的秒数，非正数时忽略。
        """
        if seconds > 0:
            self._elapsed += seconds
