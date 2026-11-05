"""设备绑定授权：接口和离线授权文件格式（**默认不启用**）。

本模块只留接口，不做任何把用户锁在外面的事：

* 配置 ``license.enabled`` 默认为 false，此时状态永远是「未启用」，什么都不检查。
* 启用后，授权缺失、过期、签名不对或设备不符，也只返回相应状态供界面提示和审计，
  **不会阻止登录、试验、查看或导出**。是否在某些状态下限制功能，要等甲方决定后另行实现。

离线授权文件（JSON，UTF-8）：

.. code-block:: json

    {
      "format": "gelinspect-license/1",
      "license_id": "LIC-2026-0001",
      "customer": "客户名称",
      "device_fingerprint": "设备指纹（64 位十六进制），留空表示不绑定设备",
      "features": ["standard"],
      "issued_at": "2026-11-06T00:00:00Z",
      "expires_at": null,
      "signature": "对上面字段规范化 JSON 的 Ed25519 签名（Base64）"
    }

签名覆盖除 ``signature`` 外的全部字段（键排序、无空白、UTF-8）。验签公钥由签发方提供，
通过 :class:`LicenseService` 的参数注入（发布时编译进程序）；没有公钥时只校验格式。
设备指纹由 :class:`DeviceFingerprint` 提供，默认实现 :func:`default_fingerprint` 取主机名、
机器类型和 MAC 地址的 SHA-256，**只用于演示接口**，网卡变化会改变它，正式启用前应换成更稳定的来源。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import platform
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

LICENSE_FORMAT = "gelinspect-license/1"
"""授权文件的格式标识。"""

LICENSE_FILE = "license.json"
"""默认的授权文件名（数据目录下）。"""


class LicenseState(StrEnum):
    """授权状态。"""

    NOT_ENABLED = "not_enabled"
    """未启用授权检查（默认）。"""
    VALID = "valid"
    """授权有效。"""
    MISSING = "missing"
    """启用了检查，但没有授权文件。"""
    INVALID = "invalid"
    """授权文件格式不对或签名不对。"""
    EXPIRED = "expired"
    """授权已过期。"""
    WRONG_DEVICE = "wrong_device"
    """授权绑定的是另一台设备。"""


@dataclass(frozen=True, slots=True)
class LicenseInfo:
    """授权文件的内容（已解析）。

    Attributes:
        license_id: 授权编号。
        customer: 客户名称。
        device_fingerprint: 绑定的设备指纹，空表示不绑定。
        features: 授权的功能。
        issued_at: 签发时间。
        expires_at: 到期时间，None 表示长期。
    """

    license_id: str
    customer: str
    device_fingerprint: str
    features: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class LicenseStatus:
    """授权检查结果。

    Attributes:
        state: 状态。
        message: 面向管理员的说明。
        info: 解析出的授权内容（有授权文件且格式正确时）。
        enforced: 是否会限制功能。本版本总是 False：授权只提示，不锁定。
    """

    state: LicenseState
    message: str
    info: LicenseInfo | None = None
    enforced: bool = field(default=False)


class DeviceFingerprint(Protocol):
    """设备指纹的来源。"""

    def __call__(self) -> str:
        """返回本机的设备指纹（64 位十六进制）。"""
        ...


def default_fingerprint() -> str:
    """演示用的设备指纹：主机名、机器类型和 MAC 地址的 SHA-256。

    Returns:
        64 位十六进制。
    """
    raw = "|".join((platform.node(), platform.machine(), f"{uuid.getnode():012x}"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def signed_payload(document: dict[str, Any]) -> bytes:
    """授权文件里参与签名的内容：除 ``signature`` 外的全部字段，规范化 JSON。

    Args:
        document: 授权文件的 JSON 对象。

    Returns:
        参与签名的字节串。
    """
    body = {key: value for key, value in document.items() if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _parse_time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是时间文字")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} 必须带时区")
    return parsed


def parse_license(document: dict[str, Any]) -> LicenseInfo:
    """解析授权文件的字段（不验签）。

    Args:
        document: 授权文件的 JSON 对象。

    Returns:
        授权内容。

    Raises:
        ValueError: 格式不对。
    """
    if document.get("format") != LICENSE_FORMAT:
        raise ValueError(f"format 应为 {LICENSE_FORMAT}")
    features = document.get("features", [])
    if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
        raise ValueError("features 必须是文字列表")
    expires = document.get("expires_at")
    return LicenseInfo(
        license_id=str(document["license_id"]),
        customer=str(document.get("customer", "")),
        device_fingerprint=str(document.get("device_fingerprint") or ""),
        features=tuple(features),
        issued_at=_parse_time(document.get("issued_at"), "issued_at"),
        expires_at=None if expires is None else _parse_time(expires, "expires_at"),
    )


class LicenseService:
    """授权检查。默认不启用；启用后也只报告状态，不限制使用。"""

    def __init__(
        self,
        *,
        enabled: bool = False,
        license_file: Path | None = None,
        public_key: bytes | None = None,
        fingerprint: DeviceFingerprint | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            enabled: 是否启用授权检查（配置 ``license.enabled``）。
            license_file: 授权文件路径。
            public_key: 验签用的 Ed25519 公钥（32 字节原始格式）；None 时只校验格式。
            fingerprint: 设备指纹来源，默认 :func:`default_fingerprint`。
            clock: 当前时间，测试注入。
        """
        self._enabled = enabled
        self._file = license_file
        self._public_key = public_key
        self._fingerprint = fingerprint or default_fingerprint
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def enabled(self) -> bool:
        """是否启用了授权检查。"""
        return self._enabled

    def device_fingerprint(self) -> str:
        """本机的设备指纹，用于向签发方申请授权。

        Returns:
            64 位十六进制。
        """
        return self._fingerprint()

    def check(self) -> LicenseStatus:
        """检查授权。

        Returns:
            授权状态。未启用时总是 ``NOT_ENABLED``，不读任何文件。
        """
        if not self._enabled:
            return LicenseStatus(LicenseState.NOT_ENABLED, "未启用授权检查")
        if self._file is None or not self._file.is_file():
            return LicenseStatus(LicenseState.MISSING, "启用了授权检查，但没有找到授权文件")
        try:
            document = json.loads(self._file.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("顶层必须是对象")
            info = parse_license(document)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
            return LicenseStatus(LicenseState.INVALID, f"授权文件格式不对：{exc}")
        if self._public_key is not None:
            try:
                signature = base64.b64decode(str(document.get("signature", "")), validate=True)
                Ed25519PublicKey.from_public_bytes(self._public_key).verify(
                    signature, signed_payload(document)
                )
            except (InvalidSignature, ValueError, binascii.Error):
                return LicenseStatus(LicenseState.INVALID, "授权文件的签名不对", info)
        if info.expires_at is not None and self._clock() >= info.expires_at:
            return LicenseStatus(LicenseState.EXPIRED, "授权已过期", info)
        if info.device_fingerprint and info.device_fingerprint != self._fingerprint():
            return LicenseStatus(LicenseState.WRONG_DEVICE, "授权绑定的是另一台设备", info)
        return LicenseStatus(LicenseState.VALID, "授权有效", info)
