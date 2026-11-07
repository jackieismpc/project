"""可注入的时钟。

视频管线用时钟读取时间戳、限速、计算帧率和判断断流。阻塞等待（条件变量、事件）始终使用
真实时间，所以注入的时钟只影响“现在是几点”，不影响线程调度。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """时钟协议：单调时间和带时区的墙钟时间。"""

    def monotonic(self) -> float:
        """返回单调时钟读数，单位秒。"""
        ...

    def now(self) -> datetime:
        """返回带时区的当前时间。"""
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
