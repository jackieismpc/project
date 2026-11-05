"""授权接口：默认不启用；启用后各种状态都只报告、不锁定。"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gelinspect.services.license import (
    LICENSE_FORMAT,
    LicenseService,
    LicenseState,
    signed_payload,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)
DEVICE = "ab" * 32


def issue(path: Path, key: Ed25519PrivateKey, **fields: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": LICENSE_FORMAT,
        "license_id": "LIC-1",
        "customer": "测试客户",
        "device_fingerprint": DEVICE,
        "features": ["standard"],
        "issued_at": "2026-11-01T00:00:00Z",
        "expires_at": None,
        **fields,
    }
    document["signature"] = base64.b64encode(key.sign(signed_payload(document))).decode()
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return document


def service(path: Path, key: Ed25519PrivateKey | None, **kwargs: Any) -> LicenseService:
    public = (
        None
        if key is None
        else key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
    return LicenseService(
        license_file=path,
        public_key=public,
        fingerprint=lambda: DEVICE,
        clock=lambda: NOW,
        **kwargs,
    )


def test_license_check_is_not_enabled_by_default(tmp_path: Path) -> None:
    status = LicenseService(license_file=tmp_path / "nothing.json").check()
    assert status.state is LicenseState.NOT_ENABLED and not status.enforced
    assert len(LicenseService().device_fingerprint()) == 64


def test_valid_license(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    issue(tmp_path / "license.json", key)
    status = service(tmp_path / "license.json", key, enabled=True).check()
    assert status.state is LicenseState.VALID and status.info is not None
    assert status.info.customer == "测试客户" and not status.enforced


def test_every_problem_is_reported_but_never_enforced(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "license.json"
    assert service(path, key, enabled=True).check().state is LicenseState.MISSING
    issue(path, key, expires_at="2026-11-05T00:00:00Z")
    assert service(path, key, enabled=True).check().state is LicenseState.EXPIRED
    issue(path, key, device_fingerprint="cd" * 32)
    assert service(path, key, enabled=True).check().state is LicenseState.WRONG_DEVICE
    document = issue(path, key)
    document["customer"] = "改过的客户"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert service(path, key, enabled=True).check().state is LicenseState.INVALID
    path.write_text("{not json", encoding="utf-8")
    assert service(path, None, enabled=True).check().state is LicenseState.INVALID
    issue(path, Ed25519PrivateKey.generate())  # 别人签的
    status = service(path, key, enabled=True).check()
    assert status.state is LicenseState.INVALID and not status.enforced
