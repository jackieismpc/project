"""验签：用 pyHanko 校验 PDF 的数字签名，并核对文件哈希。

检查的是「防篡改」：

1. 签名完整：文件内容的摘要与签名里的一致（``intact``），签名本身有效（``valid``）。
2. 签名覆盖整个文件：签名之后没有被追加任何内容。
3. 证书是本机信任的那张：信任根只设为 ``data/keys/`` 里的自签证书（自签证书不走公共信任链），
   并且签名证书的指纹与它相同。
4. 文件的 SHA-256 与库里记录的一致（验证已入库的报告时）。

通过只说明文件自签署后没有被改动、且由本机的这把密钥签署，**不具有第三方 CA 的法律效力**。
任何一项不通过都给出明确原因，不会笼统地说「验签失败」。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from io import BytesIO

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator import ValidationContext

from gelinspect.services.reporting.keystore import certificate_fingerprint, certificate_subject
from gelinspect.services.reporting.pdf_signing import to_asn1

_log = logging.getLogger(__name__)


class VerifyFailure(StrEnum):
    """验签不通过的原因，界面据此给出当前语言的说明。"""

    FILE_MISSING = "file_missing"
    """文件不存在或读不了。"""
    FILE_HASH_MISMATCH = "file_hash_mismatch"
    """文件的 SHA-256 与库里记录的不一致：文件被替换或改动。"""
    UNREADABLE = "unreadable"
    """不是合法的 PDF，或结构已被破坏、无法解析。"""
    NOT_SIGNED = "not_signed"
    """没有数字签名。"""
    MULTIPLE_SIGNATURES = "multiple_signatures"
    """签名不止一个：本软件的报告只有一个签名。"""
    CONTENT_MODIFIED = "content_modified"
    """内容摘要与签名不符：文件被改动过。"""
    SIGNATURE_INVALID = "signature_invalid"
    """签名本身无效。"""
    NOT_ENTIRE_FILE = "not_entire_file"
    """签名没有覆盖整个文件：签署之后被追加过内容。"""
    UNTRUSTED_CERTIFICATE = "untrusted_certificate"
    """签名证书不是本机信任的那张。"""
    NO_LOCAL_CERTIFICATE = "no_local_certificate"
    """本机还没有签名证书，无法判断证书是否可信。"""


@dataclass(frozen=True, slots=True)
class SignatureVerification:
    """一次验签的结果。

    Attributes:
        ok: 全部检查通过。
        failures: 没有通过的检查；为空时 ``ok`` 为真。
        signature_intact: 签名完整有效且覆盖整个文件（不含证书和文件哈希）。
        file_sha256: 文件的 SHA-256。
        signer_subject: 签名证书的主题。
        certificate_sha256: 签名证书的 SHA-256 指纹。
        signed_at: 签名里的签署时间（签署人电脑的时钟）。
    """

    ok: bool
    failures: tuple[VerifyFailure, ...]
    signature_intact: bool
    file_sha256: str
    signer_subject: str | None = None
    certificate_sha256: str | None = None
    signed_at: datetime | None = None


def _failed(
    failures: list[VerifyFailure], file_sha256: str, *, intact: bool = False
) -> SignatureVerification:
    return SignatureVerification(
        ok=False,
        failures=tuple(failures),
        signature_intact=intact,
        file_sha256=file_sha256,
    )


def verify_pdf_bytes(
    pdf: bytes,
    trusted: x509.Certificate | None,
    *,
    expected_sha256: str | None = None,
) -> SignatureVerification:
    """校验 PDF 的数字签名。

    Args:
        pdf: 文件内容。
        trusted: 本机信任的自签证书，作为唯一的信任根；没有时无法判断证书是否可信。
        expected_sha256: 库里记录的文件 SHA-256；为 None 时不核对（验证外部文件）。

    Returns:
        验签结果，不抛异常：解析失败也表达为 ``UNREADABLE``。
    """
    file_sha = hashlib.sha256(pdf).hexdigest()
    failures: list[VerifyFailure] = []
    if expected_sha256 is not None and expected_sha256 != file_sha:
        failures.append(VerifyFailure.FILE_HASH_MISMATCH)
    try:
        reader = PdfFileReader(BytesIO(pdf))
        signatures = list(reader.embedded_regular_signatures)
    except Exception:
        _log.info("验签：PDF 无法解析", exc_info=True)
        return _failed([*failures, VerifyFailure.UNREADABLE], file_sha)
    if not signatures:
        return _failed([*failures, VerifyFailure.NOT_SIGNED], file_sha)
    if len(signatures) > 1:
        failures.append(VerifyFailure.MULTIPLE_SIGNATURES)
    embedded = signatures[0]
    context = ValidationContext(
        # 没有本机证书时信任根为空：不能退回去读系统信任库（慢，且在没有钥匙串的电脑上会报错）。
        trust_roots=[] if trusted is None else [to_asn1(_der(trusted))],
        allow_fetching=False,
    )
    try:
        status = validate_pdf_signature(embedded, context)
    except Exception:
        _log.info("验签：签名无法校验", exc_info=True)
        return _failed([*failures, VerifyFailure.UNREADABLE], file_sha)
    intact = bool(status.intact)
    valid = bool(status.valid)
    entire = status.coverage is SignatureCoverageLevel.ENTIRE_FILE
    if not intact:
        failures.append(VerifyFailure.CONTENT_MODIFIED)
    if not valid:
        failures.append(VerifyFailure.SIGNATURE_INVALID)
    if intact and valid and not entire:
        failures.append(VerifyFailure.NOT_ENTIRE_FILE)
    signer_cert = embedded.signer_cert
    fingerprint = hashlib.sha256(signer_cert.dump()).hexdigest()
    if trusted is None:
        failures.append(VerifyFailure.NO_LOCAL_CERTIFICATE)
    elif fingerprint != certificate_fingerprint(trusted) or (
        intact and valid and not status.trusted
    ):
        failures.append(VerifyFailure.UNTRUSTED_CERTIFICATE)
    subject = None
    with contextlib.suppress(ValueError):
        subject = certificate_subject(x509.load_der_x509_certificate(signer_cert.dump()))
    return SignatureVerification(
        ok=not failures,
        failures=tuple(failures),
        signature_intact=intact and valid and entire,
        file_sha256=file_sha,
        signer_subject=subject,
        certificate_sha256=fingerprint,
        signed_at=status.signer_reported_dt,
    )


def _der(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.DER)
