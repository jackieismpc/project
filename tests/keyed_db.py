"""测试工具：按数据目录里的审计链密钥打开数据库（应用启动后审计链已改用 HMAC）。"""

from pathlib import Path

from gelinspect.infrastructure.db import Database, open_database
from gelinspect.infrastructure.security.audit_keys import read_chain_keys
from gelinspect.infrastructure.security.protectors import create_key_protector


def open_keyed(data_dir: Path) -> Database:
    """打开 ``data_dir`` 下的数据库，并带上 ``data_dir/keys`` 里的审计链密钥。"""
    keys_dir = data_dir / "keys"
    keys = read_chain_keys(keys_dir, create_key_protector(keys_dir, "file"))
    return open_database(data_dir / "gelinspect.db", chain_keys=keys)
