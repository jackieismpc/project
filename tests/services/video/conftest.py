"""视频管线测试的夹具与“重型测试默认跳过”规则。"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

import pytest

PERF_ENV = "GELINSPECT_PERF_VIDEO"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``video_perf`` 标记的测试默认跳过，设置环境变量或 ``-m video_perf`` 运行。"""
    markexpr = str(config.getoption("markexpr", default="") or "")
    if os.environ.get(PERF_ENV) or "video_perf" in markexpr:
        return
    skip = pytest.mark.skip(reason=f"4000×3000 压测约 40 秒，默认跳过；设置 {PERF_ENV}=1 运行")
    for item in items:
        if "video_perf" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def no_thread_leak() -> Iterator[None]:
    """断言测试前后本进程的线程数不变（允许收尾线程稍后退出）。"""
    import time

    before = threading.active_count()
    yield
    deadline = time.monotonic() + 3.0
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.01)
    assert threading.active_count() <= before
