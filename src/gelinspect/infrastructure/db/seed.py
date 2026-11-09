"""初始数据：内置模板与默认管理员。

默认管理员的密码**必须由调用方传入**，本模块不含任何可用的默认密码。
管理员默认不要求首次登录改密：密码是调用方（例如“创建管理员”页上的管理员本人）自己设的；
需要强制改密的场景（别人代为设置初始密码）传 ``must_change_password=True``。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.templates import ALL_BUILTIN_TEMPLATES
from gelinspect.infrastructure.db.database import Database
from gelinspect.infrastructure.db.passwords import hash_password

MIN_PASSWORD_LENGTH = 8
"""初始管理员密码的最短长度。"""

SYSTEM_ACTOR = "system"
"""系统事件的操作者。"""


@dataclass(frozen=True, slots=True)
class SeedResult:
    """初始化结果。

    Attributes:
        admin_created: 本次是否新建了管理员。
        templates_added: 本次新增的内置模板标识。
    """

    admin_created: bool
    templates_added: tuple[str, ...]


def seed_builtin_templates(database: Database) -> tuple[str, ...]:
    """写入缺失的内置模板，并把内容与程序定义不同的内置模板更新为程序定义。可重复调用。

    内置模板只读，内容以程序为准；软件升级改了内置布局时，启动后自动更新。已创建的试验保存的是
    当时的管位快照，不受影响。

    Args:
        database: 已打开的数据库。

    Returns:
        本次新增或更新的内置模板标识。
    """
    changed: list[str] = []
    for template in ALL_BUILTIN_TEMPLATES:
        existing = database.templates.get(template.template_id)
        if existing == template:
            continue
        database.templates.save(template, builtin=True)
        changed.append(template.template_id)
        verb = "初始化" if existing is None else "更新"
        database.audit.append(
            AuditEvent(
                occurred_at=datetime.now(UTC),
                actor=SYSTEM_ACTOR,
                actor_role=None,
                action=AuditAction.TEMPLATE_CHANGED,
                detail=f"{verb}内置模板 {template.template_id}",
            )
        )
    return tuple(changed)


def seed_defaults(
    database: Database,
    *,
    admin_password: str,
    admin_username: str = "admin",
    admin_display_name: str = "系统管理员",
    must_change_password: bool = False,
) -> SeedResult:
    """写入内置模板和默认管理员，可重复调用。

    已存在的内置模板和同名用户不会被覆盖，也不会重置已有管理员的密码。
    每个新增项都写一条审计事件。

    Args:
        database: 已打开的数据库。
        admin_password: 管理员初始密码，由调用方提供，不少于 8 个字符。
        admin_username: 管理员登录名。
        admin_display_name: 管理员显示名称。
        must_change_password: 管理员首次登录是否必须改密。密码由管理员自己设置时为 False
            （默认），由他人代设的初始密码应为 True。

    Returns:
        本次新增的内容。

    Raises:
        ValueError: 密码过短或登录名为空。
    """
    if len(admin_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"管理员密码不能少于 {MIN_PASSWORD_LENGTH} 个字符")
    if not admin_username.strip():
        raise ValueError("管理员登录名不能为空")

    templates_added = seed_builtin_templates(database)

    admin_created = database.users.get_by_username(admin_username) is None
    if admin_created:
        database.users.add(
            User(
                user_id=uuid.uuid4().hex,
                username=admin_username,
                display_name=admin_display_name,
                role=Role.ADMIN,
            ),
            password_hash=hash_password(admin_password),
            must_change_password=must_change_password,
        )
        database.audit.append(
            AuditEvent(
                occurred_at=datetime.now(UTC),
                actor=SYSTEM_ACTOR,
                actor_role=None,
                action=AuditAction.USER_CHANGED,
                detail=(
                    f"初始化管理员 {admin_username}"
                    + ("，首次登录须修改密码" if must_change_password else "")
                ),
            )
        )
    return SeedResult(admin_created=admin_created, templates_added=templates_added)
