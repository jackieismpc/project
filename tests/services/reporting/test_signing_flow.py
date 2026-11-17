"""签署全流程：真实 SQLite、真实 Qt 排版、真实 pyHanko 签名。"""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader

from gelinspect.domain.enums import AuditAction, PermissionGroup, ReportScope
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import InvalidCredentialsError, PermissionDeniedError
from gelinspect.services.reporting.report_service import SigningNotAllowedError
from gelinspect.services.reporting.texts import ReportLanguage
from gelinspect.services.reporting.verify import VerifyFailure, verify_pdf_bytes
from tests.services.conftest import PASSWORDS, Stack

from .conftest import SIGN_TIME, make_completed, make_service
from .pdftext import flat_text, pdf_font_report


def test_sign_generates_a_valid_pades_pdf_and_records_everything(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, operator="zhang")
    service, keystore = make_service(stack, tmp_path)
    analyst = stack.login("analyst")

    result = service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])

    path = result.path
    assert path == tmp_path / "data" / "reports" / "EXP-SAMPLE-0001" / "EXP-SAMPLE-0001.pdf"
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    record = result.record
    assert record.sha256 == hashlib.sha256(data).hexdigest()
    assert record.path == "reports/EXP-SAMPLE-0001/EXP-SAMPLE-0001.pdf"
    assert record.signed and record.signed_by == "analyst" and record.signed_at == SIGN_TIME
    assert record.scope is ReportScope.EXPERIMENT
    assert stack.database.reports.get(record.report_id) == record
    # pyHanko 验签：完整、有效、覆盖整个文件、证书是本机信任的那张
    verification = verify_pdf_bytes(
        data, keystore.trusted_certificate(), expected_sha256=record.sha256
    )
    assert verification.ok and verification.signature_intact
    assert verification.certificate_sha256 == record.certificate_sha256
    imported = service.verify_imported_file(analyst, path)
    assert imported.signature_status == "valid"
    assert imported.matched_report_id == record.report_id
    assert imported.experiment_id == record.experiment_id
    assert imported.hash_match
    embedded = list(PdfFileReader(BytesIO(data)).embedded_regular_signatures)[0]
    reason_obj = embedded.sig_object.get("/Reason")
    assert f"GI-REPORT:{record.report_id}:{record.experiment_id}:{record.content_sha256}" in str(
        reason_obj
    )
    raw_reason = reason_obj.original_bytes
    escaped_reason = b"".join(
        bytes([value])
        if bytes([value]).isalnum() or value == 0x20
        else f"\\{value:03o}".encode("ascii")
        for value in raw_reason
    )
    reason_offset = data.index(escaped_reason) + len(escaped_reason) - 1
    tampered = bytearray(data)
    tampered[reason_offset] = ord("0") if tampered[reason_offset] != ord("0") else ord("1")
    tampered_path = tmp_path / "tampered-reason.pdf"
    tampered_path.write_bytes(tampered)
    rejected = service.verify_imported_file(analyst, tampered_path)
    assert rejected.signature_status == "invalid"
    assert rejected.matched_report_id is None and rejected.experiment_id is None
    # 状态、签署人和时间、审计在同一个工作单元里生效
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None
    assert stored.state is ExperimentState.SIGNED
    assert stored.signed_by == "analyst" and stored.signed_at == SIGN_TIME
    signed_audit = stack.audit(AuditAction.EXPERIMENT_SIGNED)
    assert len(signed_audit) == 1
    assert record.sha256 in signed_audit[0] and "-----" not in signed_audit[0]
    assert not list(path.parent.glob(".*.part"))


def test_pdf_has_chinese_text_key_fields_and_embedded_bundled_font(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, operator="zhang")
    service, _ = make_service(stack, tmp_path)
    result = service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    data = result.path.read_bytes()
    text = flat_text(data)
    for expected in (
        "自动凝胶检测报告",
        "文件名（试验编号）",
        "EXP-SAMPLE-0001",
        "GI-001",
        "张工",  # 操作人
        "李工",  # 签署人
        "2026-11-05 13:30",  # 签署日期
        "LAL2606A",
        "CSE2605A",
        "WAT2607A",
        "0.5 EU/mL",
        "试管编号",
        "样品名称",
        "序列号",
        "样品批号",
        "稀释倍数",
        "外加内毒素",
        "阳性（已复核）",
        "结论：合格",
        "λc = 0.5946",
        "sensitivity-v1",
        "本报告已使用数字签名，任何修改都会导致验签失败。",
        "© 厦门鲎试剂生物科技股份有限公司，未经许可不得复制。",
        "SHA-256",
        "第 1 页 / 共 1 页",
    ):
        assert expected in text, expected
    assert "�" not in text  # 没有缺字
    # 中文字体是内置的 Noto Sans SC，并且嵌入了字体文件
    fonts = pdf_font_report(data)
    assert any(name.endswith("NotoSansSC-Regular") or "NotoSansSC" in name for name, _ in fonts)
    assert all(embedded for _, embedded in fonts)


def test_temperature_range_is_taken_from_the_real_log(signing_stack: Stack, tmp_path: Path) -> None:
    from datetime import timedelta

    from gelinspect.interfaces.storage import TemperatureSample

    stack = signing_stack
    make_completed(stack)
    base = SIGN_TIME - timedelta(hours=1)
    stack.database.telemetry.add_temperatures(
        [
            TemperatureSample(base + timedelta(minutes=i), "incubator", c, "EXP-SAMPLE-0001")
            for i, c in enumerate((36.9, 37.04, 37.2, 37.1))
        ]
    )
    service, _ = make_service(stack, tmp_path)
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    assert preview.document.sections[0].temperature_range == (36.9, 37.2)
    result = service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert "36.9–37.2" in flat_text(result.path.read_bytes())


def test_no_temperature_log_leaves_the_range_blank(signing_stack: Stack, tmp_path: Path) -> None:
    make_completed(signing_stack)
    service, _ = make_service(signing_stack, tmp_path)
    preview = service.preview(signing_stack.login("analyst"), "EXP-SAMPLE-0001")
    assert preview.document.sections[0].temperature_range is None


def test_wrong_password_cannot_sign_and_leaves_no_trace(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    with pytest.raises(InvalidCredentialsError):
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", "wrong-password")
    assert not (tmp_path / "data" / "reports").exists()
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    assert stack.database.reports.list_for_experiment("EXP-SAMPLE-0001") == []
    assert any("密码错误" in d for d in stack.audit(AuditAction.LOGIN_FAILED))


def test_read_only_user_cannot_sign(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    admin = stack.login("admin")
    experimenter = stack.database.users.get_by_username("experimenter")
    assert experimenter is not None
    from gelinspect.services.user_service import UserService

    UserService(stack.auth, stack.database).set_permission_groups(
        admin, experimenter.user_id, frozenset({PermissionGroup.VIEW})
    )
    with pytest.raises(PermissionDeniedError):
        service.sign(stack.login("experimenter"), "EXP-SAMPLE-0001", PASSWORDS["experimenter"])
    assert any("experiment_sign" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    with pytest.raises(PermissionDeniedError):
        service.sign(None, "EXP-SAMPLE-0001", "x")


def test_experiment_group_user_can_review_then_sign(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack, reviewer="experimenter")
    service, _ = make_service(stack, tmp_path)
    experimenter = stack.login("experimenter")
    assert experimenter.can(Permission.RESULT_REVIEW)
    assert experimenter.can(Permission.EXPERIMENT_SIGN)
    result = service.sign(experimenter, "EXP-SAMPLE-0001", PASSWORDS["experimenter"])
    assert result.record.signed_by == "experimenter"
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "experimenter-print.pdf"))
    preview = service.preview(experimenter, "EXP-SAMPLE-0001")
    with pytest.raises(PermissionDeniedError):
        service.paint(experimenter, preview, printer)
    with pytest.raises(PermissionDeniedError):
        service.export_report_file(experimenter, result.record.report_id, tmp_path / "export.pdf")


def test_read_only_user_cannot_paint_to_pdf_printer(signing_stack: Stack, tmp_path: Path) -> None:
    from PySide6.QtPrintSupport import QPrinter

    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    admin = stack.login("admin")
    experimenter = stack.database.users.get_by_username("experimenter")
    assert experimenter is not None
    from gelinspect.services.user_service import UserService

    UserService(stack.auth, stack.database).set_permission_groups(
        admin, experimenter.user_id, frozenset({PermissionGroup.VIEW})
    )
    reader = stack.login("experimenter")
    preview = service.preview(reader, "EXP-SAMPLE-0001")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    output = tmp_path / "unauthorized-service-output.pdf"
    printer.setOutputFileName(str(output))
    with pytest.raises(PermissionDeniedError):
        service.paint(reader, preview, printer)
    assert not output.exists()


def test_signed_experiment_can_be_re_reviewed_and_revision_keeps_original_file(
    signing_stack: Stack, tmp_path: Path
) -> None:
    from gelinspect.domain.enums import Interpretation
    from gelinspect.services.review_service import ReviewService

    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    result = service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    ReviewService(
        stack.auth, stack.database, clock=lambda: SIGN_TIME + timedelta(days=1)
    ).review_tube(analyst, "EXP-SAMPLE-0001", 7, Interpretation.NEGATIVE, "复核后更正")
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None
    assert stored.state is ExperimentState.SIGNED
    assert stored.results[7].final_interpretation is Interpretation.NEGATIVE
    with pytest.raises(SigningNotAllowedError):
        service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    revised_service, _ = make_service(stack, tmp_path, clock=lambda: SIGN_TIME + timedelta(days=2))
    revised = revised_service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert revised.record.revision == 2
    assert revised.record.supersedes_report_id == result.record.report_id
    assert result.path.exists() and result.path.read_bytes().startswith(b"%PDF")
    assert revised.path != result.path and revised.path.exists()
    # 同一个文件可以反复导出，内容逐字节相同
    first = revised_service.export_report_file(
        analyst, revised.record.report_id, tmp_path / "out1.pdf"
    )
    second = revised_service.export_report_file(
        analyst, revised.record.report_id, tmp_path / "out2.pdf"
    )
    assert first.read_bytes() == second.read_bytes() == revised.path.read_bytes()
    assert len(stack.audit(AuditAction.REPORT_EXPORTED)) == 2


def test_preview_uses_the_client_language_and_can_create_another_signed_language_version(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    assert service.preview(analyst, "EXP-SAMPLE-0001", ReportLanguage.EN).language is (
        ReportLanguage.EN
    )
    service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"], ReportLanguage.ZH_CN)
    preview = service.preview(analyst, "EXP-SAMPLE-0001", ReportLanguage.EN)
    assert preview.language is ReportLanguage.EN
    assert preview.record is not None and preview.document.signed
    assert preview.can_sign
    other_language, _ = make_service(
        stack, tmp_path, clock=lambda: SIGN_TIME + timedelta(days=1)
    )
    revised = other_language.sign(
        analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"], ReportLanguage.EN
    )
    assert revised.record.revision == 2 and revised.record.language == ReportLanguage.EN.value
    assert stack.database.reports.get(preview.record.report_id) == preview.record


def test_key_file_is_private_and_nothing_secret_is_logged_or_audited(
    signing_stack: Stack, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    stack = signing_stack
    make_completed(stack)
    service, keystore = make_service(stack, tmp_path)
    with caplog.at_level(logging.DEBUG):
        service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    key_pem = keystore.key_path.read_bytes()
    body = key_pem.decode().split("\n")[1]
    assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    if os.name == "posix":
        assert stat.S_IMODE(keystore.key_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(keystore.key_path.parent.stat().st_mode) == 0o700
    else:
        from gelinspect.infrastructure.security.protectors import DpapiKeyProtector

        protected = DpapiKeyProtector().protect(key_pem)
        assert key_pem not in protected
        assert DpapiKeyProtector().unprotect(protected) == key_pem
    assert body not in caplog.text
    assert "PRIVATE KEY" not in caplog.text
    everything = "\n".join(stack.audit(a) and "\n".join(stack.audit(a)) or "" for a in AuditAction)
    assert body not in everything and "PRIVATE KEY" not in everything


def test_tampering_with_one_byte_fails_verification_with_a_clear_reason(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, keystore = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    result = service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert service.verify_report(analyst, result.record.report_id).ok

    original = result.path.read_bytes()
    trusted = keystore.trusted_certificate()
    # ByteRange 指定的PDF内容都受签名保护；签名值的预留填充不在摘要范围内。
    from io import BytesIO

    from pyhanko.pdf_utils.reader import PdfFileReader

    embedded = list(PdfFileReader(BytesIO(original)).embedded_regular_signatures)[0]
    start1, length1, start2, length2 = map(int, embedded.sig_object.get("/ByteRange"))
    signed_offsets = (start1 + 20, start1 + 700, start2 + 100, start2 + length2 // 2)
    for offset in (*signed_offsets, len(original) - 5000):
        broken = bytearray(original)
        broken[offset] ^= 0x01
        outcome = verify_pdf_bytes(bytes(broken), trusted, expected_sha256=result.record.sha256)
        assert not outcome.ok, offset
        assert VerifyFailure.FILE_HASH_MISMATCH in outcome.failures, offset
    # 不依赖整文件 SHA-256，改动签名覆盖的任意内容都应失败。
    for offset in signed_offsets:
        broken = bytearray(original)
        broken[offset] ^= 0x01
        by_signature_alone = verify_pdf_bytes(bytes(broken), trusted)
        assert not by_signature_alone.ok, offset
        assert not by_signature_alone.signature_intact, offset
    # 预留的签名容器填充不在PDF ByteRange内；只有数据库整文件哈希能发现其改变。
    padding = bytearray(original)
    padding[len(original) - 5000] ^= 0x01
    assert verify_pdf_bytes(bytes(padding), trusted).ok
    assert not verify_pdf_bytes(bytes(padding), trusted, expected_sha256=result.record.sha256).ok
    # 签名本身（/Contents 里的十六进制）被改一个字符
    start = original.rindex(b"/Contents <") + len(b"/Contents <") + 20
    broken = bytearray(original)
    broken[start] = ord("0") if broken[start] != ord("0") else ord("1")
    forged = verify_pdf_bytes(bytes(broken), trusted)
    assert not forged.ok and not forged.signature_intact
    # 通过库里入口重新验签：文件被改一个字节，原因明确
    broken = bytearray(original)
    broken[len(original) // 2] ^= 0x01
    result.path.write_bytes(bytes(broken))
    tampered = service.verify_report(analyst, result.record.report_id)
    assert not tampered.ok
    assert VerifyFailure.FILE_HASH_MISMATCH in tampered.failures
    assert VerifyFailure.CONTENT_MODIFIED in tampered.failures or (
        VerifyFailure.UNREADABLE in tampered.failures
    )
    assert any("未通过" in d for d in stack.audit(AuditAction.REPORT_VERIFIED))


def test_appended_data_is_detected_as_not_covering_the_whole_file(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, keystore = make_service(stack, tmp_path)
    result = service.sign(stack.login("analyst"), "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    outcome = verify_pdf_bytes(
        result.path.read_bytes() + b"\n%% appended after signing\n", keystore.trusted_certificate()
    )
    assert not outcome.ok
    assert VerifyFailure.NOT_ENTIRE_FILE in outcome.failures or (
        VerifyFailure.UNREADABLE in outcome.failures
    )


def test_replacing_the_file_with_another_valid_signed_pdf_is_detected(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack, experiment_id="EXP-A")
    make_completed(stack, experiment_id="EXP-B")
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    first = service.sign(analyst, "EXP-A", PASSWORDS["analyst"])
    second = service.sign(analyst, "EXP-B", PASSWORDS["analyst"])
    # 用 B 的合法签名文件替换 A：签名本身有效，但与库里记录的 SHA-256 不符
    first.path.write_bytes(second.path.read_bytes())
    outcome = service.verify_report(analyst, first.record.report_id)
    assert not outcome.ok
    assert outcome.failures == (VerifyFailure.FILE_HASH_MISMATCH,)
    assert outcome.signature_intact  # 签名完整，但文件不是当初入库的那份


def test_missing_file_and_a_foreign_certificate_are_reported(
    signing_stack: Stack, tmp_path: Path
) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    result = service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    # 另一台电脑（另一个密钥库）签的文件：签名完整，但证书不是本机信任的那张
    other_service, other_keys = make_service(stack, tmp_path / "other")
    foreign = other_service.sign  # noqa: F841  # 只为说明场景：下面直接用同一个 PDF 的另一份签名
    from gelinspect.services.reporting.pdf_signing import sign_pdf_bytes

    identity = other_keys.load_or_create()
    unsigned_pdf = _unsigned_pdf(stack, tmp_path)
    signed_elsewhere = sign_pdf_bytes(unsigned_pdf, identity, signer_name="外人", reason="x")
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(signed_elsewhere)
    outcome = service.verify_file(analyst, outside)
    assert not outcome.ok and outcome.signature_intact
    assert outcome.failures == (VerifyFailure.UNTRUSTED_CERTIFICATE,)
    # 本机签的外部文件可以通过
    assert service.verify_file(analyst, result.path).ok
    # 文件被删
    result.path.unlink()
    assert service.verify_report(analyst, result.record.report_id).failures == (
        VerifyFailure.FILE_MISSING,
    )
    # 没有签名的 PDF
    plain = tmp_path / "plain.pdf"
    plain.write_bytes(unsigned_pdf)
    assert service.verify_file(analyst, plain).failures == (VerifyFailure.NOT_SIGNED,)
    # 根本不是 PDF
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert VerifyFailure.UNREADABLE in service.verify_file(analyst, junk).failures


def _unsigned_pdf(stack: Stack, tmp_path: Path) -> bytes:
    from gelinspect.services.reporting.texts import ReportTexts

    service, _ = make_service(stack, tmp_path / "unsigned")
    preview = service.preview(stack.login("analyst"), "EXP-SAMPLE-0001")
    data, _ = service._renderer.render_pdf(preview.document, ReportTexts(ReportLanguage.ZH_CN))
    return data
