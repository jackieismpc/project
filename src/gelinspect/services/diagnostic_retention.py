"""按明确日期和受保护实验清理受控诊断文件。"""

from __future__ import annotations

import re
import stat
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PurgeFailure:
    """一个未删除或未能记录结果的文件及原因。"""

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """清理结果。"""

    deleted: tuple[Path, ...]
    failures: tuple[PurgeFailure, ...]


Audit = Callable[[str, Path, str], None]
"""审计回调：阶段、相对数据目录路径、结果；写入失败须抛出异常。"""


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
        return path.is_symlink() or bool(
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    except OSError:
        return True


def _safe_file(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
        cursor = root
        if _is_link(cursor):
            return False
        for part in relative.parts:
            cursor = cursor / part
            if _is_link(cursor):
                return False
        return (
            path.is_file()
            and path.stat().st_nlink == 1
            and path.resolve().is_relative_to(root.resolve())
        )
    except (OSError, ValueError):
        return False


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None
    except ValueError:
        return None


def purge_diagnostics(
    data_dir: Path,
    retention_days: int | None,
    today: date,
    protected_experiment_ids: Iterable[str],
    audit: Audit,
    *,
    eligible_experiment_ids: Iterable[str] = (),
    raw_guard: Callable[[str], AbstractContextManager[bool]] | None = None,
) -> PurgeResult:
    """清理超期诊断日志与原始输出；每项先审计，失败则不删除。

    路径必须属于受控布局；活动及签署实验标识须由调用方提供。
    正式视频、图像、报告和数据库不在本函数的清理范围。
    """
    if retention_days is None or retention_days == 0:
        return PurgeResult((), ())
    if retention_days < 0:
        raise ValueError("retention_days must be nonnegative")
    root = data_dir / "diagnostics"
    if not root.exists() or _is_link(root):
        return PurgeResult((), ())
    cutoff = today - timedelta(days=retention_days - 1)
    protected = set(protected_experiment_ids)
    eligible = set(eligible_experiment_ids)
    candidates: list[tuple[Path, str | None]] = []
    logs = root / "logs"
    if logs.is_dir() and not _is_link(logs):
        for item in logs.iterdir():
            day = _day(item.stem) if item.suffix == ".log" else None
            if day is not None and day < cutoff and _safe_file(root, item):
                candidates.append((item, None))
    analysis = root / "analysis"
    if raw_guard is not None and analysis.is_dir() and not _is_link(analysis):
        for dated in analysis.iterdir():
            day = _day(dated.name)
            if day is None or day >= cutoff or _is_link(dated):
                continue
            if not dated.is_dir():
                continue
            for experiment in dated.iterdir():
                if (
                    experiment.name not in eligible
                    or experiment.name in protected
                    or _is_link(experiment)
                    or not experiment.is_dir()
                ):
                    continue
                for item in experiment.iterdir():
                    if re.fullmatch(r"row-\d+-\d+\.zip", item.name) and _safe_file(root, item):
                        candidates.append((item, experiment.name))
    deleted: list[Path] = []
    failures: list[PurgeFailure] = []
    for item, experiment_id in sorted(candidates):
        relative = item.relative_to(data_dir)
        try:
            audit("intent", relative, "pending")
        except Exception as exc:
            failures.append(PurgeFailure(relative, f"audit intent: {exc}"))
            continue
        try:
            guard = (
                raw_guard(experiment_id)
                if experiment_id is not None and raw_guard
                else nullcontext(True)
            )
            with guard as allowed:
                if allowed:
                    if not _safe_file(root, item):
                        raise OSError("path is no longer a safe regular file")
                    item.unlink()
                    deleted.append(relative)
            if not allowed:
                audit("result", relative, "protected")
                continue
        except Exception as exc:
            failures.append(PurgeFailure(relative, str(exc)))
            try:
                audit("result", relative, "failed")
            except Exception as audit_error:
                failures.append(PurgeFailure(relative, f"audit result: {audit_error}"))
            continue
        try:
            audit("result", relative, "deleted")
        except Exception as exc:
            failures.append(PurgeFailure(relative, f"audit result: {exc}"))
    return PurgeResult(tuple(deleted), tuple(failures))
