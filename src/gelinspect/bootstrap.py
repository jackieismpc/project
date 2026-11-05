"""首次启动的账号初始化。

组装根的一部分：这里同时知道数据库和服务层。演示账号由配置引用的钩子创建
（通常在 ``simulation/``），真实配置没有钩子，所以数据库里不会出现任何默认账号。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.infrastructure.db import Database, seed_builtin_templates, seed_defaults
from gelinspect.infrastructure.db.passwords import hash_password, needs_rehash, verify_password
from gelinspect.infrastructure.db.seed import SYSTEM_ACTOR
from gelinspect.interfaces.bootstrap import BootstrapHook
from gelinspect.interfaces.storage import DuplicateError
from gelinspect.services.config import AppConfig, load_backend

_log = logging.getLogger(__name__)


class Argon2Hasher:
    """``PasswordHasher`` 的 argon2 实现。"""

    def hash(self, password: str) -> str:
        """计算哈希。

        Args:
            password: 明文密码。

        Returns:
            哈希字符串。
        """
        return hash_password(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """校验密码。

        Args:
            password_hash: 存储的哈希。
            password: 明文密码。

        Returns:
            匹配时为 True。
        """
        return verify_password(password_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        """哈希参数是否过时。

        Args:
            password_hash: 存储的哈希。

        Returns:
            需要重新哈希时为 True。
        """
        return needs_rehash(password_hash)


class DatabaseProvisioner:
    """``AccountProvisioner`` 的数据库实现，供初始化钩子创建账号。"""

    def __init__(self, database: Database) -> None:
        """绑定数据库。

        Args:
            database: 已打开的数据库。
        """
        self._database = database

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: Role,
        password: str,
        must_change_password: bool = False,
    ) -> bool:
        """创建账号并写审计，同名账号已存在时不覆盖。

        Args:
            username: 登录名。
            display_name: 显示名称。
            role: 角色。
            password: 明文密码。
            must_change_password: 首次登录是否必须改密。

        Returns:
            新建时为 True。
        """
        user = User(
            user_id=uuid.uuid4().hex, username=username, display_name=display_name, role=role
        )
        try:
            self._database.users.add(
                user,
                password_hash=hash_password(password),
                must_change_password=must_change_password,
            )
        except DuplicateError:
            return False
        self._database.audit.append(
            AuditEvent(
                occurred_at=datetime.now(UTC),
                actor=SYSTEM_ACTOR,
                actor_role=None,
                action=AuditAction.USER_CHANGED,
                detail=f"初始化账号 {username}（{role.value}）",
            )
        )
        return True


class DatabaseFirstAdminSeeder:
    """``FirstAdminSeeder`` 的数据库实现：调用 ``seed_defaults``，管理员首次登录须改密。"""

    def __init__(self, database: Database) -> None:
        """绑定数据库。

        Args:
            database: 已打开的数据库。
        """
        self._database = database

    def seed_first_admin(self, *, username: str, display_name: str, password: str) -> None:
        """创建管理员和内置模板。

        Args:
            username: 登录名。
            display_name: 显示名称。
            password: 初始密码。
        """
        seed_defaults(
            self._database,
            admin_password=password,
            admin_username=username,
            admin_display_name=display_name,
        )


def load_bootstrap_hook(config: AppConfig) -> BootstrapHook | None:
    """按配置加载初始化钩子。

    Args:
        config: 应用配置。

    Returns:
        钩子；配置没有写 ``bootstrap.hook`` 时为 None。

    Raises:
        ConfigError: 钩子无法加载（例如 ``simulation/`` 缺失）。
    """
    spec = config.bootstrap.hook
    if not spec:
        return None
    return load_backend(
        "bootstrap.hook",
        spec,
        BootstrapHook,  # type: ignore[type-abstract]
        config.bootstrap.options,
    )


def initialize_accounts(database: Database, hook: BootstrapHook | None) -> bool:
    """补全内置模板；首次启动（还没有任何账号）时运行钩子创建账号。

    可重复调用：已有账号时不再运行钩子，模板只补缺失的。

    Args:
        database: 已打开的数据库。
        hook: 初始化钩子，没有时不创建任何账号。

    Returns:
        本次是否为首次启动。
    """
    first_run = not database.users.list_all()
    added = seed_builtin_templates(database)
    if added:
        _log.info("已写入内置模板：%s", ", ".join(added))
    if first_run and hook is not None:
        hook.initialize(DatabaseProvisioner(database))
        _log.info("首次启动：已运行初始化钩子 %s", type(hook).__name__)
    return first_run
