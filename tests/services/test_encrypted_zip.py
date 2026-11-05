"""带密码的 ZIP（WinZip AES-256）：往返、错误密码、篡改、与 libarchive 互通。"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from gelinspect.services.encrypted_zip import (
    EncryptedZipError,
    WrongPasswordError,
    read_encrypted_zip,
    write_encrypted_zip,
)

PASSWORD = "Export#2026"


def test_round_trip_keeps_names_and_bytes(tmp_path: Path) -> None:
    source = tmp_path / "结果.xlsx"
    source.write_bytes(os.urandom(70_000) + b"tail")
    archive = write_encrypted_zip(
        tmp_path / "out.zip",
        [("结果.xlsx", source), ("manifest.csv", b"a,b\n1,2\n")],
        PASSWORD,
        now=datetime(2026, 11, 6, 9, 30),
    )
    content = read_encrypted_zip(archive, PASSWORD)
    assert content == {"结果.xlsx": source.read_bytes(), "manifest.csv": b"a,b\n1,2\n"}
    raw = archive.read_bytes()
    assert b"tail" not in raw and b"a,b" not in raw  # 内容是密文
    assert not list(tmp_path.glob("*.part"))


def test_wrong_password_and_tampering_are_detected(tmp_path: Path) -> None:
    archive = write_encrypted_zip(tmp_path / "x.zip", [("a.txt", b"secret" * 100)], PASSWORD)
    with pytest.raises(WrongPasswordError):
        read_encrypted_zip(archive, "Wrong#2026")
    data = bytearray(archive.read_bytes())
    data[80] ^= 0xFF  # 改密文中间一个字节
    archive.write_bytes(bytes(data))
    with pytest.raises(EncryptedZipError, match="认证码不对|密码不对"):
        read_encrypted_zip(archive, PASSWORD)


def test_short_passwords_and_duplicate_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(EncryptedZipError, match="至少"):
        write_encrypted_zip(tmp_path / "x.zip", [("a", b"1")], "short")
    with pytest.raises(EncryptedZipError, match="重复"):
        write_encrypted_zip(tmp_path / "x.zip", [("a", b"1"), ("a", b"2")], PASSWORD)
    assert not (tmp_path / "x.zip").exists()


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="没有 bsdtar（libarchive）")
def test_libarchive_can_extract_it(tmp_path: Path) -> None:
    payload = os.urandom(5000)
    archive = write_encrypted_zip(tmp_path / "x.zip", [("报告/a.bin", payload)], PASSWORD)
    out = tmp_path / "out"
    out.mkdir()
    completed = subprocess.run(
        ["bsdtar", "-x", "-C", str(out), "--passphrase", PASSWORD, "-f", str(archive)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (out / "报告" / "a.bin").read_bytes() == payload
    wrong = subprocess.run(
        ["bsdtar", "-x", "-C", str(out), "--passphrase", "nope-nope", "-f", str(archive)],
        capture_output=True,
        check=False,
    )
    assert wrong.returncode != 0
