"""诊断包只收集白名单资料，并剔除秘密和越界媒体。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from gelinspect.domain.entities import Experiment, User
from gelinspect.domain.enums import AuditAction, ExperimentType, Role
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import ExperimentQuery, MediaFile, MediaKind, PageRequest
from gelinspect.logging_setup import configure_logging, shutdown_logging
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.diagnostics_service import DiagnosticCancelledError, DiagnosticsService


class _Auth:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def authorize(self, _session: object, permission: Permission) -> object:
        assert permission is Permission.DIAGNOSTICS_EXPORT
        if not self.allowed:
            raise PermissionDeniedError("denied")
        return User("u1", "maintenance", "Maintenance", Role.ADMIN)


class _Storage:
    def __init__(self, media: list[MediaFile] | None = None) -> None:
        self.events: list[Any] = []
        self.media_items = media or []
        self.telemetry = self
        self.experiment = Experiment(
            "exp-one",
            ExperimentType.GENERAL,
            "builtin",
            "GI-001",
            "operator",
            datetime(2026, 1, 1, tzinfo=UTC),
            (),
        )
        self.experiments = self
        self.audit = self
        self.media = self

    def query(self, _query: object, page: PageRequest) -> object:
        if isinstance(_query, ExperimentQuery):
            return SimpleNamespace(
                items=[SimpleNamespace(experiment_id="exp-one")], total=1, request=page
            )
        return SimpleNamespace(items=[], total=0, request=page)

    def get(self, _experiment_id: str) -> Experiment:
        return self.experiment

    def latest_created_at(self) -> datetime:
        return self.experiment.created_at

    def latest_seq(self) -> int:
        return len(self.events)

    def list_for_experiment(self, _experiment_id: str, **_kwargs: object) -> list[MediaFile]:
        return self.media_items

    def query_temperatures(self, **_kwargs: object) -> object:
        return SimpleNamespace(items=[], total=0)

    def query_device_logs(self, **_kwargs: object) -> object:
        return SimpleNamespace(items=[], total=0)

    def append(self, event: object) -> object:
        self.events.append(event)
        return event


def _service(tmp_path: Path, storage: _Storage, *, allowed: bool = True) -> DiagnosticsService:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    log_dir = data_dir / "logs"
    log_dir.mkdir()
    return DiagnosticsService(
        cast(Any, _Auth(allowed)),
        cast(Any, storage),
        data_dir=data_dir,
        log_dir=log_dir,
        clock=lambda: datetime(2026, 11, 17, tzinfo=UTC),
    )


def test_exports_manifest_and_redacted_rotating_logs_without_database(tmp_path: Path) -> None:
    storage = _Storage()
    service = _service(tmp_path, storage)
    log = tmp_path / "data" / "logs" / "gelinspect.log"
    log.write_text(
        (
            "password=hunter2 password_hash=argon-value token:abc123 "
            "authorization: Bearer jwt-value ok\n"
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "out.zip"

    result = service.create_package(cast(Any, object()), destination)

    assert result.path == destination and result.file_count == 4
    assert len(storage.events) == 1
    assert storage.events[0].action is AuditAction.DATA_EXPORTED
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert names == {
            "records/experiments.json",
            "records/audit.json",
            "records/telemetry.json",
            "logs/gelinspect.log",
            "manifest.json",
        }
        log_data = archive.read("logs/gelinspect.log").decode()
        assert all(
            value not in log_data for value in ("hunter2", "argon-value", "abc123", "jwt-value")
        )
        manifest = json.loads(archive.read("manifest.json"))
        assert all("database" not in item["path"].lower() for item in manifest["files"])


def test_excludes_media_outside_data_root_and_reports_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not included")
    media = MediaFile(
        file_id="one",
        experiment_id="exp-one",
        kind=MediaKind.IMAGE,
        path=str(outside),
        sha256=hashlib.sha256(b"not included").hexdigest(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    storage = _Storage([media])
    service = _service(tmp_path, storage)

    result = service.create_package(cast(Any, object()), tmp_path / "safe.zip")

    assert result.missing and "ValueError" in result.missing[0]
    with zipfile.ZipFile(result.path) as archive:
        assert not any(name.startswith("media/") for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["missing_or_excluded"] == list(result.missing)


def test_includes_only_registered_media_with_matching_checksum(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    media_dir = data_dir / "media"
    media_dir.mkdir(parents=True)
    source = media_dir / "capture.png"
    source.write_bytes(b"image bytes")
    record = MediaFile(
        file_id="one",
        experiment_id="exp-one",
        kind=MediaKind.IMAGE,
        path=str(source),
        sha256=hashlib.sha256(b"image bytes").hexdigest(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = _service(tmp_path, _Storage([record]))

    result = service.create_package(cast(Any, object()), tmp_path / "media.zip")

    assert not result.missing
    with zipfile.ZipFile(result.path) as archive:
        assert archive.read("media/exp-one/one-capture.png") == b"image bytes"


def test_reports_media_checksum_mismatch_without_exporting_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    media_dir = data_dir / "media"
    media_dir.mkdir(parents=True)
    source = media_dir / "capture.png"
    source.write_bytes(b"changed image")
    record = MediaFile(
        file_id="one",
        experiment_id="exp-one",
        kind=MediaKind.IMAGE,
        path=str(source),
        sha256=hashlib.sha256(b"original image").hexdigest(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = _service(tmp_path, _Storage([record]))

    result = service.create_package(cast(Any, object()), tmp_path / "bad-media.zip")

    assert result.missing and "不匹配" in result.missing[0]
    with zipfile.ZipFile(result.path) as archive:
        assert not any(name.startswith("media/") for name in archive.namelist())


def test_cancellation_removes_partial_zip_and_does_not_write_export_audit(tmp_path: Path) -> None:
    storage = _Storage()
    service = _service(tmp_path, storage)
    cancel = threading.Event()
    cancel.set()
    destination = tmp_path / "cancelled.zip"

    with pytest.raises(DiagnosticCancelledError):
        service.create_package(cast(Any, object()), destination, cancel=cancel)

    assert not destination.exists()
    assert not list(tmp_path.glob(".diagnostics-*.zip"))
    assert len(storage.events) == 1
    assert "结果=cancelled" in storage.events[0].detail


def test_requires_diagnostics_export_permission(tmp_path: Path) -> None:
    storage = _Storage()
    service = _service(tmp_path, storage, allowed=False)
    with pytest.raises(PermissionDeniedError):
        service.create_package(cast(Any, object()), tmp_path / "denied.zip")
    assert len(storage.events) == 1


def test_log_rollover_keeps_previous_files_without_automatic_deletion(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_path = configure_logging(log_dir)
    handler = next(
        item
        for item in logging.getLogger().handlers
        if getattr(item, "name", None) == "gelinspect-file-handler"
    )
    handler.maxBytes = 1  # type: ignore[attr-defined]
    logger = logging.getLogger("gelinspect.diagnostics-test")
    try:
        logger.warning("first")
        logger.warning("second")
        handler.flush()
        assert log_path.exists()
        archived = list(log_dir.glob("gelinspect.log.*"))
        assert archived and any("first" in path.read_text(encoding="utf-8") for path in archived)
        assert "second" in log_path.read_text(encoding="utf-8")
    finally:
        shutdown_logging()
