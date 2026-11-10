"""签名证书与私钥：本地自签、权限、配置、预留的加密接口。"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from gelinspect.services.config import DEFAULT_CONFIG, AppConfig, ReportSection, parse_config
from gelinspect.services.reporting.keystore import (
    KeyStoreError,
    LocalKeyStore,
    certificate_fingerprint,
)

ROOT = Path(__file__).resolve().parents[3]


def make_store(directory: Path, **kwargs: object) -> LocalKeyStore:
    options: dict[str, object] = {"common_name": "测试签名", "organization": "测试机构"}
    options.update(kwargs)
    return LocalKeyStore(directory, **options)  # type: ignore[arg-type]


def test_first_use_generates_a_self_signed_certificate_from_configuration(tmp_path: Path) -> None:
    store = make_store(tmp_path / "keys", common_name="我方签名证书", organization="某某公司")
    assert store.trusted_certificate() is None  # 只读，不会生成
    assert not (tmp_path / "keys").exists()
    identity = store.load_or_create()
    certificate = identity.certificate
    assert certificate.subject == certificate.issuer  # 自签
    assert (
        certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "我方签名证书"
    )
    assert (
        certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "某某公司"
    )
    assert identity.subject == "我方签名证书 (某某公司)"
    assert isinstance(identity.private_key, ec.EllipticCurvePrivateKey)
    span = certificate.not_valid_after_utc - certificate.not_valid_before_utc
    assert timedelta(days=3649) < span < timedelta(days=3651)
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and usage.content_commitment and not usage.key_cert_sign
    assert store.trusted_certificate() == certificate


def test_second_use_loads_the_same_identity(tmp_path: Path) -> None:
    first = make_store(tmp_path / "keys").load_or_create()
    second = make_store(tmp_path / "keys").load_or_create()
    assert first.fingerprint == second.fingerprint == certificate_fingerprint(first.certificate)


def test_rsa_is_available(tmp_path: Path) -> None:
    identity = make_store(tmp_path / "keys", algorithm="rsa").load_or_create()
    assert isinstance(identity.private_key, rsa.RSAPrivateKey)
    assert identity.private_key.key_size == 3072


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 POSIX 权限位")
def test_private_key_files_are_owner_only_and_loose_permissions_are_tightened(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "keys")
    store.load_or_create()
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.certificate_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.key_path.parent.stat().st_mode) == 0o700
    os.chmod(store.key_path, 0o644)  # 被人改宽了
    store.load_or_create()
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert not list(store.key_path.parent.glob("*.tmp"))


def test_missing_certificate_is_reissued_for_the_same_key(tmp_path: Path) -> None:
    store = make_store(tmp_path / "keys")
    first = store.load_or_create()
    store.certificate_path.unlink()
    second = store.load_or_create()
    assert first.private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == second.private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert store.certificate_path.is_file()


def test_certificate_without_a_key_is_an_error_not_a_silent_regeneration(tmp_path: Path) -> None:
    store = make_store(tmp_path / "keys")
    store.load_or_create()
    store.key_path.unlink()
    with pytest.raises(KeyStoreError) as caught:
        store.load_or_create()
    assert "缺少私钥" in str(caught.value)
    assert store.trusted_certificate() is not None  # 验签仍然可用


def test_mismatched_or_corrupt_files_are_reported_without_leaking_the_key(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "keys")
    store.load_or_create()
    other = make_store(tmp_path / "other")
    other.load_or_create()
    original = store.certificate_path.read_bytes()
    store.certificate_path.write_bytes(other.certificate_path.read_bytes())
    with pytest.raises(KeyStoreError, match="不成对"):
        store.load_or_create()
    store.certificate_path.write_bytes(original)
    secret = store.key_path.read_bytes()
    store.key_path.write_bytes(secret[:60] + b"\x00garbage" + secret[70:])
    with pytest.raises(KeyStoreError) as caught:
        store.load_or_create()
    assert secret.split(b"\n")[1].decode() not in str(caught.value)


def test_a_key_protector_can_be_plugged_in_for_p13(tmp_path: Path) -> None:
    """P13 提供加密实现后注入即可：磁盘上不再是明文 PEM，调用方不用改。"""

    class Scramble:
        def protect(self, key_pem: bytes) -> bytes:
            return b"ENC1:" + base64.b64encode(key_pem[::-1])

        def unprotect(self, data: bytes) -> bytes:
            assert data.startswith(b"ENC1:")
            return base64.b64decode(data[5:])[::-1]

    store = make_store(tmp_path / "keys", protector=Scramble())
    first = store.load_or_create()
    assert store.key_path.read_bytes().startswith(b"ENC1:")
    assert b"PRIVATE KEY" not in store.key_path.read_bytes()
    assert make_store(tmp_path / "keys", protector=Scramble()).load_or_create().fingerprint == (
        first.fingerprint
    )


def test_identity_repr_never_shows_the_private_key(tmp_path: Path) -> None:
    identity = make_store(tmp_path / "keys").load_or_create()
    assert "private_key" not in repr(identity)
    assert "PRIVATE" not in repr(identity)


def test_certificate_validity_is_issued_from_the_real_clock_and_configurable(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    identity = make_store(tmp_path / "keys", valid_days=30).load_or_create()
    assert now - timedelta(minutes=10) < identity.certificate.not_valid_before_utc <= now
    span = identity.certificate.not_valid_after_utc - identity.certificate.not_valid_before_utc
    assert timedelta(days=29) < span < timedelta(days=31)


# ---------------------------------------------------------------------------- 配置


def test_report_configuration_defaults_and_validation() -> None:
    section = ReportSection()
    assert section.copyright_holder == "厦门鲎试剂生物科技股份有限公司"
    assert section.key_algorithm == "ecdsa" and section.include_images is False
    config = parse_config(DEFAULT_CONFIG, base_dir=ROOT)
    assert config.keys_dir == config.data_dir / "keys"
    assert config.reports_dir == config.data_dir / "reports"
    custom = parse_config(
        {
            **DEFAULT_CONFIG,
            "reports": {
                "copyright_holder": "某某公司",
                "certificate_common_name": "某某签名",
                "key_algorithm": "rsa",
            },
        },
        base_dir=ROOT,
    )
    assert isinstance(custom, AppConfig)
    assert custom.reports.copyright_holder == "某某公司"
    with pytest.raises(Exception, match="key_algorithm|reports"):
        parse_config({**DEFAULT_CONFIG, "reports": {"key_algorithm": "dsa"}}, base_dir=ROOT)
    with pytest.raises(Exception, match="reports|extra"):
        parse_config({**DEFAULT_CONFIG, "reports": {"allow_sign_invalid": True}}, base_dir=ROOT)


def test_key_and_data_files_are_ignored_by_git() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("*.pem", "*.key", "*.p12", "/data/"):
        assert pattern in ignore


# ---------------------------------------------------------------------------- 无窗口


def test_service_layer_renders_and_signs_without_any_window(tmp_path: Path) -> None:
    """没有 QApplication 的进程里：自动用 offscreen 的 QGuiApplication，照样生成并签名 PDF。"""
    code = f"""
import sys
from datetime import UTC
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from PySide6.QtGui import QGuiApplication
assert QGuiApplication.instance() is None
from tests.services.reporting.test_rendering import big_experiment, document_of
from gelinspect.services.reporting.keystore import LocalKeyStore
from gelinspect.services.reporting.pdf_signing import sign_pdf_bytes
from gelinspect.services.reporting.renderer import ReportRenderer
from gelinspect.services.reporting.texts import ReportLanguage, ReportTexts
from gelinspect.services.reporting.verify import verify_pdf_bytes
pdf, pages = ReportRenderer(zone=UTC).render_pdf(
    document_of(big_experiment(10)), ReportTexts(ReportLanguage.ZH_CN))
store = LocalKeyStore(Path({str(tmp_path / "keys")!r}), common_name="c", organization="o")
signed = sign_pdf_bytes(pdf, store.load_or_create(), signer_name="n", reason="r")
assert verify_pdf_bytes(signed, store.trusted_certificate()).ok
print("OK", pages, QGuiApplication.instance() is not None)
"""
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "OK 1 True" in done.stdout
