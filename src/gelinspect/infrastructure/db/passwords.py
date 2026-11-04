"""密码哈希：argon2id。哈希参数使用 argon2-cffi 的默认值。"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """计算密码的 argon2 哈希。

    Args:
        password: 明文密码。

    Returns:
        带参数和盐的哈希字符串。
    """
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验密码。

    Args:
        password_hash: 存储的哈希。
        password: 待校验的明文密码。

    Returns:
        匹配返回 True；不匹配或哈希格式无效返回 False。
    """
    try:
        return _HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """哈希参数低于当前默认值时返回 True，登录成功后可据此重新哈希。"""
    return _HASHER.check_needs_rehash(password_hash)
