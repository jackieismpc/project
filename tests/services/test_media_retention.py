"""受控媒体保留策略的隔离数据库测试。"""

import hashlib
import json
import threading
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from gelinspect.domain.entities import Experiment
from gelinspect.domain.enums import ExperimentType
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import MediaFile, MediaKind
from gelinspect.services.diagnostics_service import DiagnosticsService
from gelinspect.services.media_retention import purge_managed_media, raw_retention_guard
from tests.services.conftest import NOW, Stack
from tests.services.engine_helpers import make_harness
from tests.services.test_diagnostics_service import _Auth


def test_purge_only_completed_unsigned_managed_media(stack: Stack, tmp_path: Path) -> None:
    data = tmp_path / "data"
    for name, state in (("done", ExperimentState.COMPLETED), ("draft", ExperimentState.DRAFT)):
        experiment = Experiment(
            name, ExperimentType.GENERAL, "builtin", "GI", "user", NOW, (), state=state
        )
        stack.database.experiments.save(experiment)
        path = data / "images" / name / "frame.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(name.encode())
        stack.database.media.add(
            MediaFile(
                name,
                name,
                MediaKind.IMAGE,
                str(path),
                hashlib.sha256(name.encode()).hexdigest(),
                NOW,
            )
        )
    purge_managed_media(stack.database, data, 1, NOW + timedelta(days=10))
    assert not (data / "images" / "done" / "frame.png").exists()
    assert (data / "images" / "draft" / "frame.png").exists()
    assert stack.database.media.purge_state("done") == "purged"
    assert stack.database.media.get("done") is not None


def test_purge_keeps_signed_active_and_audit_failure(stack: Stack, tmp_path: Path) -> None:
    data = tmp_path / "data"
    for name in ("signed", "active", "audit"):
        experiment = Experiment(
            name,
            ExperimentType.GENERAL,
            "builtin",
            "GI",
            "user",
            NOW,
            (),
            state=ExperimentState.COMPLETED,
            signed_at=NOW if name == "signed" else None,
        )
        stack.database.experiments.save(experiment)
        path = data / "videos" / name / "capture.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(name.encode())
        stack.database.media.add(
            MediaFile(
                name,
                name,
                MediaKind.VIDEO,
                str(path),
                hashlib.sha256(name.encode()).hexdigest(),
                NOW,
            )
        )
    with patch.object(
        stack.database, "unit_of_work", side_effect=RuntimeError("audit unavailable")
    ):
        purge_managed_media(stack.database, data, 1, NOW + timedelta(days=10), active_id="active")
    assert all(
        (data / "videos" / name / "capture.mp4").exists() for name in ("signed", "active", "audit")
    )
    assert stack.database.media.purge_state("audit") is None


def test_recovers_missing_file_after_recorded_intent(stack: Stack, tmp_path: Path) -> None:
    data = tmp_path / "data"
    stack.database.experiments.save(
        Experiment(
            "done",
            ExperimentType.GENERAL,
            "builtin",
            "GI",
            "user",
            NOW,
            (),
            state=ExperimentState.COMPLETED,
        )
    )
    path = data / "images" / "done" / "frame.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"frame")
    media = MediaFile(
        "one", "done", MediaKind.IMAGE, str(path), hashlib.sha256(b"frame").hexdigest(), NOW
    )
    stack.database.media.add(media)
    stack.database.media.set_purge_state(media, "intent", NOW)
    path.unlink()
    purge_managed_media(stack.database, data, 1, NOW + timedelta(days=10))
    assert stack.database.media.purge_state("one") == "missing"
    assert any("原因待核实" in item for item in stack.audit())


def test_retention_guard_serializes_concurrent_signing_write(stack: Stack) -> None:
    stack.database.experiments.save(
        Experiment(
            "done",
            ExperimentType.GENERAL,
            "builtin",
            "GI",
            "user",
            NOW,
            (),
            state=ExperimentState.COMPLETED,
        )
    )
    started, acquired = threading.Event(), threading.Event()

    def signing_writer() -> None:
        started.set()
        with stack.database.unit_of_work():
            acquired.set()

    with raw_retention_guard(stack.database, "done", None) as allowed:
        assert allowed
        thread = threading.Thread(target=signing_writer)
        thread.start()
        assert started.wait(2)
        assert not acquired.wait(0.1)
    thread.join(5)
    assert acquired.is_set()


def test_real_run_exports_recorded_video_and_raw_analysis(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    try:
        h.save_and_start()
        h.incubating()
        h.fire_rows()
        h.finished()
        service = DiagnosticsService(
            cast(Any, _Auth()),
            h.database,
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "data" / "logs",
        )
        result = service.create_package(cast(Any, object()), tmp_path / "diagnostics.zip")
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
            assert any(name.startswith("media/") and name.endswith(".mp4") for name in names)
            raw = next(
                name for name in names if name.startswith("analysis/") and name.endswith(".zip")
            )
            import io

            with zipfile.ZipFile(io.BytesIO(archive.read(raw))) as raw_zip:
                payload = json.loads(raw_zip.read("result.json"))
                assert (
                    payload["experiment_id"]
                    and payload["results"]
                    and payload["analyzer_info"]["model_version"]
                )
                assert payload["frames_before"] and payload["frames_after"]
            manifest = json.loads(archive.read("manifest.json"))
            assert any(
                entry["path"] == raw and len(entry["sha256"]) == 64 for entry in manifest["files"]
            )
    finally:
        h.close()
