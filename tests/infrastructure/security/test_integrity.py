"""程序文件清单：生成、签名、校验；改动、缺失、多出代码、伪造清单都能发现。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gelinspect.infrastructure.security.integrity import (
    MANIFEST_FILE,
    SIGNATURE_FILE,
    IntegrityStatus,
    build_manifest,
    verify_manifest,
)


def public_hex(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    root = tmp_path / "GelInspect"
    (root / "gelinspect" / "ui" / "assets").mkdir(parents=True)
    (root / "translations").mkdir()
    (root / "gelinspect-app").write_bytes(b"\xcf\xfa\xed\xfe main executable")
    (root / "libpython3.11.dylib").write_bytes(b"lib" * 1000)
    (root / "gelinspect" / "ui" / "assets" / "font.ttf").write_bytes(b"font")
    (root / "translations" / "gelinspect_en.qm").write_bytes(b"qm")
    (root / "gelinspect" / "__pycache__").mkdir()
    (root / "gelinspect" / "__pycache__" / "x.pyc").write_bytes(b"cache")
    key = Ed25519PrivateKey.generate()
    count = build_manifest(
        root, key, version="0.1.0", build_id="b1", variant="release", exclude=["gelinspect-app"]
    )
    assert count == 3  # 主程序被排除，__pycache__ 被忽略
    return root, key


def test_untouched_release_passes(release: tuple[Path, Ed25519PrivateKey]) -> None:
    root, key = release
    report = verify_manifest(root, public_hex(key))
    assert report.status is IntegrityStatus.OK and report.ok and report.checked == 3
    document = json.loads((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert document["variant"] == "release" and document["excluded"] == ["gelinspect-app"]


def test_modified_missing_and_added_files_are_reported(
    release: tuple[Path, Ed25519PrivateKey],
) -> None:
    root, key = release
    (root / "translations" / "gelinspect_en.qm").write_bytes(b"changed")
    (root / "gelinspect" / "ui" / "assets" / "font.ttf").unlink()
    (root / "evil.dylib").write_bytes(b"injected")
    (root / "notes.txt").write_text("用户随手放的文字文件不算代码")
    report = verify_manifest(root, public_hex(key))
    assert report.status is IntegrityStatus.MISMATCH and not report.ok
    assert report.modified == ("translations/gelinspect_en.qm",)
    assert report.missing == ("gelinspect/ui/assets/font.ttf",)
    assert report.added == ("evil.dylib",)
    summary = report.summary()
    assert "改动" in summary and "缺失" in summary and "evil.dylib" in summary


def test_a_rewritten_manifest_or_foreign_key_is_rejected(
    release: tuple[Path, Ed25519PrivateKey],
) -> None:
    root, key = release
    (root / "libpython3.11.dylib").write_bytes(b"patched")
    # 攻击者改了文件又重算清单，但没有构建私钥，只能自己签
    attacker = Ed25519PrivateKey.generate()
    build_manifest(root, attacker, version="0.1.0", build_id="b1", variant="release")
    assert verify_manifest(root, public_hex(key)).status is IntegrityStatus.BAD_SIGNATURE
    # 只改清单内容不重签
    data = (root / MANIFEST_FILE).read_text(encoding="utf-8").replace("release", "demo")
    (root / MANIFEST_FILE).write_text(data, encoding="utf-8")
    assert verify_manifest(root, public_hex(attacker)).status is IntegrityStatus.BAD_SIGNATURE


def test_missing_manifest_is_reported(release: tuple[Path, Ed25519PrivateKey]) -> None:
    root, key = release
    (root / SIGNATURE_FILE).unlink()
    assert verify_manifest(root, public_hex(key)).status is IntegrityStatus.MISSING_MANIFEST
