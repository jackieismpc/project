"""``KeyProtector`` 的实现：DPAPI、钥匙串、安装密钥。

落盘格式统一为 ``GIKP1:<种类>:`` 头加上各自的密文，读取时按头里的种类选择解密方式，
所以换了保护方式（例如从口令加密升级到系统凭据）后，旧文件仍能读出来再重新保护。

* ``dpapi``（Windows）：``CryptProtectData``，绑定当前 Windows 用户，用 ``ctypes`` 调用，
  不新增依赖。换电脑或换用户后无法解密。
* ``keychain``（macOS）：主密钥（32 字节随机数）存放在当前用户的登录钥匙串里，
  数据用 AES-256-GCM 加密。主密钥通过 ``security -i`` 的标准输入写入，不出现在进程参数里。
* ``file``（都不可用时）：主密钥由数据目录里的安装密钥（``keys/install.secret``，32 字节随机数）
  经 scrypt 派生，AES-256-GCM 加密。**安装密钥和密文在同一台机器的同一个目录里**：
  它只能防止“只拿到某一个密钥文件”和误把密钥文件当明文传播，挡不住能读整个数据目录的人。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from gelinspect.interfaces.security import KeyProtectionError, KeyProtector

_log = logging.getLogger(__name__)

MAGIC = b"GIKP1:"
"""受保护数据的文件头。"""

INSTALL_SECRET_FILE = "install.secret"
"""安装密钥文件名（放在密钥目录里）。"""

KEYCHAIN_SERVICE = "GelInspect"
"""钥匙串条目的服务名。"""

ProtectionMode = Literal["auto", "os", "file"]
"""保护方式：``auto`` 系统凭据可用就用，否则回退到安装密钥；``os`` 只用系统凭据；
``file`` 只用安装密钥。"""

CommandRunner = Callable[[Sequence[str], str | None], "CommandResult"]
"""执行外部命令的函数：参数列表和标准输入，返回结果。测试替换成假的 ``security``。"""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """外部命令的结果。

    Attributes:
        returncode: 退出码。
        stdout: 标准输出。
        stderr: 标准错误。
    """

    returncode: int
    stdout: str
    stderr: str


def _run_command(args: Sequence[str], stdin: str | None) -> CommandResult:
    """执行命令，超时 15 秒。"""
    try:
        completed = subprocess.run(
            list(args),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _header(kind: str) -> bytes:
    return MAGIC + kind.encode("ascii") + b":"


def protected_kind(data: bytes) -> str | None:
    """读出受保护数据的种类。

    Args:
        data: 磁盘上的内容。

    Returns:
        ``dpapi``、``keychain``、``file``；不是受保护格式（例如明文 PEM）时为 None。
    """
    if not data.startswith(MAGIC):
        return None
    kind, sep, _ = data[len(MAGIC) :].partition(b":")
    if not sep or not kind:
        return None
    try:
        return kind.decode("ascii")
    except UnicodeDecodeError:
        return None


def _split(data: bytes, kind: str) -> bytes:
    header = _header(kind)
    if not data.startswith(header):
        found = protected_kind(data)
        raise KeyProtectionError(
            f"数据不是用 {kind} 方式保护的（{'未加密' if found is None else found}）"
        )
    return data[len(header) :]


def _aes_seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _aes_open(key: bytes, payload: bytes, aad: bytes) -> bytes:
    if len(payload) < 12 + 16:
        raise KeyProtectionError("受保护的数据太短，可能已损坏")
    try:
        return AESGCM(key).decrypt(payload[:12], payload[12:], aad)
    except InvalidTag as exc:
        raise KeyProtectionError("无法解密：密钥不匹配或数据已被改动") from exc


# ---------------------------------------------------------------------- 安装密钥


class FileKeyProtector:
    """用安装密钥派生的口令加密（AES-256-GCM，scrypt 派生）。

    安装密钥在第一次加密时生成，POSIX 下权限 600。没有安装密钥时解密失败，不会重新生成
    一个对不上的。
    """

    kind = "file"

    def __init__(self, secret_path: Path) -> None:
        """绑定安装密钥文件。

        Args:
            secret_path: 安装密钥文件路径（通常是 ``keys/install.secret``）。
        """
        self._path = secret_path

    @property
    def secret_path(self) -> Path:
        """安装密钥文件路径。"""
        return self._path

    def _secret(self, *, create: bool) -> bytes:
        if self._path.is_file():
            try:
                raw = self._path.read_text(encoding="ascii").strip()
                secret = binascii.unhexlify(raw)
            except (OSError, UnicodeDecodeError, binascii.Error) as exc:
                raise KeyProtectionError(f"安装密钥 {self._path.name} 无法读取或已损坏") from exc
            if len(secret) != 32:
                raise KeyProtectionError(f"安装密钥 {self._path.name} 长度不对，可能已损坏")
            return secret
        if not create:
            raise KeyProtectionError(
                f"缺少安装密钥 {self._path.name}，无法解密。请从备份恢复整个 keys 目录"
            )
        secret = secrets.token_bytes(32)
        write_private_file(self._path, secret.hex().encode("ascii"))
        return secret

    @staticmethod
    def _derive(secret: bytes, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(secret)

    def protect(self, key_pem: bytes) -> bytes:
        """加密。

        Args:
            key_pem: 明文。

        Returns:
            ``GIKP1:file:`` + 盐（16 字节）+ AES-GCM 密文。
        """
        salt = secrets.token_bytes(16)
        key = self._derive(self._secret(create=True), salt)
        header = _header(self.kind)
        return header + salt + _aes_seal(key, key_pem, header)

    def unprotect(self, data: bytes) -> bytes:
        """解密。

        Args:
            data: 磁盘上的内容。

        Returns:
            明文。

        Raises:
            KeyProtectionError: 不是本方式的数据、缺少安装密钥、密钥不匹配或数据被改动。
        """
        payload = _split(data, self.kind)
        if len(payload) < 16:
            raise KeyProtectionError("受保护的数据太短，可能已损坏")
        key = self._derive(self._secret(create=False), payload[:16])
        return _aes_open(key, payload[16:], _header(self.kind))


# ---------------------------------------------------------------------- macOS 钥匙串


class KeychainKeyProtector:
    """主密钥放在 macOS 登录钥匙串里，数据用 AES-256-GCM 加密。"""

    kind = "keychain"

    def __init__(
        self,
        account: str,
        *,
        service: str = KEYCHAIN_SERVICE,
        runner: CommandRunner | None = None,
        security_tool: str = "/usr/bin/security",
    ) -> None:
        """绑定一个钥匙串条目。

        Args:
            account: 条目的账户名，每个数据目录一个（见 :func:`keychain_account`）。
            service: 条目的服务名。
            runner: 执行命令的函数，测试注入。
            security_tool: ``security`` 命令的路径。
        """
        self._account = account
        self._service = service
        self._run = runner or _run_command
        self._tool = security_tool
        self._cached: bytes | None = None

    @property
    def account(self) -> str:
        """钥匙串条目的账户名。"""
        return self._account

    def available(self) -> bool:
        """钥匙串能否使用（有 ``security`` 命令且能访问默认钥匙串）。

        Returns:
            可用时为 True。
        """
        result = self._run([self._tool, "default-keychain"], None)
        return result.returncode == 0

    def ready(self) -> bool:
        """钥匙串真的能用：能访问默认钥匙串，并且能取得（必要时创建）本数据目录的主密钥。

        沙盒或无图形会话里常见“能读默认钥匙串、却不能写条目”，只看 :meth:`available` 不够。

        Returns:
            可以用来加密时为 True。
        """
        if not self.available():
            return False
        try:
            self._master(create=True)
        except KeyProtectionError as exc:
            _log.warning("macOS 钥匙串不可用：%s", exc)
            return False
        return True

    def _find(self) -> bytes | None:
        result = self._run(
            [self._tool, "find-generic-password", "-s", self._service, "-a", self._account, "-w"],
            None,
        )
        if result.returncode != 0:
            return None
        try:
            key = binascii.unhexlify(result.stdout.strip())
        except binascii.Error as exc:
            raise KeyProtectionError("钥匙串里的 GelInspect 主密钥格式不对") from exc
        if len(key) != 32:
            raise KeyProtectionError("钥匙串里的 GelInspect 主密钥长度不对")
        return key

    def _master(self, *, create: bool) -> bytes:
        if self._cached is not None:
            return self._cached
        key = self._find()
        if key is None:
            if not create:
                raise KeyProtectionError(
                    "钥匙串里没有 GelInspect 主密钥（或无法访问钥匙串），无法解密。"
                    "请在原来的 macOS 用户下运行，或从备份恢复钥匙串"
                )
            fresh = secrets.token_bytes(32)
            # 用交互模式从标准输入传命令，主密钥不出现在进程参数里。
            command = (
                f"add-generic-password -U -s {self._service} -a {self._account} "
                f"-l GelInspect -w {fresh.hex()}\n"
            )
            self._run([self._tool, "-i"], command)
            key = self._find()
            if key != fresh:
                raise KeyProtectionError("无法把主密钥写入 macOS 钥匙串（可能被拒绝访问）")
        self._cached = key
        return key

    def protect(self, key_pem: bytes) -> bytes:
        """加密。

        Args:
            key_pem: 明文。

        Returns:
            ``GIKP1:keychain:`` + AES-GCM 密文。

        Raises:
            KeyProtectionError: 无法访问钥匙串。
        """
        header = _header(self.kind)
        return header + _aes_seal(self._master(create=True), key_pem, header)

    def unprotect(self, data: bytes) -> bytes:
        """解密。

        Args:
            data: 磁盘上的内容。

        Returns:
            明文。

        Raises:
            KeyProtectionError: 不是本方式的数据、钥匙串里没有主密钥、密钥不匹配或数据被改动。
        """
        payload = _split(data, self.kind)
        return _aes_open(self._master(create=False), payload, _header(self.kind))


def keychain_account(keys_dir: Path) -> str:
    """一个数据目录对应的钥匙串账户名：同一台机器上的多个数据目录互不干扰。

    Args:
        keys_dir: 密钥目录。

    Returns:
        ``data-`` 加路径 SHA-256 的前 16 位。
    """
    digest = hashlib.sha256(str(keys_dir.resolve()).encode("utf-8")).hexdigest()
    return f"data-{digest[:16]}"


# ---------------------------------------------------------------------- Windows DPAPI


_DPAPI_ENTROPY = b"GelInspect key protection v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DpapiKeyProtector:
    """Windows DPAPI（``CryptProtectData``），绑定当前 Windows 用户。"""

    kind = "dpapi"

    @staticmethod
    def available() -> bool:
        """是否在 Windows 上。

        Returns:
            Windows 时为 True。
        """
        return sys.platform == "win32"

    def protect(self, key_pem: bytes) -> bytes:
        """加密。

        Args:
            key_pem: 明文。

        Returns:
            ``GIKP1:dpapi:`` + DPAPI 密文。

        Raises:
            KeyProtectionError: 不在 Windows 上或调用失败。
        """
        return _header(self.kind) + _dpapi(key_pem, protect=True)

    def unprotect(self, data: bytes) -> bytes:
        """解密。

        Args:
            data: 磁盘上的内容。

        Returns:
            明文。

        Raises:
            KeyProtectionError: 不在 Windows 上、换了用户或电脑、数据被改动。
        """
        return _dpapi(_split(data, self.kind), protect=False)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """调用 ``CryptProtectData`` 或 ``CryptUnprotectData``。"""
    if sys.platform != "win32":
        raise KeyProtectionError("DPAPI 只在 Windows 上可用")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source, _keep = blob(data)
    entropy, _keep_entropy = blob(_DPAPI_ENTROPY)
    output = DataBlob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    if not ok:
        code = ctypes.get_last_error()
        action = "加密" if protect else "解密（可能换了 Windows 用户或电脑，或数据被改动）"
        raise KeyProtectionError(f"DPAPI {action}失败，错误码 {code}")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


# ---------------------------------------------------------------------- 组合与选择


class MultiKeyProtector:
    """用首选方式加密；解密时按文件头选择对应方式，所以换过保护方式的旧文件也能读出。"""

    def __init__(self, primary: KeyProtector, readers: Mapping[str, KeyProtector]) -> None:
        """组合。

        Args:
            primary: 加密用的方式（有 ``kind`` 属性）。
            readers: 种类到解密方式的映射。
        """
        self._primary = primary
        self._readers = dict(readers)

    @property
    def kind(self) -> str:
        """加密用的方式。"""
        return str(getattr(self._primary, "kind", "unknown"))

    def protect(self, key_pem: bytes) -> bytes:
        """用首选方式加密。

        Args:
            key_pem: 明文。

        Returns:
            受保护的数据。
        """
        return self._primary.protect(key_pem)

    def unprotect(self, data: bytes) -> bytes:
        """按文件头解密。

        Args:
            data: 磁盘上的内容。

        Returns:
            明文。

        Raises:
            KeyProtectionError: 不是受保护的格式，或对应的方式无法解密。
        """
        kind = protected_kind(data)
        if kind is None:
            raise KeyProtectionError("数据不是受保护的格式（可能是旧版本留下的明文）")
        reader = self._readers.get(kind)
        if reader is None:
            raise KeyProtectionError(f"不支持的保护方式：{kind}")
        return reader.unprotect(data)

    def needs_upgrade(self, data: bytes) -> bool:
        """文件是否用的不是首选方式（可以重新保护）。

        Args:
            data: 磁盘上的内容。

        Returns:
            需要重新保护时为 True。
        """
        return protected_kind(data) != self.kind


def create_key_protector(
    keys_dir: Path,
    mode: ProtectionMode = "auto",
    *,
    runner: CommandRunner | None = None,
    platform: str | None = None,
) -> MultiKeyProtector:
    """按平台和配置选择密钥保护方式。

    Args:
        keys_dir: 密钥目录（安装密钥放在这里；钥匙串账户名由它派生）。
        mode: ``auto``、``os`` 或 ``file``。
        runner: 执行命令的函数，测试注入假的 ``security``。
        platform: 平台名（``sys.platform`` 的取值），测试注入。

    Returns:
        组合后的保护器：加密用选中的方式，解密支持全部方式。

    Raises:
        KeyProtectionError: ``mode`` 为 ``os`` 而系统凭据不可用。
    """
    current = platform or sys.platform
    file_protector = FileKeyProtector(keys_dir / INSTALL_SECRET_FILE)
    keychain = KeychainKeyProtector(keychain_account(keys_dir), runner=runner)
    dpapi = DpapiKeyProtector()
    readers: dict[str, KeyProtector] = {
        file_protector.kind: file_protector,
        keychain.kind: keychain,
        dpapi.kind: dpapi,
    }
    primary: KeyProtector = file_protector
    if mode != "file":
        system: KeyProtector | None = None
        if current == "win32":
            system = dpapi
        elif current == "darwin" and (runner is not None or shutil.which("security")):
            system = keychain if keychain.ready() else None
        if system is not None:
            primary = system
        elif mode == "os":
            raise KeyProtectionError(
                "配置要求用系统凭据保护密钥（security.key_protection: os），"
                "但当前系统不可用（不是 Windows，或无法访问 macOS 钥匙串）"
            )
        else:
            _log.warning("系统凭据不可用，密钥改用安装密钥派生的口令加密（风险见安全文档）")
    return MultiKeyProtector(primary, readers)


# ---------------------------------------------------------------------- 文件工具


def write_private_file(path: Path, data: bytes) -> None:
    """原子地写一个只有本用户可读的文件（POSIX 600；目录 700）。

    Args:
        path: 目标文件。
        data: 内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if os.name == "posix":
        os.chmod(path, 0o600)


def overwrite_file(path: Path) -> None:
    """删除前用随机数据覆盖文件内容并落盘（“安全删除”的尽力版本）。

    SSD 的磨损均衡和 APFS、NTFS 的写时复制、快照都可能保留旧数据块，所以这只能防止
    简单的恢复工具，不能保证物理上不可恢复。

    Args:
        path: 要覆盖的文件，不存在时什么都不做。
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    with path.open("r+b") as handle:
        handle.write(secrets.token_bytes(size))
        handle.flush()
        os.fsync(handle.fileno())


def encode_secret(secret: bytes) -> str:
    """把密钥编码成 JSON 可存的文字。

    Args:
        secret: 密钥。

    Returns:
        Base64 文字。
    """
    return base64.b64encode(secret).decode("ascii")


def decode_secret(text: str) -> bytes:
    """:func:`encode_secret` 的逆运算。

    Args:
        text: Base64 文字。

    Returns:
        密钥。

    Raises:
        KeyProtectionError: 格式不对。
    """
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise KeyProtectionError("密钥文件内容格式不对") from exc
