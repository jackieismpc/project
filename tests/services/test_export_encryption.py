"""敏感导出加密：Excel 和批量导出报告打成 AES-256 ZIP；默认不加密，设置可开且立即生效。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from gelinspect.domain.enums import AuditAction
from gelinspect.interfaces.storage import ExperimentQuery
from gelinspect.services.audit_service import AuditFilter
from gelinspect.services.encrypted_zip import read_encrypted_zip
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.export_service import ExportPolicy, ExportService
from tests.services.reporting.conftest import make_completed

from .conftest import NOW, Stack

PASSWORD = "Export#2026"


def encrypted_exports(stack: Stack) -> ExportService:
    return ExportService(stack.auth, stack.database, clock=lambda: NOW, policy=ExportPolicy(True))


def test_exports_are_plain_by_default(stack: Stack, tmp_path: Path) -> None:
    exports = ExportService(stack.auth, stack.database, clock=lambda: NOW)
    assert not exports.encryption_required
    make_completed(stack, experiment_id="EXP-1")
    summary = exports.export_results(stack.login("analyst"), ["EXP-1"], tmp_path / "r.xlsx")
    assert summary.path.suffix == ".xlsx" and not summary.encrypted


def test_encryption_required_refuses_exports_without_a_password(
    stack: Stack, tmp_path: Path
) -> None:
    exports = encrypted_exports(stack)
    make_completed(stack, experiment_id="EXP-1")
    analyst = stack.login("analyst")
    with pytest.raises(ServiceError) as missing:
        exports.export_results(analyst, ["EXP-1"], tmp_path / "r.xlsx")
    assert missing.value.code is ServiceErrorCode.EXPORT_PASSWORD_REQUIRED
    with pytest.raises(ServiceError) as weak:
        exports.export_history(analyst, ExperimentQuery(), tmp_path / "h.xlsx", password="short")
    assert weak.value.code is ServiceErrorCode.EXPORT_PASSWORD_WEAK
    assert not [p for p in tmp_path.iterdir() if p.suffix in (".xlsx", ".zip")]


@pytest.mark.parametrize("kind", ["results", "history", "audit"])
def test_encrypted_excel_round_trip_leaves_no_plaintext(
    stack: Stack, tmp_path: Path, kind: str
) -> None:
    exports = encrypted_exports(stack)
    make_completed(stack, experiment_id="EXP-1")
    admin_session = stack.login("admin")
    target = tmp_path / "out" / f"{kind}.xlsx"
    if kind == "results":
        summary = exports.export_results(admin_session, ["EXP-1"], target, password=PASSWORD)
    elif kind == "history":
        summary = exports.export_history(
            admin_session, ExperimentQuery(), target, password=PASSWORD
        )
    else:
        summary = exports.export_audit(admin_session, AuditFilter(), target, password=PASSWORD)
    assert summary.encrypted and summary.path == tmp_path / "out" / f"{kind}.zip"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [f"{kind}.zip"]
    content = read_encrypted_zip(summary.path, PASSWORD)
    assert list(content) == [f"{kind}.xlsx"]
    workbook = load_workbook(io.BytesIO(content[f"{kind}.xlsx"]), read_only=True)
    assert workbook.sheetnames
    details = stack.audit(AuditAction.DATA_EXPORTED)
    assert "已加密（AES-256 ZIP）" in details[-1] and PASSWORD not in details[-1]
