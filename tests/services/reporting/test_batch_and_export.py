"""单批次报告和批量导出。"""

from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from gelinspect.domain.enums import (
    AuditAction,
    BatchKind,
    ReportScope,
)
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import ReportRecord
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.reporting.report_service import ExportResult, ReportService
from gelinspect.services.reporting.verify import verify_pdf_bytes
from tests.services.conftest import PASSWORDS, Stack

from .conftest import SIGN_TIME, make_completed, make_service
from .pdftext import flat_text, has_text


def sign_one(
    stack: Stack, service: ReportService, experiment_id: str, **kwargs: object
) -> ReportRecord:
    make_completed(stack, experiment_id=experiment_id, **kwargs)  # type: ignore[arg-type]
    return service.sign(stack.login("analyst"), experiment_id, PASSWORDS["analyst"]).record


# ---------------------------------------------------------------------------- 单批次报告


def test_batch_report_summarises_experiments_of_the_same_lal_batch_and_is_signed(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, keystore = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    sign_one(stack, service, "EXP-A", sample_batch="SB-1")
    sign_one(stack, service, "EXP-B", sample_batch="SB-2")

    assert service.batch_numbers(analyst, BatchKind.LAL_REAGENT) == ["LAL2606A"]
    assert service.batch_numbers(analyst, BatchKind.SAMPLE) == ["SB-1", "SB-2"]
    preview = service.batch_preview(analyst, BatchKind.LAL_REAGENT, "LAL2606A")
    assert preview.scope is ReportScope.BATCH and preview.can_sign
    assert [s.experiment_id for s in preview.document.sections] == ["EXP-A", "EXP-B"]

    signed = service.sign_batch(analyst, BatchKind.LAL_REAGENT, "LAL2606A", PASSWORDS["analyst"])
    record = signed.record
    assert record.scope is ReportScope.BATCH and record.experiment_id is None
    assert record.batch_kind is BatchKind.LAL_REAGENT and record.batch_no == "LAL2606A"
    assert record.experiment_ids == ("EXP-A", "EXP-B")
    assert "reports/_batch/" in record.path
    data = signed.path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == record.sha256
    assert verify_pdf_bytes(data, keystore.trusted_certificate(), expected_sha256=record.sha256).ok
    for expected in ("单批次汇总报告", "LAL2606A", "共 2 个试验", "EXP-A", "EXP-B", "李工"):
        assert has_text(data, expected), expected
    assert flat_text(data).count("文件名（试验编号）") == 2  # 每个试验一节
    assert stack.database.reports.list_for_batch(BatchKind.LAL_REAGENT, "LAL2606A") == [record]
    assert service.batch_reports(analyst, BatchKind.LAL_REAGENT, "LAL2606A") == [record]
    assert any("单批次" in d for d in stack.audit(AuditAction.REPORT_SIGNED))
    # 单批次报告不改变任何试验的状态
    for experiment_id in ("EXP-A", "EXP-B"):
        stored = stack.database.experiments.get(experiment_id)
        assert stored is not None and stored.state is ExperimentState.SIGNED


def test_batch_report_by_sample_batch_only_includes_that_batch(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    sign_one(stack, service, "EXP-A", sample_batch="SB-1")
    sign_one(stack, service, "EXP-B", sample_batch="SB-2")
    preview = service.batch_preview(analyst, BatchKind.SAMPLE, "SB-2")
    assert [s.experiment_id for s in preview.document.sections] == ["EXP-B"]


def test_batch_cannot_be_signed_while_an_experiment_in_it_is_unsigned(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    sign_one(stack, service, "EXP-A")
    make_completed(stack, experiment_id="EXP-B")  # 已完成，还没签
    preview = service.batch_preview(analyst, BatchKind.LAL_REAGENT, "LAL2606A")
    assert not preview.can_sign  # 预览和打印仍然可以
    assert len(preview.document.sections) == 2
    with pytest.raises(ServiceError) as caught:
        service.sign_batch(analyst, BatchKind.LAL_REAGENT, "LAL2606A", PASSWORDS["analyst"])
    assert caught.value.code is ServiceErrorCode.BATCH_NOT_SIGNABLE
    assert stack.database.reports.list_for_batch(BatchKind.LAL_REAGENT, "LAL2606A") == []


def test_batch_with_no_completed_experiment_is_an_empty_state(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    assert service.batch_numbers(analyst, BatchKind.LAL_REAGENT) == []
    for call in (
        lambda: service.batch_preview(analyst, BatchKind.LAL_REAGENT, "NOPE"),
        lambda: service.sign_batch(analyst, BatchKind.LAL_REAGENT, "NOPE", PASSWORDS["analyst"]),
    ):
        with pytest.raises(ServiceError) as caught:
            call()
        assert caught.value.code is ServiceErrorCode.BATCH_EMPTY


def test_batch_permissions_and_password(signing_stack: Stack, tmp_path: Path) -> None:
    from gelinspect.services.auth import InvalidCredentialsError

    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    sign_one(stack, service, "EXP-A")
    experimenter = stack.login("experimenter")
    # 实验员可以预览，不能签署
    service.batch_preview(experimenter, BatchKind.LAL_REAGENT, "LAL2606A")
    with pytest.raises(PermissionDeniedError):
        service.sign_batch(
            experimenter, BatchKind.LAL_REAGENT, "LAL2606A", PASSWORDS["experimenter"]
        )
    with pytest.raises(InvalidCredentialsError):
        service.sign_batch(
            stack.login("analyst"), BatchKind.LAL_REAGENT, "LAL2606A", "wrong-password"
        )
    assert stack.database.reports.list_for_batch(BatchKind.LAL_REAGENT, "LAL2606A") == []


def test_unusual_batch_numbers_cannot_escape_the_reports_directory(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    sign_one(stack, service, "EXP-A", sample_batch="../../etc/批 号")
    signed = service.sign_batch(
        stack.login("analyst"),
        BatchKind.SAMPLE,
        "../../etc/批 号",
        PASSWORDS["analyst"],
    )
    reports = (tmp_path / "data" / "reports").resolve()
    assert signed.path.resolve().is_relative_to(reports)


# ---------------------------------------------------------------------------- 导出


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_batch_export_copies_signed_files_and_writes_a_manifest(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    records = [sign_one(stack, service, f"EXP-{n}") for n in "ABC"]
    result = service.export_reports(analyst, [r.report_id for r in records], tmp_path / "out")
    assert isinstance(result, ExportResult)
    assert result.exported == 3 and result.failures == ()
    for record in records:
        copied = tmp_path / "out" / f"{record.experiment_id}.pdf"
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == record.sha256
    rows = read_manifest(result.manifest)
    assert [r["status"] for r in rows] == ["ok"] * 3
    assert {r["experiment_id"] for r in rows} == {"EXP-A", "EXP-B", "EXP-C"}
    assert rows[0]["sha256"] == records[0].sha256 and rows[0]["signed_by"] == "analyst"
    assert any("批量导出" in d for d in stack.audit(AuditAction.REPORT_EXPORTED))


def test_export_refuses_a_tampered_or_missing_file_and_says_why(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    good, tampered, gone = (sign_one(stack, service, f"EXP-{n}") for n in "ABC")
    path = service.resolve(tampered)
    path.write_bytes(path.read_bytes() + b"x")
    service.resolve(gone).unlink()
    result = service.export_reports(
        analyst, [good.report_id, tampered.report_id, gone.report_id, "no-such-id"], tmp_path / "o"
    )
    assert result.exported == 1
    reasons = {f.report_id: f.reason for f in result.failures}
    assert reasons == {
        tampered.report_id: "hash_mismatch",
        gone.report_id: "missing",
        "no-such-id": "not_found",
    }
    assert not (tmp_path / "o" / "EXP-B.pdf").exists()  # 被改动的文件不导出
    statuses = {r["report_id"]: r["status"] for r in read_manifest(result.manifest)}
    assert statuses[tampered.report_id] == "hash_mismatch"
    # 单个导出也拒绝被改动的文件
    with pytest.raises(ServiceError) as caught:
        service.export_report_file(analyst, tampered.report_id, tmp_path / "x.pdf")
    assert caught.value.code is ServiceErrorCode.REPORT_HASH_MISMATCH


def test_export_needs_the_export_permission_and_a_selection(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    record = sign_one(stack, service, "EXP-A")
    with pytest.raises(PermissionDeniedError):
        service.export_reports(stack.login("experimenter"), [record.report_id], tmp_path / "o")
    with pytest.raises(PermissionDeniedError):
        service.export_report_file(stack.login("experimenter"), record.report_id, tmp_path / "x")
    assert not (tmp_path / "o").exists()
    # 管理员可以导出（data_export），但不能签署
    service.export_reports(stack.login("admin"), [record.report_id], tmp_path / "o")
    with pytest.raises(ServiceError) as caught:
        service.export_reports(stack.login("analyst"), [], tmp_path / "empty")
    assert caught.value.code is ServiceErrorCode.EXPORT_EMPTY


def test_export_twice_to_the_same_directory_keeps_both_manifests(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    record = sign_one(stack, service, "EXP-A")
    first = service.export_reports(analyst, [record.report_id], tmp_path / "o")
    second = service.export_reports(analyst, [record.report_id], tmp_path / "o")
    assert first.manifest != second.manifest and first.manifest.is_file()


# ---------------------------------------------------------------------------- 性能


@pytest.mark.perf
def test_exporting_100_signed_reports_takes_under_10_seconds(
    signing_stack: Stack, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """指标：批量导出 <10 秒。100 份各有试验和报告记录，文件是一份真实签名 PDF 的副本。"""
    stack = signing_stack
    service, _ = make_service(stack, tmp_path)
    base = sign_one(stack, service, "EXP-BASE")
    source = service.resolve(base)
    original = stack.database.experiments.get("EXP-BASE")
    assert original is not None
    ids: list[str] = []
    for n in range(100):
        experiment_id = f"EXP-{n:04d}"
        copy = original
        copy.experiment_id = experiment_id
        copy.state = ExperimentState.SIGNED
        stack.database.experiments.save(copy)
        folder = tmp_path / "data" / "reports" / experiment_id
        folder.mkdir(parents=True)
        shutil.copyfile(source, folder / f"{experiment_id}.pdf")
        record = ReportRecord(
            report_id=f"r{n:04d}",
            experiment_id=experiment_id,
            path=f"reports/{experiment_id}/{experiment_id}.pdf",
            sha256=base.sha256,
            created_by="analyst",
            created_at=SIGN_TIME + timedelta(seconds=n),
            signed=True,
            signed_by="analyst",
            signed_at=SIGN_TIME,
            experiment_ids=(experiment_id,),
        )
        stack.database.reports.add(record)
        ids.append(record.report_id)
    result = service.export_reports(stack.login("analyst"), ids, tmp_path / "export")
    assert result.exported == 100 and result.failures == ()
    assert len(list((tmp_path / "export").glob("*.pdf"))) == 100
    size_kb = source.stat().st_size / 1024
    with capsys.disabled():
        print(f"\n批量导出 100 份已签署报告（每份约 {size_kb:.0f} KB）：{result.elapsed_s:.3f} 秒")
    assert result.elapsed_s < 10.0
