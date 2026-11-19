"""后台保留策略调度。"""

import threading
import time
from types import SimpleNamespace
from typing import cast

import pytest

from gelinspect.app_context import AppContext
from gelinspect.services import retention_worker


def test_worker_stays_idle_when_disabled_and_retries_failure_then_tracks_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(retention_days=None)
    context = cast("AppContext", SimpleNamespace(settings=settings))
    calls: list[int] = []
    done = threading.Event()

    def run(_context: AppContext, days: int, _now: object) -> None:
        calls.append(days)
        if len(calls) == 1:
            raise RuntimeError("temporary audit failure")
        if days == 3:
            done.set()

    monkeypatch.setattr(retention_worker, "run_retention", run)
    worker = retention_worker.RetentionWorker(context, interval_s=0.02)
    worker.start()
    try:
        time.sleep(0.05)
        assert calls == []
        settings.retention_days = 2
        deadline = time.monotonic() + 3
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == [2, 2]
        settings.retention_days = 3
        assert done.wait(3)
        assert calls == [2, 2, 3]
    finally:
        worker.stop()
