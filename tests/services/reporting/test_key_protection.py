"""签名私钥加密存储：加密落盘、迁移 P13 之前的明文私钥、错误密钥、组装根自动迁移并写审计。"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from gelinspect.app_context import build_app_context
from gelinspect.domain.enums import AuditAction
from gelinspect.infrastructure.security.protectors import (
    INSTALL_SECRET_FILE,
    FileKeyProtector,
    create_key_protector,
)
from gelinspect.interfaces.storage import AuditQuery, PageRequest
from gelinspect.services.config import DEFAULT_CONFIG, parse_config
from gelinspect.services.reporting.keystore import KEY_FILE, KeyStoreError, LocalKeyStore

from ..conftest import NOW


def make_store(directory: Path, protector: object | None = None) -> LocalKeyStore:
    return LocalKeyStore(
        directory,
        common_name="测试签名",
        organization="测试机构",
        protector=protector,  # type: ignore[arg-type]
    )


def write_plaintext_key(directory: Path) -> bytes:
    """P13 之前的样子：明文 PEM 私钥和证书。返回私钥的公钥 DER。"""
    identity = make_store(directory).load_or_create()  # 默认明文保护器
    assert (directory / KEY_FILE).read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    return identity.private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def test_new_private_key_is_encrypted_on_disk(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    store = make_store(keys, FileKeyProtector(keys / INSTALL_SECRET_FILE))
    identity = store.load_or_create()
    raw = (keys / KEY_FILE).read_bytes()
    assert raw.startswith(b"GIKP1:file:") and b"PRIVATE KEY" not in raw
    again = make_store(keys, FileKeyProtector(keys / INSTALL_SECRET_FILE)).load_or_create()
    assert again.fingerprint == identity.fingerprint


def test_plaintext_key_is_migrated_and_the_plaintext_is_gone(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    public = write_plaintext_key(keys)
    store = make_store(keys, FileKeyProtector(keys / INSTALL_SECRET_FILE))
    assert store.migrate() == "plaintext"
    raw = (keys / KEY_FILE).read_bytes()
    assert raw.startswith(b"GIKP1:file:") and b"PRIVATE KEY" not in raw
    assert not list(keys.glob("*.tmp"))
    identity = store.load_or_create()
    assert (
        identity.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        == public
    )
    assert store.migrate() is None  # 已经迁移过


def test_plaintext_key_is_also_migrated_on_first_use(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    write_plaintext_key(keys)
    make_store(keys, FileKeyProtector(keys / INSTALL_SECRET_FILE)).load_or_create()
    assert (keys / KEY_FILE).read_bytes().startswith(b"GIKP1:")


def test_wrong_key_is_a_clear_error_without_key_material(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    make_store(keys, FileKeyProtector(keys / INSTALL_SECRET_FILE)).load_or_create()
    other = FileKeyProtector(tmp_path / "other" / INSTALL_SECRET_FILE)
    other.protect(b"make the other install key")
    with pytest.raises(KeyStoreError, match="无法解密私钥") as error:
        make_store(keys, other).load_or_create()
    assert "PRIVATE" not in str(error.value)


def test_moving_to_a_new_protection_method_re_encrypts(tmp_path: Path) -> None:
    from tests.infrastructure.security.fake_security import FakeSecurity

    keys = tmp_path / "keys"
    identity = make_store(keys, create_key_protector(keys, "file")).load_or_create()
    keychain = create_key_protector(keys, "auto", runner=FakeSecurity(), platform="darwin")
    store = make_store(keys, keychain)
    assert store.migrate() == "reprotected"
    assert (keys / KEY_FILE).read_bytes().startswith(b"GIKP1:keychain:")
    assert store.load_or_create().fingerprint == identity.fingerprint


def test_startup_migrates_a_plaintext_key_and_audits_it(tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_plaintext_key(data / "keys")
    config = parse_config({**DEFAULT_CONFIG, "data_dir": str(data)})
    context = build_app_context(config, clock=lambda: NOW)
    try:
        assert (data / "keys" / KEY_FILE).read_bytes().startswith(b"GIKP1:file:")
        page = context.database.audit.query(
            AuditQuery(action=AuditAction.KEY_PROTECTION_CHANGED), PageRequest()
        )
        assert [r.event.detail for r in page.items] == [
            "报告签名私钥由明文改为加密保存，原明文文件已覆盖删除"
        ]
        assert context.audit_keys is not None and context.audit_keys.switched
    finally:
        context.close()
