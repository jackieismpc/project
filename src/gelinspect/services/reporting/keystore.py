"""签名证书与私钥：本地自签，存放在 ``data/keys/``。

* 第一次需要签名时生成自签证书（默认 ECDSA P-256，可配置 RSA 3072）和私钥。主题（CN、O）、
  有效期来自配置。证书在软件里是「本机信任的那张」，也是验签时唯一的信任根。
* 文件被 ``.gitignore``（``*.pem``、``*.key``）忽略。私钥文件权限收紧为 600（目录 700）；
  Windows 没有等价的 POSIX 权限，只能依赖用户目录的访问控制，部署文档里说明。
* **私钥目前以明文 PEM 保存**，加密留给 P13：:class:`KeyProtector` 是预留的接口，
  P13 提供实现（口令或系统凭据保护）后注入 :class:`LocalKeyStore` 即可，调用方不用改。
* 私钥永远不进日志、审计和异常信息；日志里只出现证书指纹。
* 这是自签证书：它只能证明「文件自签署后没有被改动，且由本机的这把密钥签署」，
  **不具有第三方 CA 的法律效力**。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

_log = logging.getLogger(__name__)

KEY_FILE = "report-signing.key"
"""私钥文件名（PEM，PKCS#8）。"""
CERT_FILE = "report-signing.pem"
"""证书文件名（PEM）。"""

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey


class KeyStoreError(Exception):
    """证书或私钥无法使用：文件损坏、不成对，或有证书没有私钥。信息面向部署人员。"""


class KeyProtector(Protocol):
    """私钥落盘前后的保护，P13 提供加密实现。"""

    def protect(self, key_pem: bytes) -> bytes:
        """落盘前处理私钥 PEM（例如加密）。"""
        ...

    def unprotect(self, data: bytes) -> bytes:
        """读盘后还原私钥 PEM。"""
        ...


class PlainKeyProtector:
    """不做保护：明文保存。P13 之前的默认实现。"""

    def protect(self, key_pem: bytes) -> bytes:
        """原样返回。

        Args:
            key_pem: 私钥 PEM。

        Returns:
            同一份字节。
        """
        return key_pem

    def unprotect(self, data: bytes) -> bytes:
        """原样返回。

        Args:
            data: 磁盘上的内容。

        Returns:
            同一份字节。
        """
        return data


@dataclass(frozen=True, slots=True)
class SigningIdentity:
    """签名身份：证书和私钥。

    Attributes:
        certificate: 自签证书。
        private_key: 私钥（不参与 ``repr``，避免被打进日志）。
    """

    certificate: x509.Certificate
    private_key: PrivateKey = field(repr=False)

    @property
    def fingerprint(self) -> str:
        """证书的 SHA-256 指纹（DER，小写十六进制）。"""
        return certificate_fingerprint(self.certificate)

    @property
    def subject(self) -> str:
        """证书主题的可读文字。"""
        return certificate_subject(self.certificate)


def _public_der(public_key: object) -> bytes:
    """公钥的 SubjectPublicKeyInfo DER，用来比较证书和私钥是否成对。"""
    assert isinstance(public_key, ec.EllipticCurvePublicKey | rsa.RSAPublicKey)
    return public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def certificate_fingerprint(certificate: x509.Certificate) -> str:
    """证书的 SHA-256 指纹。

    Args:
        certificate: 证书。

    Returns:
        DER 编码的 SHA-256，64 位小写十六进制。
    """
    return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


def certificate_subject(certificate: x509.Certificate) -> str:
    """证书主题的可读文字，例如 ``GelInspect Report Signing（GelInspect）``。

    Args:
        certificate: 证书。

    Returns:
        通用名，有组织名时附在括号里。
    """

    def first(oid: x509.ObjectIdentifier) -> str:
        values = certificate.subject.get_attributes_for_oid(oid)
        return str(values[0].value) if values else ""

    common = first(NameOID.COMMON_NAME)
    organization = first(NameOID.ORGANIZATION_NAME)
    return f"{common}（{organization}）" if organization else common


class KeyStore(Protocol):
    """签名身份的存取。"""

    def load_or_create(self) -> SigningIdentity:
        """取得签名身份；第一次使用时生成。"""
        ...

    def trusted_certificate(self) -> x509.Certificate | None:
        """本机信任的证书（只读公开部分），还没有生成过时为 None；不会生成新证书。"""
        ...


class LocalKeyStore:
    """把自签证书和私钥放在一个目录里。线程安全。"""

    def __init__(
        self,
        directory: Path,
        *,
        common_name: str,
        organization: str,
        valid_days: int = 3650,
        algorithm: Literal["ecdsa", "rsa"] = "ecdsa",
        protector: KeyProtector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建密钥库（不读写磁盘，第一次使用时才生成）。

        Args:
            directory: 存放证书和私钥的目录。
            common_name: 证书通用名（来自配置）。
            organization: 证书组织名（来自配置）。
            valid_days: 证书有效天数。
            algorithm: 密钥算法。
            protector: 私钥保护，默认明文（P13 换成加密）。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._dir = directory
        self._common_name = common_name
        self._organization = organization
        self._valid_days = valid_days
        self._algorithm = algorithm
        self._protector: KeyProtector = protector or PlainKeyProtector()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    @property
    def key_path(self) -> Path:
        """私钥文件路径。"""
        return self._dir / KEY_FILE

    @property
    def certificate_path(self) -> Path:
        """证书文件路径。"""
        return self._dir / CERT_FILE

    def trusted_certificate(self) -> x509.Certificate | None:
        """读取本机证书，不生成。

        Returns:
            证书；还没有生成过时为 None。

        Raises:
            KeyStoreError: 证书文件损坏。
        """
        with self._lock:
            if not self.certificate_path.is_file():
                return None
            return self._read_certificate()

    def load_or_create(self) -> SigningIdentity:
        """取得签名身份：两个文件都在就加载，都没有就生成，只剩证书时报错。

        只有私钥（证书丢失）时用同一把私钥重新签发证书。

        Returns:
            签名身份。

        Raises:
            KeyStoreError: 证书存在但私钥丢失，或文件损坏、证书与私钥不成对。
        """
        with self._lock:
            has_key = self.key_path.is_file()
            has_cert = self.certificate_path.is_file()
            if has_cert and not has_key:
                raise KeyStoreError(
                    f"找到证书 {self.certificate_path.name} 但缺少私钥 {self.key_path.name}，"
                    "无法继续签署。已签署的报告仍可验签；请恢复私钥文件，"
                    "或在确认后移走旧证书让系统重新生成（旧报告将不再被本机信任）。"
                )
            if not has_key:
                return self._generate()
            key = self._read_key()
            if not has_cert:
                _log.warning("缺少证书文件，用现有私钥重新签发")
                certificate = self._issue(key)
                self._write_atomic(self.certificate_path, self._pem_certificate(certificate))
                return SigningIdentity(certificate, key)
            certificate = self._read_certificate()
            if _public_der(key.public_key()) != _public_der(certificate.public_key()):
                raise KeyStoreError("证书与私钥不成对，请检查 data/keys/ 目录")
            self._tighten(self.key_path)
            return SigningIdentity(certificate, key)

    # ------------------------------------------------------------------ 内部

    def _generate(self) -> SigningIdentity:
        key: PrivateKey
        if self._algorithm == "rsa":
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
        certificate = self._issue(key)
        self._dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self._dir, 0o700)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        # 先写私钥再写证书：中途失败只会留下「只有私钥」，下次自动补发证书。
        self._write_atomic(self.key_path, self._protector.protect(key_pem))
        self._write_atomic(self.certificate_path, self._pem_certificate(certificate))
        identity = SigningIdentity(certificate, key)
        _log.info("已生成本地签名证书，指纹 %s", identity.fingerprint)
        return identity

    def _issue(self, key: PrivateKey) -> x509.Certificate:
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, self._common_name),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, self._organization),
            ]
        )
        now = self._clock()
        public = key.public_key()
        return (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=self._valid_days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=True,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
            .sign(key, hashes.SHA256())
        )

    @staticmethod
    def _pem_certificate(certificate: x509.Certificate) -> bytes:
        return certificate.public_bytes(serialization.Encoding.PEM)

    def _read_certificate(self) -> x509.Certificate:
        try:
            return x509.load_pem_x509_certificate(self.certificate_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise KeyStoreError(f"无法读取证书 {self.certificate_path.name}：{exc}") from exc

    def _read_key(self) -> PrivateKey:
        try:
            pem = self._protector.unprotect(self.key_path.read_bytes())
            key = serialization.load_pem_private_key(pem, password=None)
        except (OSError, ValueError, TypeError) as exc:
            # 不把异常原文带出去：它可能包含密钥内容的片段。
            raise KeyStoreError(f"无法读取私钥 {self.key_path.name}（文件损坏或受保护）") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey):
            raise KeyStoreError("私钥类型不受支持，只支持 ECDSA 和 RSA")
        return key

    @staticmethod
    def _tighten(path: Path) -> None:
        """POSIX 下把文件权限收紧为 600。"""
        if os.name != "posix":
            return
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            _log.warning("%s 的权限过宽（%o），已收紧为 600", path.name, mode)
            os.chmod(path, 0o600)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """先写同目录临时文件（权限 600）再原子替换，不会留下写了一半的文件。"""
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
