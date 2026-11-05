"""KeyProtector：安装密钥、钥匙串（假 security 命令）、DPAPI、组合与平台选择。"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from gelinspect.infrastructure.security.protectors import (
    INSTALL_SECRET_FILE,
    DpapiKeyProtector,
    FileKeyProtector,
    KeychainKeyProtector,
    create_key_protector,
    keychain_account,
    protected_kind,
)
from gelinspect.interfaces.security import KeyProtectionError, KeyProtector

from .fake_security import FakeSecurity

SECRET = b"-----BEGIN PRIVATE KEY-----\nnot really a key\n-----END PRIVATE KEY-----\n"


def test_file_protector_round_trip_and_file_format(tmp_path: Path) -> None:
    protector = FileKeyProtector(tmp_path / "keys" / INSTALL_SECRET_FILE)
    assert isinstance(protector, KeyProtector)
    blob = protector.protect(SECRET)
    assert blob.startswith(b"GIKP1:file:") and SECRET not in blob
    assert protected_kind(blob) == "file"
    assert protector.unprotect(blob) == SECRET
    # 每次加密用新的盐和随机数
    assert protector.protect(SECRET) != blob
    if os.name == "posix":
        assert (tmp_path / "keys" / INSTALL_SECRET_FILE).stat().st_mode & 0o777 == 0o600


def test_file_protector_with_another_install_key_fails(tmp_path: Path) -> None:
    blob = FileKeyProtector(tmp_path / "a" / INSTALL_SECRET_FILE).protect(SECRET)
    other = FileKeyProtector(tmp_path / "b" / INSTALL_SECRET_FILE)
    other.protect(b"create the other install key")
    with pytest.raises(KeyProtectionError, match="密钥不匹配"):
        other.unprotect(blob)


def test_file_protector_detects_tampering_and_missing_install_key(tmp_path: Path) -> None:
    path = tmp_path / INSTALL_SECRET_FILE
    protector = FileKeyProtector(path)
    blob = bytearray(protector.protect(SECRET))
    blob[-1] ^= 0x01
    with pytest.raises(KeyProtectionError):
        protector.unprotect(bytes(blob))
    good = protector.protect(SECRET)
    path.unlink()
    with pytest.raises(KeyProtectionError, match="缺少安装密钥"):
        protector.unprotect(good)
    assert not path.exists()  # 解密时不会悄悄生成一把对不上的新密钥


def test_unprotect_rejects_other_formats(tmp_path: Path) -> None:
    protector = FileKeyProtector(tmp_path / INSTALL_SECRET_FILE)
    with pytest.raises(KeyProtectionError, match="未加密"):
        protector.unprotect(SECRET)
    with pytest.raises(KeyProtectionError):
        protector.unprotect(b"GIKP1:keychain:xxxx")


def test_keychain_protector_round_trip_without_secret_in_arguments() -> None:
    fake = FakeSecurity()
    protector = KeychainKeyProtector("data-test", runner=fake)
    assert protector.available()
    blob = protector.protect(SECRET)
    assert blob.startswith(b"GIKP1:keychain:")
    assert KeychainKeyProtector("data-test", runner=fake).unprotect(blob) == SECRET
    master = fake.items[("GelInspect", "data-test")]
    # 主密钥只经过标准输入，不出现在任何一次命令的参数里
    assert all(master not in " ".join(call) for call in fake.calls)
    assert any(master in text for text in fake.stdin)


def test_keychain_protector_wrong_master_key_or_missing_item_fails() -> None:
    fake = FakeSecurity()
    blob = KeychainKeyProtector("data-a", runner=fake).protect(SECRET)
    KeychainKeyProtector("data-b", runner=fake).protect(b"other")
    with pytest.raises(KeyProtectionError, match="密钥不匹配"):
        KeychainKeyProtector("data-b", runner=fake).unprotect(blob)
    with pytest.raises(KeyProtectionError, match="没有 GelInspect 主密钥"):
        KeychainKeyProtector("data-a", runner=FakeSecurity()).unprotect(blob)


def test_keychain_write_denied_is_an_error() -> None:
    protector = KeychainKeyProtector("data-x", runner=FakeSecurity(deny_write=True))
    with pytest.raises(KeyProtectionError, match="无法把主密钥写入"):
        protector.protect(SECRET)


def test_keychain_accounts_differ_per_data_directory(tmp_path: Path) -> None:
    assert keychain_account(tmp_path / "a") != keychain_account(tmp_path / "b")
    assert keychain_account(tmp_path / "a") == keychain_account(tmp_path / "a")


def test_auto_mode_prefers_the_keychain_on_macos_and_reads_every_format(tmp_path: Path) -> None:
    fake = FakeSecurity()
    old = create_key_protector(tmp_path, "file", runner=fake, platform="darwin")
    old_blob = old.protect(SECRET)
    protector = create_key_protector(tmp_path, "auto", runner=fake, platform="darwin")
    assert protector.kind == "keychain"
    assert protector.unprotect(old_blob) == SECRET  # 旧的安装密钥格式仍能读
    assert protector.needs_upgrade(old_blob)
    blob = protector.protect(SECRET)
    assert not protector.needs_upgrade(blob)
    assert protector.unprotect(blob) == SECRET


def test_auto_mode_falls_back_to_the_install_key_and_os_mode_refuses(tmp_path: Path) -> None:
    unavailable = FakeSecurity(available=False)
    assert create_key_protector(tmp_path, "auto", runner=unavailable, platform="darwin").kind == (
        "file"
    )
    assert create_key_protector(tmp_path, "auto", runner=unavailable, platform="linux").kind == (
        "file"
    )
    with pytest.raises(KeyProtectionError, match="系统凭据"):
        create_key_protector(tmp_path, "os", runner=unavailable, platform="darwin")
    assert create_key_protector(tmp_path, "auto", platform="win32").kind == "dpapi"


def test_dpapi_is_refused_outside_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows 上见下一个测试")
    with pytest.raises(KeyProtectionError, match="只在 Windows"):
        DpapiKeyProtector().protect(SECRET)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 只在 Windows 上可用")
def test_dpapi_round_trip_and_tampering_on_windows() -> None:
    protector = DpapiKeyProtector()
    blob = protector.protect(SECRET)
    assert blob.startswith(b"GIKP1:dpapi:") and SECRET not in blob
    assert protector.unprotect(blob) == SECRET
    broken = bytearray(blob)
    broken[-5] ^= 0xFF
    with pytest.raises(KeyProtectionError):
        protector.unprotect(bytes(broken))


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("GELINSPECT_TEST_KEYCHAIN") != "1",
    reason="真实钥匙串测试：在沙盒外设置 GELINSPECT_TEST_KEYCHAIN=1 运行",
)
def test_real_keychain_round_trip(tmp_path: Path) -> None:
    account = f"pytest-{uuid.uuid4().hex[:12]}"
    protector = KeychainKeyProtector(account)
    try:
        assert protector.available()
        blob = protector.protect(SECRET)
        assert KeychainKeyProtector(account).unprotect(blob) == SECRET
    finally:
        subprocess.run(
            ["/usr/bin/security", "delete-generic-password", "-s", "GelInspect", "-a", account],
            capture_output=True,
            check=False,
        )


def test_auto_mode_falls_back_when_the_keychain_can_be_read_but_not_written(
    tmp_path: Path,
) -> None:
    """沙盒里常见：能读默认钥匙串，写条目被拒。"""
    fake = FakeSecurity(deny_write=True)
    assert create_key_protector(tmp_path, "auto", runner=fake, platform="darwin").kind == "file"
    with pytest.raises(KeyProtectionError):
        create_key_protector(tmp_path, "os", runner=fake, platform="darwin")
