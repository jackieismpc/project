"""用户与权限管理：列表、新建、停用和启用、重置密码、改角色。

权限：全部操作需要 ``user_manage``（管理员），服务层每次都重新校验。规则：

* **不能停用自己**；**不能停用或降级最后一个启用的管理员**，否则系统里再没有人能管理用户。
  检查和写入在同一个事务里，不会被并发的另一次操作绕过。
* 新建账号和重置密码都会把“首次登录必须改密”置位；明文临时密码只在返回值里出现一次，
  不写日志，也不写审计。
* 改角色、停用、重置密码后，该账号**已登录的会话立即采用新角色或失效**，不用等下次登录。
* 每次改动和它的审计事件（``user_changed``）在同一个事务里提交，审计写不进去就不会生效。
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import (
    DuplicateError,
    NotFoundError,
    UnitOfWork,
    UserRepository,
)
from gelinspect.services.auth import (
    AuthErrorCode,
    AuthService,
    PasswordPolicyError,
    Session,
    validate_username,
)
from gelinspect.services.errors import ServiceError, ServiceErrorCode

_ROLE_NAMES: dict[Role, str] = {
    Role.ADMIN: "管理员",
    Role.EXPERIMENTER: "实验员",
    Role.ANALYST: "分析员",
}

MAX_DISPLAY_NAME_LENGTH = 40
"""显示名称最大长度。"""

_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
"""临时密码的字符集，去掉了容易看错的 0 O 1 l I。"""

TEMPORARY_PASSWORD_LENGTH = 12
"""生成的临时密码长度。"""


class UserStorage(Protocol):
    """用户服务需要的存储。"""

    @property
    def users(self) -> UserRepository:
        """用户仓储（读）。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元：用户的改动和审计在同一事务提交。"""
        ...


@dataclass(frozen=True, slots=True)
class UserRecord:
    """用户列表里的一行。

    Attributes:
        user: 用户。
        must_change_password: 下次登录是否必须先改密（新建或重置之后还没改）。
    """

    user: User
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class CredentialIssue:
    """新建账号或重置密码的结果。

    Attributes:
        user: 用户。
        temporary_password: 临时密码明文，只在这里出现一次，管理员当面告知用户。
    """

    user: User
    temporary_password: str


def generate_temporary_password() -> str:
    """生成随机的临时密码。

    Returns:
        :data:`TEMPORARY_PASSWORD_LENGTH` 位，字母和数字混合。
    """
    while True:
        password = "".join(
            secrets.choice(_PASSWORD_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH)
        )
        if any(c.isdigit() for c in password) and any(c.isalpha() for c in password):
            return password


class UserService:
    """用户与权限管理服务。"""

    def __init__(
        self,
        auth: AuthService,
        storage: UserStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务：校验权限、计算密码哈希、刷新会话。
            storage: 存储。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ 查询

    def list_users(self, session: Session | None) -> list[UserRecord]:
        """列出全部用户，按登录名排序。

        Args:
            session: 调用者的会话，需要 ``user_manage`` 权限。

        Returns:
            用户列表。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.USER_MANAGE)
        repo = self._storage.users
        records: list[UserRecord] = []
        for user in repo.list_all():
            credentials = repo.get_credentials(user.username)
            records.append(
                UserRecord(user, credentials is not None and credentials.must_change_password)
            )
        return records

    # ------------------------------------------------------------------ 新建

    def create_user(
        self,
        session: Session | None,
        *,
        username: str,
        display_name: str,
        role: Role,
        password: str | None = None,
    ) -> CredentialIssue:
        """新建账号，首次登录必须改密。

        Args:
            session: 调用者的会话，需要 ``user_manage`` 权限。
            username: 登录名。
            display_name: 显示名称，留空则用登录名。
            role: 角色。
            password: 初始密码；为 None 时生成随机临时密码。

        Returns:
            新用户和临时密码。

        Raises:
            PermissionDeniedError: 没有权限。
            PasswordPolicyError: 登录名或密码不合要求。
            ServiceError: 登录名已存在，或显示名称过长。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        actor = self._auth.authorize(session, Permission.USER_MANAGE)
        name = validate_username(username)
        shown = display_name.strip() or name
        if len(shown) > MAX_DISPLAY_NAME_LENGTH:
            raise ServiceError(
                f"显示名称不能超过 {MAX_DISPLAY_NAME_LENGTH} 个字符",
                ServiceErrorCode.USER_NAME_REQUIRED,
                max=MAX_DISPLAY_NAME_LENGTH,
            )
        initial = self._checked_password(password, name)
        user = User(user_id=uuid.uuid4().hex, username=name, display_name=shown, role=role)
        event = self._event(actor, f"新建用户 {name}（{_ROLE_NAMES[role]}），首次登录须改密")
        try:
            with self._storage.unit_of_work() as uow:
                if uow.users.get_by_username(name) is not None:
                    raise DuplicateError(name)
                uow.users.add(
                    user, password_hash=self._auth.hash_password(initial), must_change_password=True
                )
                uow.audit.append(event)
        except DuplicateError as error:
            raise ServiceError(
                f"登录名已存在：{name}", ServiceErrorCode.USER_DUPLICATE, name=name
            ) from error
        return CredentialIssue(user, initial)

    # ------------------------------------------------------------------ 停用、启用、改角色

    def set_active(self, session: Session | None, user_id: str, active: bool) -> User:
        """停用或启用账号。停用后该账号已登录的会话立即失效。

        Args:
            session: 调用者的会话，需要 ``user_manage`` 权限。
            user_id: 目标用户。
            active: True 为启用，False 为停用。

        Returns:
            更新后的用户。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 用户不存在、停用自己、停用最后一个管理员。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        actor = self._auth.authorize(session, Permission.USER_MANAGE)
        if not active and user_id == actor.user_id:
            raise ServiceError("不能停用自己的账号", ServiceErrorCode.USER_SELF_DISABLE)
        with self._storage.unit_of_work() as uow:
            target = self._require(uow.users, user_id)
            if target.active == active:
                return target
            if not active:
                self._require_other_admin(uow.users, target, "停用")
            updated = replace(target, active=active)
            uow.users.update(updated)
            uow.audit.append(
                self._event(actor, f"{'启用' if active else '停用'}用户 {target.username}")
            )
        if not active:
            self._auth.revoke_sessions(user_id)
        else:
            self._auth.refresh_sessions(updated)
        return updated

    def change_role(self, session: Session | None, user_id: str, role: Role) -> User:
        """修改角色。该账号已登录的会话立即采用新角色。

        Args:
            session: 调用者的会话，需要 ``user_manage`` 权限。
            user_id: 目标用户。
            role: 新角色。

        Returns:
            更新后的用户。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 用户不存在，或把最后一个管理员降级。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        actor = self._auth.authorize(session, Permission.USER_MANAGE)
        with self._storage.unit_of_work() as uow:
            target = self._require(uow.users, user_id)
            if target.role is role:
                return target
            if target.role is Role.ADMIN:
                self._require_other_admin(uow.users, target, "降级")
            updated = replace(target, role=role)
            uow.users.update(updated)
            uow.audit.append(
                self._event(
                    actor,
                    f"修改用户 {target.username} 的角色：{_ROLE_NAMES[target.role]} → "
                    f"{_ROLE_NAMES[role]}",
                )
            )
        self._auth.refresh_sessions(updated)
        return updated

    # ------------------------------------------------------------------ 重置密码

    def reset_password(
        self, session: Session | None, user_id: str, password: str | None = None
    ) -> CredentialIssue:
        """重置别人的密码，对方下次登录必须先改密，已登录的会话失效。

        自己的密码用“修改密码”，不能在这里重置。

        Args:
            session: 调用者的会话，需要 ``user_manage`` 权限。
            user_id: 目标用户。
            password: 临时密码；为 None 时生成随机的。

        Returns:
            用户和临时密码。

        Raises:
            PermissionDeniedError: 没有权限。
            PasswordPolicyError: 密码不合要求。
            ServiceError: 用户不存在，或重置自己的密码。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        actor = self._auth.authorize(session, Permission.USER_MANAGE)
        if user_id == actor.user_id:
            raise ServiceError("不能在这里重置自己的密码", ServiceErrorCode.USER_SELF_RESET)
        with self._storage.unit_of_work() as uow:
            target = self._require(uow.users, user_id)
            issued = self._checked_password(password, target.username)
            uow.users.set_password_hash(
                user_id, self._auth.hash_password(issued), must_change_password=True
            )
            uow.audit.append(
                self._event(actor, f"重置用户 {target.username} 的密码，下次登录须改密")
            )
        self._auth.revoke_sessions(user_id)
        return CredentialIssue(target, issued)

    # ------------------------------------------------------------------ 内部

    def _checked_password(self, password: str | None, username: str) -> str:
        if password is None:
            return generate_temporary_password()
        minimum = self._auth.policy.min_password_length
        if len(password) < minimum:
            raise PasswordPolicyError(
                f"密码不能少于 {minimum} 个字符", AuthErrorCode.PASSWORD_TOO_SHORT, min=minimum
            )
        if password.casefold() == username.casefold():
            raise PasswordPolicyError(
                "密码不能与登录名相同", AuthErrorCode.PASSWORD_SAME_AS_USERNAME
            )
        return password

    @staticmethod
    def _require(repo: UserRepository, user_id: str) -> User:
        try:
            user = repo.get(user_id)
        except NotFoundError:
            user = None
        if user is None:
            raise ServiceError(f"用户不存在：{user_id}", ServiceErrorCode.USER_NOT_FOUND)
        return user

    @staticmethod
    def _require_other_admin(repo: UserRepository, target: User, what: str) -> None:
        """目标是启用中的管理员时，必须还有另一个启用的管理员。"""
        if not (target.role is Role.ADMIN and target.active):
            return
        others = [
            u
            for u in repo.list_all()
            if u.role is Role.ADMIN and u.active and u.user_id != target.user_id
        ]
        if not others:
            raise ServiceError(
                f"不能{what}最后一个管理员，否则没有人能管理用户",
                ServiceErrorCode.USER_LAST_ADMIN,
            )

    def _event(self, actor: User, detail: str) -> AuditEvent:
        return AuditEvent(
            occurred_at=self._clock(),
            actor=actor.username,
            actor_role=actor.role,
            action=AuditAction.USER_CHANGED,
            detail=detail,
        )
