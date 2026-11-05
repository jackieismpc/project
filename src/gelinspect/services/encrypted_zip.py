"""带密码的 ZIP（WinZip AES-256，AE-2），用于加密敏感导出。

只依赖已有的 ``cryptography`` 和 ``numpy``，没有引入 GPL 依赖。格式按 WinZip 公开的
「AES Encryption Information」规范：

* 每个文件：deflate 压缩 → AES-256-CTR（计数器为小端、从 1 开始）加密 →
  HMAC-SHA1 认证码（前 10 字节）。
* 密钥：PBKDF2-HMAC-SHA1，1000 次迭代，16 字节随机盐，派生出 AES 密钥、HMAC 密钥和
  2 字节密码校验值。
* 压缩方法记为 99，额外字段 ``0x9901`` 记录 AE-2、AES-256 和真实的压缩方法；
  AE-2 不存 CRC（由认证码保证完整性）。

7-Zip、WinRAR、Keka、The Unarchiver、bsdtar（libarchive）都能打开；**Windows 资源管理器和
macOS「归档实用工具」自带的解压不支持 AES**，文档里说明。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import struct
import tempfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MIN_PASSWORD_LENGTH = 8
"""导出密码的最短长度。"""

_SALT_LENGTH = 16  # AES-256
_KEY_LENGTH = 32
_ITERATIONS = 1000
_AUTH_LENGTH = 10
_METHOD_AES = 99
_METHOD_DEFLATE = 8
_FLAG_ENCRYPTED = 0x0001
_FLAG_UTF8 = 0x0800
_VERSION_NEEDED = 51
_EXTRA_ID = 0x9901
_MAX_SIZE = 0xFFFFFFFF - 1024


class EncryptedZipError(Exception):
    """加密 ZIP 无法生成或读取。"""


class WrongPasswordError(EncryptedZipError):
    """密码不对。"""


@dataclass(frozen=True, slots=True)
class _Entry:
    name: bytes
    flags: int
    dos_time: int
    dos_date: int
    compressed_size: int
    size: int
    extra: bytes
    offset: int


def check_password(password: str) -> None:
    """检查导出密码是否可用。

    Args:
        password: 密码。

    Raises:
        EncryptedZipError: 太短。
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise EncryptedZipError(f"导出密码至少 {MIN_PASSWORD_LENGTH} 个字符")


def _derive(password: str, salt: bytes) -> tuple[bytes, bytes, bytes]:
    material = hashlib.pbkdf2_hmac(
        "sha1", password.encode("utf-8"), salt, _ITERATIONS, 2 * _KEY_LENGTH + 2
    )
    return material[:_KEY_LENGTH], material[_KEY_LENGTH : 2 * _KEY_LENGTH], material[-2:]


def _ctr_xor(key: bytes, data: bytes) -> bytes:
    """WinZip 的 AES-CTR：计数器是 16 字节小端整数，从 1 开始。"""
    if not data:
        return b""
    blocks = (len(data) + 15) // 16
    counters = np.zeros((blocks, 2), dtype="<u8")
    counters[:, 0] = np.arange(1, blocks + 1, dtype=np.uint64)
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    stream = encryptor.update(counters.tobytes()) + encryptor.finalize()
    plain = np.frombuffer(data, dtype=np.uint8)
    keystream = np.frombuffer(stream, dtype=np.uint8)[: len(data)]
    return np.bitwise_xor(plain, keystream).tobytes()


def _dos_stamp(moment: datetime) -> tuple[int, int]:
    year = max(moment.year, 1980)
    dos_time = (moment.hour << 11) | (moment.minute << 5) | (moment.second // 2)
    dos_date = ((year - 1980) << 9) | (moment.month << 5) | moment.day
    return dos_time, dos_date


def _extra() -> bytes:
    return struct.pack("<HHH2sBH", _EXTRA_ID, 7, 2, b"AE", 3, _METHOD_DEFLATE)


def write_encrypted_zip(
    destination: Path,
    entries: Sequence[tuple[str, Path | bytes]],
    password: str,
    *,
    now: datetime | None = None,
) -> Path:
    """把若干文件写成一个 AES-256 加密的 ZIP（先写临时文件再原子替换）。

    Args:
        destination: 目标 ZIP 文件。
        entries: （包内文件名，源文件路径或内容）。
        password: 密码，至少 :data:`MIN_PASSWORD_LENGTH` 个字符。
        now: 写入包内的修改时间，默认当前本地时间。

    Returns:
        写出的文件。

    Raises:
        EncryptedZipError: 密码太短、包内文件名重复或文件太大。
        OSError: 读源文件或写目标失败。
    """
    check_password(password)
    names = [name for name, _ in entries]
    if len(set(names)) != len(names):
        raise EncryptedZipError("包内文件名重复")
    dos_time, dos_date = _dos_stamp(now or datetime.now())
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    temporary = Path(temporary_name)
    central: list[_Entry] = []
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for name, source in entries:
                data = source.read_bytes() if isinstance(source, Path) else source
                if len(data) > _MAX_SIZE:
                    raise EncryptedZipError(f"{name} 太大，超过 4 GB")
                compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
                compressed = compressor.compress(data) + compressor.flush()
                salt = secrets.token_bytes(_SALT_LENGTH)
                aes_key, mac_key, verifier = _derive(password, salt)
                encrypted = _ctr_xor(aes_key, compressed)
                auth = hmac.new(mac_key, encrypted, hashlib.sha1).digest()[:_AUTH_LENGTH]
                payload = salt + verifier + encrypted + auth
                if len(payload) > _MAX_SIZE:
                    raise EncryptedZipError(f"{name} 太大，超过 4 GB")
                raw_name = name.encode("utf-8")
                extra = _extra()
                flags = _FLAG_ENCRYPTED | _FLAG_UTF8
                offset = handle.tell()
                handle.write(
                    struct.pack(
                        "<IHHHHHIIIHH",
                        0x04034B50,
                        _VERSION_NEEDED,
                        flags,
                        _METHOD_AES,
                        dos_time,
                        dos_date,
                        0,  # AE-2 不存 CRC
                        len(payload),
                        len(data),
                        len(raw_name),
                        len(extra),
                    )
                )
                handle.write(raw_name)
                handle.write(extra)
                handle.write(payload)
                central.append(
                    _Entry(
                        raw_name, flags, dos_time, dos_date, len(payload), len(data), extra, offset
                    )
                )
            directory_offset = handle.tell()
            for entry in central:
                handle.write(
                    struct.pack(
                        "<IHHHHHHIIIHHHHHII",
                        0x02014B50,
                        _VERSION_NEEDED,
                        _VERSION_NEEDED,
                        entry.flags,
                        _METHOD_AES,
                        entry.dos_time,
                        entry.dos_date,
                        0,
                        entry.compressed_size,
                        entry.size,
                        len(entry.name),
                        len(entry.extra),
                        0,
                        0,
                        0,
                        0,
                        entry.offset,
                    )
                )
                handle.write(entry.name)
                handle.write(entry.extra)
            directory_size = handle.tell() - directory_offset
            handle.write(
                struct.pack(
                    "<IHHHHIIH",
                    0x06054B50,
                    0,
                    0,
                    len(central),
                    len(central),
                    directory_size,
                    directory_offset,
                    0,
                )
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def read_encrypted_zip(path: Path, password: str) -> dict[str, bytes]:
    """读取 :func:`write_encrypted_zip` 写出的 ZIP（用于校验和测试）。

    Args:
        path: ZIP 文件。
        password: 密码。

    Returns:
        包内文件名到内容的映射。

    Raises:
        WrongPasswordError: 密码不对。
        EncryptedZipError: 文件损坏、被改动，或不是 AES 加密的 ZIP。
    """
    data = path.read_bytes()
    end = data.rfind(b"PK\x05\x06")
    if end < 0 or len(data) - end < 22:
        raise EncryptedZipError("不是 ZIP 文件")
    _sig, _d, _cd, _n, count, size, offset, _c = struct.unpack("<IHHHHIIH", data[end : end + 22])
    result: dict[str, bytes] = {}
    position = offset
    for _ in range(count):
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", data[position : position + 46])
        if fields[0] != 0x02014B50:
            raise EncryptedZipError("ZIP 中央目录损坏")
        method, compressed_size, name_length, extra_length, comment_length, local = (
            fields[4],
            fields[8],
            fields[10],
            fields[11],
            fields[12],
            fields[16],
        )
        name = data[position + 46 : position + 46 + name_length].decode("utf-8")
        position += 46 + name_length + extra_length + comment_length
        if method != _METHOD_AES:
            raise EncryptedZipError(f"{name} 不是 AES 加密的")
        header = struct.unpack("<IHHHHHIIIHH", data[local : local + 30])
        start = local + 30 + header[9] + header[10]
        payload = data[start : start + compressed_size]
        salt, verifier = payload[:_SALT_LENGTH], payload[_SALT_LENGTH : _SALT_LENGTH + 2]
        encrypted = payload[_SALT_LENGTH + 2 : -_AUTH_LENGTH]
        auth = payload[-_AUTH_LENGTH:]
        aes_key, mac_key, expected_verifier = _derive(password, salt)
        if not hmac.compare_digest(verifier, expected_verifier):
            raise WrongPasswordError("密码不对")
        mac = hmac.new(mac_key, encrypted, hashlib.sha1).digest()[:_AUTH_LENGTH]
        if not hmac.compare_digest(mac, auth):
            raise EncryptedZipError(f"{name} 的认证码不对：文件被改动或损坏")
        try:
            result[name] = zlib.decompress(_ctr_xor(aes_key, encrypted), -15)
        except zlib.error as exc:
            raise EncryptedZipError(f"{name} 解压失败") from exc
    if size and offset + size > end:
        raise EncryptedZipError("ZIP 中央目录越界")
    return result
