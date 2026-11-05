"""程序文件清单：构建时生成并签名，发布包启动时校验。

构建脚本（``scripts/build.py``）在发布包目录里写两个文件：

* ``gelinspect-manifest.json``：每个文件的相对路径和 SHA-256，以及版本、构建标识、变体；
* ``gelinspect-manifest.sig``：对清单文件原始字节的 Ed25519 签名（Base64）。

签名私钥只在构建机上（每次构建临时生成或由 ``GELINSPECT_MANIFEST_KEY`` 指定），
公钥编译进主程序（``gelinspect._build_info``）。启动时重新计算清单里每个文件的哈希：
文件被改动、缺失，或程序目录里多出可执行代码，都会被发现。

局限（见 ``docs/features/security.md``）：主程序本身携带公钥，替换整个主程序等于换掉了校验者，
这一层防不住；macOS 上主程序在加入清单后会被重新做 ad-hoc 签名，所以主程序不在清单里，
由系统的代码签名覆盖。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MANIFEST_FILE = "gelinspect-manifest.json"
"""清单文件名（在程序目录里）。"""

SIGNATURE_FILE = "gelinspect-manifest.sig"
"""签名文件名。"""

MANIFEST_FORMAT = "gelinspect-manifest/1"
"""清单格式标识。"""

_IGNORED_PARTS = frozenset({"__pycache__"})
_CODE_SUFFIXES = frozenset({".so", ".dylib", ".dll", ".pyd", ".exe", ".py", ".pyc", ".bin"})
_CHUNK = 1024 * 1024


class IntegrityStatus(StrEnum):
    """完整性校验的结论。"""

    OK = "ok"
    """全部文件与清单一致。"""
    SKIPPED = "skipped"
    """仓库模式（开发运行）或配置关闭，跳过。"""
    MISSING_MANIFEST = "missing_manifest"
    """发布包里没有清单或签名。"""
    BAD_SIGNATURE = "bad_signature"
    """清单的签名不对：清单被改动或被替换。"""
    MISMATCH = "mismatch"
    """有文件被改动、缺失，或多出了可执行代码。"""


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """校验结果。

    Attributes:
        status: 结论。
        message: 面向管理员的说明。
        modified: 内容被改动的文件。
        missing: 缺失的文件。
        added: 清单里没有、但出现在程序目录里的代码文件。
        checked: 校验了多少个文件。
        elapsed_s: 耗时（秒）。
    """

    status: IntegrityStatus
    message: str
    modified: tuple[str, ...] = field(default=())
    missing: tuple[str, ...] = field(default=())
    added: tuple[str, ...] = field(default=())
    checked: int = 0
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        """没有发现问题（通过或跳过）。"""
        return self.status in (IntegrityStatus.OK, IntegrityStatus.SKIPPED)

    def summary(self, limit: int = 5) -> str:
        """一行摘要，写审计用。

        Args:
            limit: 每类最多列出几个文件。

        Returns:
            摘要文字。
        """
        parts = [self.message]
        for label, items in (("改动", self.modified), ("缺失", self.missing), ("多出", self.added)):
            if items:
                shown = "、".join(items[:limit])
                more = f" 等 {len(items)} 个" if len(items) > limit else ""
                parts.append(f"{label}：{shown}{more}")
        return "；".join(parts)


def file_sha256(path: Path) -> str:
    """计算文件的 SHA-256。

    Args:
        path: 文件。

    Returns:
        64 位小写十六进制。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root)
        if _IGNORED_PARTS & set(relative.parts):
            continue
        yield path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def build_manifest(
    root: Path,
    private_key: Ed25519PrivateKey,
    *,
    version: str,
    build_id: str,
    variant: str,
    exclude: Iterable[str] = (),
) -> int:
    """生成清单和签名，写到 ``root`` 下。

    Args:
        root: 发布包的程序目录。
        private_key: 签名私钥。
        version: 版本号。
        build_id: 构建标识。
        variant: ``release`` 或 ``demo``。
        exclude: 不进清单的相对路径（如 macOS 上会被重新签名的主程序），也可以是目录前缀。

    Returns:
        清单里的文件数。
    """
    excluded = {MANIFEST_FILE, SIGNATURE_FILE, *exclude}

    def skipped(name: str) -> bool:
        return any(name == item or name.startswith(item.rstrip("/") + "/") for item in excluded)

    files = {
        name: file_sha256(path)
        for path in _walk(root)
        if not skipped(name := _relative(root, path))
    }
    document = {
        "format": MANIFEST_FORMAT,
        "version": version,
        "build_id": build_id,
        "variant": variant,
        "algorithm": "sha256",
        "excluded": sorted(excluded - {MANIFEST_FILE, SIGNATURE_FILE}),
        "files": files,
    }
    data = json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8")
    (root / MANIFEST_FILE).write_bytes(data)
    signature = base64.b64encode(private_key.sign(data))
    (root / SIGNATURE_FILE).write_bytes(signature + b"\n")
    return len(files)


def verify_manifest(root: Path, public_key_hex: str) -> IntegrityReport:
    """按清单校验程序目录。

    Args:
        root: 发布包的程序目录。
        public_key_hex: 编译进程序的 Ed25519 公钥（十六进制）。

    Returns:
        校验结果。
    """
    started = time.perf_counter()
    manifest = root / MANIFEST_FILE
    signature_file = root / SIGNATURE_FILE
    if not manifest.is_file() or not signature_file.is_file():
        return IntegrityReport(IntegrityStatus.MISSING_MANIFEST, "发布包里缺少文件清单或签名")
    data = manifest.read_bytes()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        signature = base64.b64decode(signature_file.read_bytes().strip(), validate=True)
        public_key.verify(signature, data)
        document = json.loads(data.decode("utf-8"))
        files: dict[str, str] = dict(document["files"])
        excluded = [str(item) for item in document.get("excluded", [])]
    except (InvalidSignature, ValueError, binascii.Error, KeyError, TypeError):
        return IntegrityReport(
            IntegrityStatus.BAD_SIGNATURE, "文件清单的签名不对：清单可能被改动或替换"
        )
    modified: list[str] = []
    missing: list[str] = []
    for name, expected in sorted(files.items()):
        path = root / name
        if not path.is_file():
            missing.append(name)
        elif file_sha256(path) != expected:
            modified.append(name)
    skip = {MANIFEST_FILE, SIGNATURE_FILE, *excluded}

    def listed(name: str) -> bool:
        return name in files or any(
            name == item or name.startswith(item.rstrip("/") + "/") for item in skip
        )

    added = [
        name
        for path in _walk(root)
        if not listed(name := _relative(root, path)) and path.suffix.lower() in _CODE_SUFFIXES
    ]
    elapsed = time.perf_counter() - started
    if modified or missing or added:
        return IntegrityReport(
            IntegrityStatus.MISMATCH,
            "程序文件与发布清单不一致，程序可能被改动",
            tuple(modified),
            tuple(missing),
            tuple(added),
            len(files),
            elapsed,
        )
    return IntegrityReport(
        IntegrityStatus.OK,
        f"程序文件与发布清单一致（{len(files)} 个文件）",
        checked=len(files),
        elapsed_s=elapsed,
    )
