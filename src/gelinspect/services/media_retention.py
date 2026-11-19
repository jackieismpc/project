"""受控媒体的保留清理；保留登记与审计。"""

from __future__ import annotations

import hashlib
import logging
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import (
    ExperimentQuery,
    PageRequest,
    ReportRepository,
    TransactionalStorage,
)

logger = logging.getLogger(__name__)


class MediaRetentionStorage(TransactionalStorage, Protocol):
    """清理所需的数据库能力。"""

    @property
    def reports(self) -> ReportRepository:
        """报告仓储。"""
        ...


@contextmanager
def raw_retention_guard(
    storage: MediaRetentionStorage, experiment_id: str, active_id: str | None
) -> Iterator[bool]:
    """持写锁复查实验和所有报告，防止清理与签署并发。"""
    with storage.unit_of_work() as uow:
        experiment = uow.experiments.get(experiment_id)
        reports = uow.reports.list_for_experiment(experiment_id)
        yield bool(
            experiment_id != active_id
            and experiment is not None
            and experiment.state is ExperimentState.COMPLETED
            and experiment.signed_at is None
            and not any(report.signed for report in reports)
        )


def _safe_media(data_dir: Path, path: Path, experiment_id: str) -> bool:
    try:
        root = data_dir.resolve(strict=True)
        resolved = path.resolve(strict=True)
        relative = path.absolute().relative_to(root)
        if (
            len(relative.parts) < 3
            or relative.parts[0] not in {"images", "videos"}
            or relative.parts[1] != experiment_id
        ):
            return False
        current = root
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            if (
                current.is_symlink()
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                return False
        return resolved.is_relative_to(root) and path.is_file() and path.stat().st_nlink == 1
    except (OSError, ValueError):
        return False


def purge_managed_media(
    storage: MediaRetentionStorage,
    data_dir: Path,
    days: int | None,
    now: datetime,
    active_id: str | None = None,
    source_path: Path | None = None,
) -> int:
    """清理受控媒体并返回失败数；登记和哈希保留。"""
    if not days or days < 0:
        return 0
    failures = 0
    cutoff = now.astimezone().date() - timedelta(days=days - 1)
    offset = 0
    while True:
        page = storage.experiments.query(ExperimentQuery(), PageRequest(offset, 500))
        for summary in page.items:
            if (
                summary.experiment_id == active_id
                or summary.created_at.astimezone().date() >= cutoff
            ):
                continue
            experiment = storage.experiments.get(summary.experiment_id)
            reports = storage.reports.list_for_experiment(summary.experiment_id)
            if (
                experiment is None
                or experiment.state is not ExperimentState.COMPLETED
                or experiment.signed_at is not None
                or any(report.signed for report in reports)
            ):
                continue
            for media in storage.media.list_for_experiment(summary.experiment_id):
                path = Path(media.path)
                if storage.media.purge_state(media.file_id) == "intent" and not path.exists():
                    try:
                        with storage.unit_of_work() as uow:
                            current = uow.media.get(media.file_id)
                            if current == media and uow.media.purge_record_matches(media):
                                uow.media.set_purge_state(media, "missing", now)
                                uow.audit.append(
                                    AuditEvent(
                                        now,
                                        "system",
                                        None,
                                        AuditAction.DATA_EXPORTED,
                                        f"保留清理意图后文件缺失，原因待核实：{media.file_id} "
                                        f"SHA-256={media.sha256}",
                                        media.experiment_id,
                                    )
                                )
                    except Exception:
                        failures += 1
                        logger.exception("恢复媒体清理记录 %s 失败", media.file_id)
                    continue
                if source_path is not None:
                    source_location = source_path.resolve()
                    target = path.resolve()
                    if target == source_location or target.is_relative_to(source_location):
                        continue
                if not _safe_media(data_dir, path, media.experiment_id):
                    continue
                if storage.media.purge_state(media.file_id) == "purged":
                    continue
                try:
                    with storage.unit_of_work() as uow:
                        uow.media.set_purge_state(media, "intent", now)
                        uow.audit.append(
                            AuditEvent(
                                now,
                                "system",
                                None,
                                AuditAction.DATA_EXPORTED,
                                f"保留策略清理意图：{media.file_id} SHA-256={media.sha256}",
                                media.experiment_id,
                            )
                        )
                    with storage.unit_of_work() as uow:
                        experiment = uow.experiments.get(media.experiment_id)
                        reports = uow.reports.list_for_experiment(media.experiment_id)
                        if (
                            experiment is None
                            or experiment.state is not ExperimentState.COMPLETED
                            or experiment.signed_at is not None
                            or any(report.signed for report in reports)
                        ):
                            continue
                        if not _safe_media(data_dir, path, media.experiment_id):
                            continue
                        digest = hashlib.sha256()
                        with path.open("rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if digest.hexdigest() != media.sha256:
                            raise ValueError("媒体哈希不匹配")
                        path.unlink()
                        uow.media.set_purge_state(media, "purged", now)
                        uow.audit.append(
                            AuditEvent(
                                now,
                                "system",
                                None,
                                AuditAction.DATA_EXPORTED,
                                f"保留策略已清理：{media.file_id} SHA-256={media.sha256}",
                                media.experiment_id,
                            )
                        )
                except Exception:
                    failures += 1
                    logger.exception("保留策略清理媒体 %s 失败", media.file_id)
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break
    return failures
