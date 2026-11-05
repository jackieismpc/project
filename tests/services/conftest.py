"""服务层测试共用的夹具。"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest


def _worker_threads() -> list[str]:
    prefixes = ("experiment-engine", "engine-", "video-")
    return [t.name for t in threading.enumerate() if t.name.startswith(prefixes)]


@pytest.fixture
def thread_baseline() -> Iterator[None]:
    """断言测试结束后线程数回到基线，引擎和视频的线程都已退出。"""
    before = threading.active_count()
    yield
    deadline = time.monotonic() + 8.0
    while (threading.active_count() > before or _worker_threads()) and (
        time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert _worker_threads() == []
    assert threading.active_count() <= before
