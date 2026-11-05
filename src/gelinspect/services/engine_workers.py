"""试验引擎的两个辅助线程：事件分发和算法调用。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from gelinspect.interfaces.analysis import (
    AnalyzerInfo,
    GelAnalyzer,
    RowAnalysisRequest,
    RowAnalysisResult,
)
from gelinspect.services.engine_types import EngineEvent, EngineEventListener
from gelinspect.services.video.clock import Clock

logger = logging.getLogger("gelinspect.services.engine")

_MAX_QUEUED_EVENTS = 10000


class EventDispatcher:
    """把引擎事件异步分发给监听者。

    引擎线程只入队，慢的、卡死的或抛异常的监听者都不影响翻转时序。
    队列有上限，只有监听者长期卡死才会溢出，此时丢弃最旧的事件并记录日志。
    """

    def __init__(self) -> None:
        """创建分发器，不启动线程。"""
        self._cond = threading.Condition()
        self._queue: deque[EngineEvent] = deque()
        self._listeners: list[EngineEventListener] = []
        self._thread: threading.Thread | None = None
        self._stopping = False

    def subscribe(self, listener: EngineEventListener) -> None:
        """添加监听者。

        Args:
            listener: 事件处理函数。
        """
        with self._cond:
            if listener not in self._listeners:
                self._listeners = [*self._listeners, listener]

    def unsubscribe(self, listener: EngineEventListener) -> None:
        """移除监听者，不存在时忽略。

        Args:
            listener: 事件处理函数。
        """
        with self._cond:
            self._listeners = [item for item in self._listeners if item != listener]

    def emit(self, event: EngineEvent) -> None:
        """发布事件。非阻塞，不抛异常。

        Args:
            event: 事件。
        """
        with self._cond:
            if len(self._queue) >= _MAX_QUEUED_EVENTS:
                self._queue.popleft()
                logger.error("引擎事件队列已满，丢弃最旧的事件")
            self._queue.append(event)
            self._cond.notify_all()

    def start(self) -> None:
        """启动分发线程，已启动时无操作。"""
        with self._cond:
            self._stopping = False
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="engine-events", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float) -> bool:
        """投递完已入队的事件后停止线程。

        Args:
            timeout_s: 等待线程退出的时限。

        Returns:
            线程是否已退出。
        """
        with self._cond:
            thread = self._thread
            self._stopping = True
            self._cond.notify_all()
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout_s)
        return not thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopping:
                    self._cond.wait()
                if not self._queue:
                    return
                event = self._queue.popleft()
                listeners = self._listeners
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    logger.exception("引擎事件监听者抛出异常（已忽略）：%s", type(event).__name__)


@dataclass(frozen=True, slots=True)
class AnalysisStarted:
    """算法开始处理一排（排队结束，开始计时）。

    Attributes:
        row_index: 排号。
        attempt: 第几次分析。
        started_monotonic_s: 开始时的单调时钟读数。
    """

    row_index: int
    attempt: int
    started_monotonic_s: float


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """一次分析的结局，``result`` 和 ``error`` 恰有一个不为 None。

    Attributes:
        row_index: 排号。
        attempt: 第几次分析。
        result: 算法结果。
        error: 分析失败的异常，包括读回原图失败和算法抛出的任何异常。
        wall_s: ``analyze_row`` 的真实耗时，没有调用到算法时为 0。
    """

    row_index: int
    attempt: int
    result: RowAnalysisResult | None
    error: Exception | None
    wall_s: float


class AnalysisWorker:
    """算法专用线程，``load``、``analyze_row``、``close`` 都在这条线程上串行执行。

    用自己的守护线程而不是线程池：算法卡死时进程仍能退出。
    """

    def __init__(
        self,
        analyzer: GelAnalyzer,
        clock: Clock,
        on_started: Callable[[AnalysisStarted], None],
        on_done: Callable[[AnalysisOutcome], None],
    ) -> None:
        """创建工作线程，不启动。

        Args:
            analyzer: 算法实现。
            clock: 计时用的真实时钟。
            on_started: 开始分析时在本线程调用，不得阻塞。
            on_done: 分析结束时在本线程调用，不得阻塞。
        """
        self._analyzer = analyzer
        self._clock = clock
        self._on_started = on_started
        self._on_done = on_done
        self._cond = threading.Condition()
        self._jobs: deque[Callable[[], None]] = deque()
        self._closing = False
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        """线程是否还在。算法卡死时，运行结束后它仍可能活着。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """启动线程。"""
        self._thread = threading.Thread(target=self._run, name="engine-analysis", daemon=True)
        self._thread.start()

    def load(self, config_path: Path | None) -> Future[AnalyzerInfo]:
        """在工作线程上加载算法。

        Args:
            config_path: 算法配置文件。

        Returns:
            结果为算法版本的 ``Future``，失败时携带 ``AnalyzerError``。
        """
        future: Future[AnalyzerInfo] = Future()

        def job() -> None:
            try:
                future.set_result(self._analyzer.load(config_path))
            except Exception as exc:
                future.set_exception(exc)

        self._enqueue(job)
        return future

    def submit(
        self, row_index: int, attempt: int, build_request: Callable[[], RowAnalysisRequest]
    ) -> None:
        """排队分析一排。结局通过 ``on_done`` 送回。

        请求在工作线程上构造：重试时要从磁盘读回 12 MP 原图，不能占用引擎线程。

        Args:
            row_index: 排号。
            attempt: 第几次分析。
            build_request: 构造请求的函数。
        """

        def job() -> None:
            self._analyze(row_index, attempt, build_request)

        self._enqueue(job)

    def _analyze(
        self, row_index: int, attempt: int, build_request: Callable[[], RowAnalysisRequest]
    ) -> None:
        wall_s = 0.0
        try:
            request = build_request()
            started = self._clock.monotonic()
            self._on_started(AnalysisStarted(row_index, attempt, started))
            try:
                result = self._analyzer.analyze_row(request)
            finally:
                wall_s = self._clock.monotonic() - started
        except Exception as exc:
            self._on_done(AnalysisOutcome(row_index, attempt, None, exc, wall_s))
        else:
            self._on_done(AnalysisOutcome(row_index, attempt, result, None, wall_s))

    def shutdown(self, timeout_s: float) -> bool:
        """丢弃排队的任务，关闭算法并结束线程。

        Args:
            timeout_s: 等待线程退出的时限。算法卡死时超时，线程在算法返回后自行结束。

        Returns:
            线程是否已退出。
        """
        with self._cond:
            self._jobs.clear()
            self._closing = True
            self._cond.notify_all()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_s)
        return not thread.is_alive()

    def _enqueue(self, job: Callable[[], None]) -> None:
        with self._cond:
            self._jobs.append(job)
            self._cond.notify_all()

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._jobs and not self._closing:
                        self._cond.wait()
                    if not self._jobs:
                        break
                    job = self._jobs.popleft()
                job()
                # 任务的闭包可能持有整排 12 MP 帧，等下一个任务时不能继续占着。
                del job
        finally:
            try:
                self._analyzer.close()
            except Exception:
                logger.exception("关闭算法时抛出异常（已忽略）")
