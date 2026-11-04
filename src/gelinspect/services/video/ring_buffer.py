"""按时间戳索引、按字节数限额的环形缓冲。

采集线程 ``put``，其他线程按时间查询。所有方法线程安全。帧写入后只读，缓冲保存的是对同一数组的
引用，不复制像素。

**保护（pin）**：抓拍需要的帧（翻转前几帧、翻转后稳定的几帧）不能被淘汰。``pin`` 登记一个时间区间，
区间内的帧在超过 ``max_bytes`` 时也不淘汰；但总字节数超过 ``hard_limit_bytes`` 时，仍会淘汰最旧的
受保护帧并发出 ``PIN_OVERFLOW`` 告警，内存不会无限增长。
"""

from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from gelinspect.interfaces.video import VideoFrame
from gelinspect.services.video.clock import Clock, SystemClock
from gelinspect.services.video.events import (
    EventSink,
    VideoEventCode,
    VideoEventLevel,
    make_event,
)

Timestamp = datetime | float
"""带时区的时间，或 Unix 时间戳（秒）。"""


def to_timestamp(value: Timestamp) -> float:
    """把时间转换为 Unix 时间戳（秒）。

    Args:
        value: 带时区的 ``datetime`` 或浮点秒数。

    Returns:
        Unix 时间戳。

    Raises:
        ValueError: ``datetime`` 不带时区。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("时间必须带时区")
        return value.timestamp()
    return float(value)


@dataclass(frozen=True, slots=True)
class BufferStats:
    """环形缓冲的统计。

    Attributes:
        frames: 当前帧数。
        bytes_used: 当前占用字节数。
        max_bytes: 常规上限。
        hard_limit_bytes: 含受保护帧的硬上限。
        peak_bytes: 历史峰值。
        total_put: 累计写入帧数。
        evicted: 累计淘汰帧数。
        forced_evictions: 其中因超过硬上限而淘汰的受保护帧数。
        pins: 当前生效的保护数。
        clock_regressions: 帧时间戳倒退（墙钟被回拨）的次数，倒退的帧按上一帧的时间索引。
    """

    frames: int
    bytes_used: int
    max_bytes: int
    hard_limit_bytes: int
    peak_bytes: int
    total_put: int
    evicted: int
    forced_evictions: int
    pins: int
    clock_regressions: int


@dataclass(slots=True)
class _Entry:
    ts: float
    frame: VideoFrame
    nbytes: int


class FrameRingBuffer:
    """线程安全的环形缓冲。"""

    def __init__(
        self,
        max_bytes: int,
        *,
        hard_limit_bytes: int | None = None,
        on_event: EventSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建缓冲。

        Args:
            max_bytes: 常规字节数上限。
            hard_limit_bytes: 含受保护帧的硬上限，默认 ``max_bytes`` 的 1.5 倍，不得小于
                ``max_bytes``。
            on_event: 事件接收函数，在锁外调用。
            clock: 时钟，用于事件时间戳。

        Raises:
            ValueError: 上限不合法。
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正")
        hard = hard_limit_bytes if hard_limit_bytes is not None else max_bytes + max_bytes // 2
        if hard < max_bytes:
            raise ValueError("hard_limit_bytes 不得小于 max_bytes")
        self._max_bytes = max_bytes
        self._hard = hard
        self._on_event = on_event
        self._clock: Clock = clock or SystemClock()
        self._cond = threading.Condition()
        self._keys: list[float] = []
        self._entries: list[_Entry] = []
        self._bytes = 0
        self._peak = 0
        self._total_put = 0
        self._evicted = 0
        self._forced = 0
        self._regressions = 0
        self._pins: dict[int, tuple[float, float | None]] = {}
        self._next_pin = 1
        self._overflow_active = False
        self._wait_epoch = 0

    # ------------------------------------------------------------------ 写入

    def put(self, frame: VideoFrame) -> None:
        """写入一帧并按需淘汰，同时唤醒等待新帧的线程。

        Args:
            frame: 视频帧，其图像之后不得被改写。
        """
        ts = frame.captured_at.timestamp()
        nbytes = int(frame.image.nbytes)
        overflow_message: str | None = None
        with self._cond:
            if self._keys and ts < self._keys[-1]:
                ts = self._keys[-1]
                self._regressions += 1
            self._keys.append(ts)
            self._entries.append(_Entry(ts, frame, nbytes))
            self._bytes += nbytes
            self._total_put += 1
            overflow_message = self._evict_locked()
            self._peak = max(self._peak, self._bytes)
            self._cond.notify_all()
        if overflow_message is not None and self._on_event is not None:
            self._on_event(
                make_event(
                    self._clock,
                    VideoEventCode.PIN_OVERFLOW,
                    VideoEventLevel.ERROR,
                    overflow_message,
                    value=float(self._bytes),
                    threshold=float(self._hard),
                )
            )

    def _evict_locked(self) -> str | None:
        """淘汰帧直到不超限，返回需要告警时的说明。

        先淘汰最旧的未受保护帧（跳过受保护帧）；仍超过硬上限时，才强制淘汰最旧的受保护帧。
        最新一帧永远保留。
        """
        if self._bytes <= self._max_bytes:
            self._overflow_active = False
            return None
        entries = self._entries
        last = len(entries) - 1
        total = self._bytes
        removable: list[int] = []
        for index, entry in enumerate(entries):
            if total <= self._max_bytes or index >= last:
                break
            if not self._is_pinned_locked(entry.ts):
                removable.append(index)
                total -= entry.nbytes
        message: str | None = None
        if total > self._hard:
            chosen = set(removable)
            for index, entry in enumerate(entries):
                if total <= self._hard or index >= last:
                    break
                if index in chosen:
                    continue
                removable.append(index)
                total -= entry.nbytes
                self._forced += 1
            if not self._overflow_active:
                self._overflow_active = True
                message = (
                    f"被保护的帧占用超过硬上限 {self._hard} 字节，"
                    "已强制淘汰最旧的受保护帧，抓拍帧可能丢失"
                )
        if removable:
            removable.sort()
            if removable[-1] == len(removable) - 1:
                del entries[: len(removable)]
                del self._keys[: len(removable)]
            else:
                drop = set(removable)
                self._entries = [e for i, e in enumerate(entries) if i not in drop]
                self._keys = [k for i, k in enumerate(self._keys) if i not in drop]
            self._bytes = total
            self._evicted += len(removable)
        if self._overflow_active and self._bytes <= self._max_bytes:
            self._overflow_active = False
        return message

    def _is_pinned_locked(self, ts: float) -> bool:
        return any(start <= ts and (end is None or ts <= end) for start, end in self._pins.values())

    # ------------------------------------------------------------------ 保护

    def pin(self, from_ts: Timestamp, until_ts: Timestamp | None = None) -> int:
        """保护时间区间内的帧不被常规淘汰。

        Args:
            from_ts: 区间起点（含）。
            until_ts: 区间终点（含），None 表示起点之后的所有帧。

        Returns:
            保护标识，用于 :meth:`unpin`。
        """
        start = to_timestamp(from_ts)
        end = None if until_ts is None else to_timestamp(until_ts)
        with self._cond:
            pin_id = self._next_pin
            self._next_pin += 1
            self._pins[pin_id] = (start, end)
            return pin_id

    def pin_last_before(self, before_ts: Timestamp, n: int) -> int | None:
        """保护 ``before_ts`` 之前最近的 n 帧。

        Args:
            before_ts: 时间点（不含）。
            n: 帧数。

        Returns:
            保护标识；该时间点之前没有帧时为 None。
        """
        limit = to_timestamp(before_ts)
        with self._cond:
            end = bisect.bisect_left(self._keys, limit)
            if end == 0:
                return None
            start_ts = self._keys[max(0, end - n)]
            end_ts = self._keys[end - 1]
            pin_id = self._next_pin
            self._next_pin += 1
            self._pins[pin_id] = (start_ts, end_ts)
            return pin_id

    def unpin(self, pin_id: int) -> None:
        """解除保护，重复调用或标识不存在时忽略，并立即按上限回收。

        Args:
            pin_id: :meth:`pin` 返回的标识。
        """
        with self._cond:
            if self._pins.pop(pin_id, None) is None:
                return
            self._evict_locked()

    # ------------------------------------------------------------------ 查询

    def __len__(self) -> int:
        """返回当前帧数。"""
        with self._cond:
            return len(self._entries)

    def latest(self) -> VideoFrame | None:
        """返回最新一帧，缓冲为空时为 None。"""
        with self._cond:
            return self._entries[-1].frame if self._entries else None

    def frames_before(self, ts: Timestamp, n: int) -> list[VideoFrame]:
        """返回时间早于 ``ts`` 的最近 n 帧，按时间升序。

        Args:
            ts: 时间点（不含）。
            n: 最多取多少帧。

        Returns:
            帧列表，可能少于 n 帧。
        """
        limit = to_timestamp(ts)
        with self._cond:
            end = bisect.bisect_left(self._keys, limit)
            return [entry.frame for entry in self._entries[max(0, end - n) : end]]

    def frames_from(self, ts: Timestamp, limit: int | None = None) -> list[VideoFrame]:
        """返回时间不早于 ``ts`` 的帧，按时间升序。

        Args:
            ts: 时间点（含）。
            limit: 最多取多少帧，None 表示全部。

        Returns:
            帧列表。
        """
        with self._cond:
            start = bisect.bisect_left(self._keys, to_timestamp(ts))
            stop = len(self._entries) if limit is None else start + limit
            return [entry.frame for entry in self._entries[start:stop]]

    def wait_for_frames_from(
        self, ts: Timestamp, n: int, timeout_s: float
    ) -> tuple[list[VideoFrame], bool]:
        """等待缓冲中出现 n 帧时间不早于 ``ts`` 的帧。

        Args:
            ts: 时间点（含）。
            n: 需要的帧数。
            timeout_s: 最长等待时间（真实时间）。

        Returns:
            ``(帧列表, 是否被取消)``。凑够 n 帧时返回前 n 帧；超时返回已有的帧（少于 n）；
            被 :meth:`cancel_waits` 取消时第二项为 True。
        """
        threshold = to_timestamp(ts)
        deadline = time.monotonic() + timeout_s
        with self._cond:
            epoch = self._wait_epoch
            while True:
                start = bisect.bisect_left(self._keys, threshold)
                available = len(self._entries) - start
                if available >= n:
                    return [e.frame for e in self._entries[start : start + n]], False
                if self._wait_epoch != epoch:
                    return [e.frame for e in self._entries[start:]], True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [e.frame for e in self._entries[start:]], False
                self._cond.wait(min(remaining, 0.2))

    def cancel_waits(self) -> None:
        """让所有正在 :meth:`wait_for_frames_from` 的线程立即返回（管线停止时调用）。"""
        with self._cond:
            self._wait_epoch += 1
            self._cond.notify_all()

    def clear(self) -> None:
        """清空缓冲和统计（保护登记保留）。"""
        with self._cond:
            self._keys.clear()
            self._entries.clear()
            self._bytes = 0
            self._overflow_active = False

    def stats(self) -> BufferStats:
        """返回统计快照。

        Returns:
            :class:`BufferStats`。
        """
        with self._cond:
            return BufferStats(
                frames=len(self._entries),
                bytes_used=self._bytes,
                max_bytes=self._max_bytes,
                hard_limit_bytes=self._hard,
                peak_bytes=self._peak,
                total_put=self._total_put,
                evicted=self._evicted,
                forced_evictions=self._forced,
                pins=len(self._pins),
                clock_regressions=self._regressions,
            )
