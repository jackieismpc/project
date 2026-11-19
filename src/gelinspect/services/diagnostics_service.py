"""受维护权限保护的诊断包收集器。

只导出结构化业务快照、登记媒体和轮转日志，不复制数据库、配置目录或密钥目录。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import stat
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

from gelinspect.domain.entities import AuditEvent, Experiment, User
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ExperimentQuery,
    ExperimentRepository,
    MediaRepository,
    PageRequest,
    TelemetryRepository,
)
from gelinspect.services.auth import AuthError, AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode

_log = logging.getLogger(__name__)
_PAGE_SIZE = 500
_CHUNK = 1024 * 1024
_SECRET_KEY = re.compile(r"password|passwd|secret|token|private.?key|credential|api.?key", re.I)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(password(?:[_ -]?hash)?|passwd|secret|token|private[_ -]?key|authorization|api[_ -]?key)"
    r"['\"]?(\s*[:=]\s*)['\"]?(?:Bearer\s+)?([^'\"\s,;}]+)"
)


def diagnostic_cancel_token() -> threading.Event:
    """Create a thread-safe cancellation flag for background diagnostic exports."""
    return threading.Event()


class DiagnosticStorage(Protocol):
    @property
    def experiments(self) -> ExperimentRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def media(self) -> MediaRepository: ...

    @property
    def telemetry(self) -> TelemetryRepository: ...


@dataclass(frozen=True, slots=True)
class DiagnosticPackageResult:
    """诊断包生成结果摘要。"""

    path: Path
    file_count: int
    missing: tuple[str, ...]
    size_bytes: int


class DiagnosticCancelledError(Exception):
    """诊断包生成被用户取消。"""


class DiagnosticsService:
    """生成不含凭据、私钥或数据库文件的诊断资料包。"""

    def __init__(
        self,
        auth: AuthService,
        storage: DiagnosticStorage,
        *,
        data_dir: Path,
        log_dir: Path,
        clock: Callable[[], datetime] | None = None,
        retention_days: Callable[[], int | None] | None = None,
        video_source: str | None = None,
    ) -> None:
        """Create collector bound to application services and data roots."""
        self._auth = auth
        self._storage = storage
        self._data_dir = data_dir
        self._log_dir = log_dir
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retention_days = retention_days or (lambda: None)
        self._video_source = video_source

    def available_date_range(self, session: Session | None) -> tuple[date | None, date]:
        """返回允许选择的最早日期和今天（均为本地日期）。"""
        self._auth.authorize(session, Permission.DIAGNOSTICS_EXPORT)
        today = self._clock().astimezone().date()
        days = self._retention_days()
        return (today - timedelta(days=days - 1) if days and days > 0 else None, today)

    def create_package(
        self,
        session: Session | None,
        destination: Path,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> DiagnosticPackageResult:
        """汇总诊断材料，成功后原子替换目标文件并追加导出审计。"""
        destination = Path(destination)
        try:
            user = self._auth.authorize(session, Permission.DIAGNOSTICS_EXPORT)
        except AuthError as error:
            self._audit_outcome(session, None, destination.name, "denied", error)
            raise
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ServiceError("开始日期不能晚于结束日期", ServiceErrorCode.EXPORT_FAILED)
        if end_date is not None and end_date > self._clock().astimezone().date():
            raise ServiceError("结束日期不能晚于今天", ServiceErrorCode.EXPORT_FAILED)
        earliest, today = self.available_date_range(session)
        if earliest is not None:
            if start_date is None:
                start_date = earliest
            elif start_date < earliest:
                raise ServiceError("开始日期超出数据保留范围", ServiceErrorCode.EXPORT_FAILED)
        if end_date is None:
            end_date = today

        def in_range(value: datetime) -> bool:
            local = value.astimezone().date()
            return (start_date is None or local >= start_date) and (
                end_date is None or local <= end_date
            )

        if destination.suffix.lower() != ".zip" or not destination.name:
            target_error = ServiceError("诊断包目标必须是 ZIP 文件", ServiceErrorCode.EXPORT_FAILED)
            self._audit_outcome(session, user, destination.name, "failed", target_error)
            raise target_error
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._audit_outcome(session, user, destination.name, "failed", error)
            raise ServiceError(
                f"诊断包目标不可写：{error}", ServiceErrorCode.EXPORT_FAILED
            ) from error
        now = self._clock().astimezone(UTC)
        missing: list[str] = []
        entries: list[tuple[str, bytes | Path]] = []
        expected_media_hashes: dict[str, str] = {}
        next_auth_check = [time.monotonic()]

        def check_cancel() -> None:
            if cancel is not None and cancel.is_set():
                error = DiagnosticCancelledError()
                self._audit_outcome(session, user, destination.name, "cancelled", error)
                raise error
            now_monotonic = time.monotonic()
            if now_monotonic >= next_auth_check[0]:
                try:
                    self._auth.authorize(session, Permission.DIAGNOSTICS_EXPORT)
                except AuthError as error:
                    self._audit_outcome(session, user, destination.name, "denied", error)
                    raise
                next_auth_check[0] = now_monotonic + 2.0

        check_cancel()
        experiments = [e for e in self._load_experiments(check_cancel) if in_range(e.created_at)]
        experiment_ids = [item.experiment_id for item in experiments]
        audits = [a for a in self._load_audits(check_cancel) if in_range(a.event.occurred_at)]
        telemetry = self._load_telemetry(experiment_ids, check_cancel)
        entries.append(
            ("records/experiments.json", _json_bytes([_experiment_json(e) for e in experiments]))
        )
        entries.append(("records/audit.json", _json_bytes([_audit_json(a) for a in audits])))
        entries.append(("records/telemetry.json", _json_bytes(telemetry)))
        entries.append(
            (
                "system/system.json",
                _json_bytes(
                    {
                        "platform": platform.platform(),
                        "machine": platform.machine(),
                        "python": sys.version,
                        "application": "GelInspect",
                    }
                ),
            )
        )

        for experiment_id in experiment_ids:
            check_cancel()
            media_items = self._storage.media.list_for_experiment(experiment_id)
            if not any(item.kind.value == "video" for item in media_items):
                missing.append(f"{experiment_id}: 未登记分析期间录像")
            for media in media_items:
                safe_id = _safe_component(experiment_id)
                original = Path(media.path)
                purge_state = self._storage.media.purge_state(media.file_id)
                if purge_state == "purged":
                    missing.append(
                        f"{safe_id}/{_safe_component(original.name)}: 保留策略已清理"
                        f"（SHA-256={media.sha256}）"
                    )
                    continue
                if purge_state == "missing":
                    missing.append(
                        f"{safe_id}/{_safe_component(original.name)}: "
                        f"清理意图后文件缺失，原因待核实（SHA-256={media.sha256}）"
                    )
                    continue
                if purge_state == "intent" and not original.exists():
                    missing.append(
                        f"{safe_id}/{_safe_component(original.name)}: 清理意图待核实"
                        f"（SHA-256={media.sha256}）"
                    )
                    continue
                try:
                    source = _safe_media_path(self._data_dir, original)
                    digest, _size = _hash_file(source, check_cancel)
                    if digest.lower() != media.sha256.lower():
                        missing.append(f"{safe_id}/{_safe_component(original.name)}: SHA-256不匹配")
                        continue
                except (OSError, ValueError) as exc:
                    missing.append(
                        f"{safe_id}/{_safe_component(original.name)}: {type(exc).__name__}"
                    )
                    continue
                media_name = f"{_safe_component(media.file_id)}-{_safe_component(original.name)}"
                archive_name = f"media/{safe_id}/{media_name}"
                entries.append((archive_name, source))
                expected_media_hashes[archive_name] = media.sha256.lower()

        analysis_root = self._data_dir / "diagnostics" / "analysis"
        for experiment in experiments:
            # 分析可能跨越日期：扫描受控日期目录，按实验标识关联。
            found_analysis = False
            if _safe_directory(self._data_dir, analysis_root):
                for day_dir in analysis_root.iterdir():
                    if not _safe_directory(self._data_dir, day_dir):
                        continue
                    folder = day_dir / _safe_component(experiment.experiment_id)
                    if _safe_directory(self._data_dir, folder):
                        for path in sorted(folder.glob("row-*.zip")):
                            try:
                                source = _safe_media_path(self._data_dir, path)
                            except (OSError, ValueError):
                                continue
                            found_analysis = True
                            entries.append(
                                (f"analysis/{day_dir.name}/{folder.name}/{path.name}", source)
                            )
            if not found_analysis:
                missing.append(f"{experiment.experiment_id}: 未找到原始算法输出")

        for path in self._log_files(start_date, end_date):
            try:
                entries.append(
                    (f"logs/{_safe_component(path.name)}", _scrub_log(path, check_cancel))
                )
            except OSError as exc:
                missing.append(f"logs/{_safe_component(path.name)}: {type(exc).__name__}")

        total = max(1, len(entries))
        checksums: list[dict[str, object]] = []
        temporary: Path | None = None
        done = 0
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".diagnostics-", suffix=".zip", dir=destination.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, item in entries:
                    check_cancel()
                    file_digest = hashlib.sha256()
                    size = 0
                    with archive.open(name, "w") as target:
                        if isinstance(item, bytes):
                            chunks: Iterable[bytes] = (item,)
                        else:
                            source_handle = (
                                _scrub_analysis_archive(item, check_cancel)
                                if name.startswith("analysis/")
                                else item.open("rb")
                            )
                            chunks = _iter_chunks(source_handle, check_cancel)
                        try:
                            for chunk in chunks:
                                check_cancel()
                                file_digest.update(chunk)
                                size += len(chunk)
                                target.write(chunk)
                        finally:
                            if not isinstance(item, bytes):
                                source_handle.close()
                    expected_digest = expected_media_hashes.get(name)
                    if expected_digest is not None and file_digest.hexdigest() != expected_digest:
                        raise ValueError(f"媒体文件在扫描后发生变化：{name}")
                    checksums.append(
                        {"path": name, "sha256": file_digest.hexdigest(), "size_bytes": size}
                    )
                    done += 1
                    if progress is not None:
                        progress(done, total)
                manifest = {
                    "created_at": now.isoformat(),
                    "application": "GelInspect",
                    "video_source_type": self._video_source,
                    "video_scope": "每排翻转及抓帧期间录制；不包含整个孵育阶段",
                    "analysis_export": "原始结果文本已脱敏，掩码保持原样；本地原始文件不变",
                    "local_date_range": {
                        "start": start_date.isoformat() if start_date else None,
                        "end": end_date.isoformat() if end_date else None,
                    },
                    "files": checksums,
                    "missing_or_excluded": missing,
                    "excluded": [
                        "数据库文件",
                        "密码及密码哈希",
                        "令牌",
                        "签名私钥",
                        "审计密钥",
                        "配置密钥目录",
                    ],
                }
                archive.writestr("manifest.json", _json_bytes(manifest))
            check_cancel()
            os.replace(temporary, destination)
            temporary = None
        except (DiagnosticCancelledError, AuthError):
            raise
        except Exception as exc:
            self._audit_outcome(session, user, destination.name, "failed", exc)
            _log.exception("诊断包生成失败")
            raise ServiceError(f"诊断包生成失败：{exc}", ServiceErrorCode.EXPORT_FAILED) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        self._audit_outcome(
            session,
            user,
            destination.name,
            "success",
            detail=f"{len(checksums)} 个文件，缺失/排除 {len(missing)} 项",
        )
        return DiagnosticPackageResult(
            destination, len(checksums), tuple(missing), destination.stat().st_size
        )

    def _audit_outcome(
        self,
        session: Session | None,
        user: User | None,
        filename: str,
        outcome: str,
        error: Exception | None = None,
        *,
        detail: str = "",
    ) -> None:
        session_user = getattr(session, "user", None)
        actor = (
            user.username if user is not None else getattr(session_user, "username", "anonymous")
        )
        role = user.role if user is not None else getattr(session_user, "role", None)
        message = f"导出诊断包：{filename}，结果={outcome}"
        if detail:
            message += f"，{detail}"
        if error is not None:
            message += f"，原因={getattr(error, 'code', type(error).__name__)}"
        try:
            self._storage.audit.append(
                AuditEvent(
                    occurred_at=self._clock().astimezone(UTC),
                    actor=actor,
                    actor_role=role,
                    action=AuditAction.DATA_EXPORTED,
                    detail=message,
                )
            )
        except Exception:
            _log.exception("记录诊断包操作审计失败")

    def _load_experiments(self, check_cancel: Callable[[], None]) -> list[Experiment]:
        result: list[Experiment] = []
        offset = 0
        total: int | None = None
        query = ExperimentQuery(max_created_at=self._storage.experiments.latest_created_at())
        while total is None or offset < total:
            check_cancel()
            page = self._storage.experiments.query(query, PageRequest(offset, _PAGE_SIZE))
            total = page.total
            for summary in page.items:
                check_cancel()
                experiment = self._storage.experiments.get(summary.experiment_id)
                if experiment is not None:
                    result.append(experiment)
            offset += len(page.items)
            if not page.items:
                break
        return result

    def _load_audits(self, check_cancel: Callable[[], None]) -> list[AuditRecord]:
        result: list[AuditRecord] = []
        offset = 0
        total: int | None = None
        query = AuditQuery(max_seq=self._storage.audit.latest_seq())
        while total is None or offset < total:
            check_cancel()
            page = self._storage.audit.query(query, PageRequest(offset, _PAGE_SIZE))
            total = page.total
            result.extend(page.items)
            offset += len(page.items)
            if not page.items:
                break
        return result

    def _load_telemetry(
        self, experiment_ids: list[str], check_cancel: Callable[[], None]
    ) -> dict[str, list[object]]:
        result: dict[str, list[object]] = {"temperatures": [], "device_logs": []}
        for experiment_id in experiment_ids:
            check_cancel()
            offset = 0
            while True:
                check_cancel()
                temperature_page = self._storage.telemetry.query_temperatures(
                    experiment_id=experiment_id, page=PageRequest(offset, _PAGE_SIZE)
                )
                result["temperatures"].extend(
                    _safe_value(asdict(item)) for item in temperature_page.items
                )
                offset += len(temperature_page.items)
                if not temperature_page.items or offset >= temperature_page.total:
                    break
            offset = 0
            while True:
                check_cancel()
                device_page = self._storage.telemetry.query_device_logs(
                    experiment_id=experiment_id, page=PageRequest(offset, _PAGE_SIZE)
                )
                result["device_logs"].extend(
                    _safe_value(asdict(item)) for item in device_page.items
                )
                offset += len(device_page.items)
                if not device_page.items or offset >= device_page.total:
                    break
        return result

    def _log_files(self, start_date: date | None, end_date: date | None) -> list[Path]:
        directory = self._data_dir / "diagnostics" / "logs"
        if not directory.is_dir() or directory.is_symlink():
            return []
        result = []
        for path in directory.glob("????-??-??.log"):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if (
                path.is_file()
                and not path.is_symlink()
                and (start_date is None or day >= start_date)
                and (end_date is None or day <= end_date)
            ):
                result.append(path)
        return sorted(result)


def _iter_chunks(handle: BinaryIO, check_cancel: Callable[[], None]) -> Iterator[bytes]:
    while True:
        check_cancel()
        chunk = handle.read(_CHUNK)
        if not chunk:
            break
        yield chunk


def _scrub_analysis_archive(path: Path, check_cancel: Callable[[], None]) -> BinaryIO:
    """流式重打包原始输出，仅脱敏 JSON 文本，掩码逐块复制。"""
    output = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - 返回给调用方关闭
    try:
        with (
            zipfile.ZipFile(path) as source,
            zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
        ):
            for info in source.infolist():
                check_cancel()
                if info.filename == "result.json":
                    value = json.loads(source.read(info).decode("utf-8"))
                    target.writestr(info.filename, _json_bytes(_safe_value(value)))
                else:
                    with (
                        source.open(info) as source_entry,
                        target.open(info.filename, "w", force_zip64=True) as target_entry,
                    ):
                        for chunk in _iter_chunks(cast(BinaryIO, source_entry), check_cancel):
                            target_entry.write(chunk)
        output.seek(0)
        return cast(BinaryIO, output)
    except BaseException:
        output.close()
        raise


def _safe_path(data_dir: Path, path: Path) -> Path:
    root = data_dir.resolve(strict=True)
    relative = path.absolute().relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if current.is_symlink() or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("路径包含符号链接或重解析点")
    source = path.resolve(strict=True)
    source.relative_to(root)
    return source


def _safe_directory(data_dir: Path, path: Path) -> bool:
    try:
        return _safe_path(data_dir, path).is_dir()
    except (OSError, ValueError):
        return False


def _safe_media_path(data_dir: Path, path: Path) -> Path:
    source = _safe_path(data_dir, path)
    if not source.is_file() or source.stat().st_nlink != 1:
        raise ValueError("不是安全的普通文件")
    return source


def _hash_file(path: Path, check_cancel: Callable[[], None]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in _iter_chunks(handle, check_cancel):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(value).name).strip("._")
    return cleaned[:120] or "item"


def _scrub_text(value: str) -> str:
    return _SENSITIVE_TEXT.sub(r"\1\2[REDACTED]", value)


def _scrub_log(path: Path, check_cancel: Callable[[], None]) -> bytes:
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            check_cancel()
            lines.append(_scrub_text(line))
    return "".join(lines).encode("utf-8")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if isinstance(value, Path):
        return value.name
    raise TypeError(f"不能序列化：{type(value).__name__}")


def _safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key, item in value.items():
        if _SECRET_KEY.search(str(key)):
            continue
        clean[str(key)] = _safe_value(item)
    return clean


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _experiment_json(experiment: Experiment) -> dict[str, object]:
    return {
        "experiment_id": experiment.experiment_id,
        "experiment_type": experiment.experiment_type.value,
        "template_id": experiment.template_id,
        "instrument_no": experiment.instrument_no,
        "operator": experiment.operator,
        "created_at": experiment.created_at.isoformat(),
        "state": experiment.state.value,
        "settings": {
            "mvd": experiment.mvd,
            "endotoxin_limit": experiment.endotoxin_limit,
            "layout_confirmed": experiment.layout_confirmed,
        },
        "reagents": [_safe_mapping(asdict(batch)) for batch in experiment.reagents],
        "assignments": [
            {
                "tube_no": item.tube_no,
                "sample_id": item.sample.sample_id,
                "sample_name": _scrub_text(item.sample.name),
                "sample_batch_no": _scrub_text(item.sample.batch_no or ""),
                "role": item.role.value,
                "dilution_factor": item.dilution_factor,
                "spiked_endotoxin": item.spiked_endotoxin,
                "algorithm": _safe_mapping(asdict(experiment.results[item.tube_no]))
                if item.tube_no in experiment.results
                else None,
            }
            for item in experiment.assignments
        ],
    }


def _audit_json(record: object) -> dict[str, object]:
    event = record.event  # type: ignore[attr-defined]
    return {
        "seq": record.seq,  # type: ignore[attr-defined]
        "occurred_at": event.occurred_at.isoformat(),
        "actor": event.actor,
        "actor_role": event.actor_role.value if event.actor_role is not None else None,
        "action": event.action.value,
        "detail": _scrub_text(event.detail),
        "experiment_id": event.experiment_id,
        "prev_hash": record.prev_hash,  # type: ignore[attr-defined]
        "hash": record.hash,  # type: ignore[attr-defined]
    }


__all__ = ["DiagnosticCancelledError", "DiagnosticPackageResult", "DiagnosticsService"]
