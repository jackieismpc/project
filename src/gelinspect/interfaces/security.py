"""密钥保护接口：报告签名私钥、审计链 HMAC 密钥落盘前后的加密与解密。

实现位于 ``gelinspect.infrastructure.security``：Windows 用 DPAPI，macOS 用钥匙串，
都不可用时用安装密钥派生的口令加密。服务层只依赖这里的协议，不关心具体用哪一种。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class KeyProtectionError(Exception):
    """密钥无法保护或还原：系统凭据不可用、密钥不匹配或数据损坏。

    信息面向部署人员，不包含任何密钥内容。
    """


@runtime_checkable
class KeyProtector(Protocol):
    """私密数据落盘前后的保护。"""

    def protect(self, key_pem: bytes) -> bytes:
        """落盘前处理私密数据（例如加密）。

        Args:
            key_pem: 明文数据，例如私钥 PEM。

        Returns:
            写入磁盘的字节。
        """
        ...

    def unprotect(self, data: bytes) -> bytes:
        """读盘后还原私密数据。

        Args:
            data: 磁盘上的内容。

        Returns:
            明文数据。
        """
        ...
