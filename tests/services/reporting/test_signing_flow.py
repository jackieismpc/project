"""签署全流程：真实 SQLite、真实 Qt 排版、真实 pyHanko 签名。"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction, ReportScope
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.services.auth import InvalidCredentialsError, PermissionDeniedError
from gelinspect.services.reporting.report_service import SigningNotAllowedError
from gelinspect.services.reporting.texts import ReportLanguage
from gelinspect.services.reporting.verify import VerifyFailure, verify_pdf_bytes
from tests.services.conftest import NOW, PASSWORDS, Stack

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
        "© [版权方名称]，未经许可不得复制。",
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


def test_experimenter_and_admin_cannot_sign(signing_stack: Stack, tmp_path: Path) -> None:
    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    for username in ("experimenter", "admin"):
        with pytest.raises(PermissionDeniedError):
            service.sign(stack.login(username), "EXP-SAMPLE-0001", PASSWORDS[username])
    assert any("experiment_sign" in d for d in stack.audit(AuditAction.PERMISSION_DENIED))
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None and stored.state is ExperimentState.COMPLETED
    with pytest.raises(PermissionDeniedError):
        service.sign(None, "EXP-SAMPLE-0001", "x")


def test_signed_experiment_is_locked_and_the_same_file_can_be_exported_again(
    signing_stack: Stack, tmp_path: Path
) -> None:
    from gelinspect.domain.enums import Interpretation
    from gelinspect.services.errors import ServiceError, ServiceErrorCode
    from gelinspect.services.review_service import ReviewService

    stack = signing_stack
    make_completed(stack)
    service, _ = make_service(stack, tmp_path)
    analyst = stack.login("analyst")
    result = service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    # 已签署：复核被拒绝（服务层），状态不变
    with pytest.raises(ServiceError) as caught:
        ReviewService(stack.auth, stack.database, clock=lambda: NOW).review_tube(
            analyst, "EXP-SAMPLE-0001", 7, Interpretation.NEGATIVE, "想改"
        )
    assert caught.value.code is ServiceErrorCode.EXPERIMENT_SIGNED
    stored = stack.database.experiments.get("EXP-SAMPLE-0001")
    assert stored is not None
    assert stored.state is ExperimentState.SIGNED
    assert stored.results[7].final_interpretation is Interpretation.POSITIVE
    # 不能再签第二次
    with pytest.raises(SigningNotAllowedError) as blocked:
        service.sign(analyst, "EXP-SAMPLE-0001", PASSWORDS["analyst"])
    assert [b.blocker.value for b in blocked.value.blocks] == ["already_signed"]
    # 同一个文件可以反复导出，内容逐字节相同
    first = service.export_report_file(analyst, result.record.report_id, tmp_path / "out1.pdf")
    second = service.export_report_file(analyst, result.record.report_id, tmp_path / "out2.pdf")
    assert first.read_bytes() == second.read_bytes() == result.path.read_bytes()
    assert len(stack.audit(AuditAction.REPORT_EXPORTED)) == 2


def test_preview_of_a_signed_experiment_keeps_the_signing_language(
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
    assert preview.language is ReportLanguage.ZH_CN  # 已签署的以签署时的语言为准
    assert preview.record is not None and preview.document.signed
    assert preview.blocks and preview.blocks[0].blocker.value == "already_signed"


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
    assert stat.S_IMODE(keystore.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(keystore.key_path.parent.stat().st_mode) == 0o700
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
    # 在文件的不同位置各翻转一个字节：每一处都必须验签失败
    for offset in (20, 700, len(original) // 3, len(original) // 2, len(original) - 5000):
        broken = bytearray(original)
        broken[offset] ^= 0x01
        outcome = verify_pdf_bytes(bytes(broken), trusted, expected_sha256=result.record.sha256)
        assert not outcome.ok, offset
        assert VerifyFailure.FILE_HASH_MISMATCH in outcome.failures, offset
    # 只看 pyHanko 的签名校验（不借助库里的文件哈希）：改一个字节同样验不过
    for offset in (20, 700, len(original) // 3, len(original) // 2, len(original) - 5000):
        broken = bytearray(original)
        broken[offset] ^= 0x01
        by_signature_alone = verify_pdf_bytes(bytes(broken), trusted)
        assert not by_signature_alone.ok, offset
        assert not by_signature_alone.signature_intact, offset
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
