"""安全地按配置调度受控媒体和诊断文件保留清理。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import AuditRepository, ExperimentQuery, PageRequest
from gelinspect.services.config import AppConfig
from gelinspect.services.diagnostic_retention import purge_diagnostics
from gelinspect.services.media_retention import (
    MediaRetentionStorage,
    purge_managed_media,
    raw_retention_guard,
)


class RetentionStorage(MediaRetentionStorage, Protocol):
    """Storage needed by retention."""

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...


class RetentionEngine(Protocol):
    """Current engine activity."""

    @property
    def active_experiment_id(self) -> str | None:
        """当前运行的实验标识。"""
        ...


class RetentionSettings(Protocol):
    """Current effective retention setting."""

    @property
    def retention_days(self) -> int | None:
        """当前生效的保留天数。"""
        ...


class RetentionContext(Protocol):
    """Runtime dependencies needed by the worker."""

    @property
    def database(self) -> RetentionStorage:
        """清理所需存储。"""
        ...

    @property
    def engine(self) -> RetentionEngine:
        """当前引擎状态。"""
        ...

    @property
    def settings(self) -> RetentionSettings:
        """生效的用户设置。"""
        ...

    @property
    def config(self) -> AppConfig:
        """当前运行配置。"""
        ...


_log = logging.getLogger(__name__)


def run_retention(context: RetentionContext, days: int, now: datetime) -> None:
    """执行一次清理；只有可证明已完成且从未签署的实验允许清理原始输出。"""
    if days <= 0:
        return
    db = context.database
    active_id = context.engine.active_experiment_id
    eligible: set[str] = set()
    offset = 0
    while True:
        page = db.experiments.query(
            ExperimentQuery(state=ExperimentState.COMPLETED), PageRequest(offset, 500)
        )
        for item in page.items:
            if item.experiment_id == active_id or item.signed_at is not None:
                continue
            if not any(
                report.signed for report in db.reports.list_for_experiment(item.experiment_id)
            ):
                eligible.add(item.experiment_id)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break

    def audit(phase: str, path: Path, outcome: str) -> None:
        db.audit.append(
            AuditEvent(
                now,
                "system",
                None,
                AuditAction.DATA_EXPORTED,
                f"诊断保留清理 {phase}: {path.as_posix()} result={outcome}",
            )
        )

    result = purge_diagnostics(
        context.config.data_dir,
        days,
        now.astimezone().date(),
        {active_id} if active_id else set(),
        audit,
        eligible_experiment_ids=eligible,
        raw_guard=lambda experiment_id: raw_retention_guard(
            db, experiment_id, context.engine.active_experiment_id
        ),
    )
    for failure in result.failures:
        _log.error("诊断保留清理失败 path=%s reason=%s", failure.path, failure.reason)
    if result.failures:
        raise RuntimeError("诊断保留清理未完全成功")
    failures = purge_managed_media(
        db,
        context.config.data_dir,
        days,
        now,
        context.engine.active_experiment_id,
        Path(context.config.video.device),
    )
    if failures:
        raise RuntimeError(f"媒体保留清理失败 {failures} 项")


class RetentionWorker:
    """有序停止的后台保留任务；未启用时不扫描文件。"""

    def __init__(self, context: RetentionContext, *, interval_s: float = 60.0) -> None:
        """创建后台任务，interval_s 控制配置检查间隔。"""
        self._context = context
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="retention-worker")

    def start(self) -> None:
        """启动任务。"""
        self._thread.start()

    def stop(self) -> None:
        """等待当前任务结束后退出，调用方随后才可关闭数据库。"""
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        last: tuple[object, int] | None = None
        while not self._stop.is_set():
            now = datetime.now().astimezone()
            days = self._context.settings.retention_days
            if days and days > 0:
                key = (now.date(), days)
                if key != last:
                    try:
                        run_retention(self._context, days, now)
                    except Exception:
                        _log.exception("保留策略执行失败，将稍后重试")
                    else:
                        last = key
            else:
                last = None
            self._stop.wait(self._interval_s)
