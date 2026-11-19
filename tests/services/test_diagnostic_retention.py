"""受控诊断文件保留清理。"""

from contextlib import nullcontext
from datetime import date
from pathlib import Path

import pytest

from gelinspect.services.diagnostic_retention import purge_diagnostics


def write(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content", encoding="utf-8")
    return path


def test_only_expired_managed_diagnostics_are_deleted_and_audited(tmp_path: Path) -> None:
    old = write(tmp_path, "diagnostics/logs/2026-01-01.log")
    recent = write(tmp_path, "diagnostics/logs/2026-11-19.log")
    raw = write(tmp_path, "diagnostics/analysis/2026-01-01/EXP-1/row-01-123.zip")
    protected = write(tmp_path, "diagnostics/analysis/2026-01-01/EXP-2/row-01-123.zip")
    unmanaged = write(tmp_path, "diagnostics/analysis/2026-01-01/EXP-1/notes.txt")
    media = write(tmp_path, "videos/EXP-1/row-01.mp4")
    events: list[tuple[str, Path, str]] = []
    result = purge_diagnostics(
        tmp_path,
        30,
        date(2026, 11, 19),
        {"EXP-2"},
        lambda a, p, o: events.append((a, p, o)),
        eligible_experiment_ids={"EXP-1", "EXP-2"},
        raw_guard=lambda _experiment_id: nullcontext(True),
    )
    assert set(result.deleted) == {old.relative_to(tmp_path), raw.relative_to(tmp_path)}
    assert not result.failures and not old.exists() and not raw.exists()
    assert all(p.exists() for p in (recent, protected, unmanaged, media))
    assert [event[0] for event in events] == ["intent", "result", "intent", "result"]


def test_audit_failure_prevents_deletion_and_zero_disables_retention(tmp_path: Path) -> None:
    path = write(tmp_path, "diagnostics/logs/2026-01-01.log")

    def fail(_phase: str, _path: Path, _outcome: str) -> None:
        raise RuntimeError("audit unavailable")

    result = purge_diagnostics(tmp_path, 30, date(2026, 11, 19), (), fail)
    assert path.exists() and not result.deleted and len(result.failures) == 1
    assert not purge_diagnostics(tmp_path, 0, date(2026, 11, 19), (), fail).failures
    assert not purge_diagnostics(tmp_path, None, date(2026, 11, 19), (), fail).failures


def test_hard_link_to_external_file_is_not_deleted(tmp_path: Path) -> None:
    import os

    original = write(tmp_path, "outside.txt")
    target = tmp_path / "diagnostics/logs/2026-01-01.log"
    target.parent.mkdir(parents=True)
    try:
        os.link(original, target)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    result = purge_diagnostics(tmp_path, 1, date(2026, 11, 19), (), lambda *_args: None)
    assert not result.deleted and original.exists() and target.exists()


def test_linked_diagnostic_directory_does_not_escape(tmp_path: Path) -> None:
    import os
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    protected = write(outside, "2026-01-01.log")
    root = tmp_path / "diagnostics"
    root.mkdir()
    link = root / "logs"
    if os.name == "nt":
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
        )
        if junction.returncode:
            pytest.skip("directory junction is unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        result = purge_diagnostics(tmp_path, 1, date(2026, 11, 19), (), lambda *_args: None)
        assert not result.deleted and protected.exists()
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_result_audit_failure_is_reported_after_delete(tmp_path: Path) -> None:
    path = write(tmp_path, "diagnostics/logs/2026-01-01.log")

    def audit(phase: str, _path: Path, _outcome: str) -> None:
        if phase == "result":
            raise RuntimeError("audit unavailable")

    result = purge_diagnostics(tmp_path, 1, date(2026, 11, 19), (), audit)
    assert not path.exists() and result.deleted == (path.relative_to(tmp_path),)
    assert len(result.failures) == 1 and "audit result" in result.failures[0].reason


def test_days_include_today_and_unknown_experiment_is_preserved(tmp_path: Path) -> None:
    keep = write(tmp_path, "diagnostics/logs/2026-11-18.log")
    old = write(tmp_path, "diagnostics/logs/2026-11-17.log")
    unknown = write(tmp_path, "diagnostics/analysis/2026-01-01/UNKNOWN/row-01-123.zip")
    result = purge_diagnostics(tmp_path, 2, date(2026, 11, 19), (), lambda *_args: None)
    assert keep.exists() and not old.exists() and unknown.exists()
    assert result.deleted == (old.relative_to(tmp_path),)


def test_raw_requires_guard_and_rechecks_before_unlink(tmp_path: Path) -> None:
    raw = write(tmp_path, "diagnostics/analysis/2026-01-01/EXP-1/row-01-123.zip")
    events: list[str] = []

    def audit(phase: str, _path: Path, _outcome: str) -> None:
        events.append(phase)

    purge_diagnostics(tmp_path, 1, date(2026, 11, 19), (), audit, eligible_experiment_ids={"EXP-1"})
    assert raw.exists() and events == []
    purge_diagnostics(
        tmp_path,
        1,
        date(2026, 11, 19),
        (),
        audit,
        eligible_experiment_ids={"EXP-1"},
        raw_guard=lambda _id: nullcontext(False),
    )
    assert raw.exists() and events == ["intent", "result"]
