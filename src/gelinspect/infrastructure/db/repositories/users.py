"""用户仓储。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gelinspect.domain.entities import User
from gelinspect.domain.enums import Role
from gelinspect.infrastructure.db.models import UserRow
from gelinspect.infrastructure.db.repositories._common import (
    SessionProvider,
    translate_lock_errors,
)
from gelinspect.interfaces.storage import (
    DuplicateError,
    NotFoundError,
    UserCredentials,
    UserRepository,
)


def _to_user(row: UserRow) -> User:
    """ORM 行转领域用户，不带密码哈希。"""
    return User(
        user_id=row.user_id,
        username=row.username,
        display_name=row.display_name,
        role=Role(row.role),
        active=row.active,
    )


class SqlUserRepository(UserRepository):
    """基于 SQLAlchemy 的用户仓储。"""

    def __init__(self, sessions: SessionProvider) -> None:
        """记录会话提供者。

        Args:
            sessions: 返回事务会话上下文的可调用对象。
        """
        self._sessions = sessions

    def add(self, user: User, *, password_hash: str, must_change_password: bool) -> None:
        """新增用户。

        Args:
            user: 用户。
            password_hash: argon2 密码哈希。
            must_change_password: 首次登录是否必须改密。

        Raises:
            DuplicateError: 用户标识或登录名已存在。
            StorageBusyError: 等写锁超时，什么都没有写入。
        """
        try:
            with translate_lock_errors(), self._sessions() as session:
                session.add(
                    UserRow(
                        user_id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        role=user.role.value,
                        active=user.active,
                        password_hash=password_hash,
                        must_change_password=must_change_password,
                        created_at=datetime.now(UTC),
                    )
                )
        except IntegrityError as error:
            raise DuplicateError(f"用户已存在：{user.username}") from error

    def get(self, user_id: str) -> User | None:
        """按标识查询用户。

        Args:
            user_id: 用户标识。

        Returns:
            用户，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.get(UserRow, user_id)
            return None if row is None else _to_user(row)

    def get_by_username(self, username: str) -> User | None:
        """按登录名查询用户。

        Args:
            username: 登录名。

        Returns:
            用户，不存在时为 None。
        """
        credentials = self.get_credentials(username)
        return None if credentials is None else credentials.user

    def get_credentials(self, username: str) -> UserCredentials | None:
        """按登录名查询登录校验信息。

        Args:
            username: 登录名。

        Returns:
            用户及密码哈希，不存在时为 None。
        """
        with self._sessions() as session:
            row = session.execute(
                select(UserRow).where(UserRow.username == username)
            ).scalar_one_or_none()
            if row is None:
                return None
            return UserCredentials(
                user=_to_user(row),
                password_hash=row.password_hash,
                must_change_password=row.must_change_password,
            )

    def list_all(self) -> list[User]:
        """列出全部用户，按登录名排序。

        Returns:
            用户列表。
        """
        with self._sessions() as session:
            rows = session.execute(select(UserRow).order_by(UserRow.username)).scalars()
            return [_to_user(row) for row in rows]

    def update(self, user: User) -> None:
        """更新显示名称、角色和启用状态。

        Args:
            user: 用户，按 ``user_id`` 定位。

        Raises:
            NotFoundError: 用户不存在。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(UserRow, user.user_id)
            if row is None:
                raise NotFoundError(f"用户不存在：{user.user_id}")
            row.display_name = user.display_name
            row.role = user.role.value
            row.active = user.active

    def set_password_hash(
        self, user_id: str, password_hash: str, *, must_change_password: bool
    ) -> None:
        """更新密码哈希和改密标记。

        Args:
            user_id: 用户标识。
            password_hash: 新的 argon2 哈希。
            must_change_password: 下次登录是否必须改密。

        Raises:
            NotFoundError: 用户不存在。
            StorageBusyError: 等写锁超时，什么都没有写入。
        """
        with translate_lock_errors(), self._sessions() as session:
            row = session.get(UserRow, user_id)
            if row is None:
                raise NotFoundError(f"用户不存在：{user_id}")
            row.password_hash = password_hash
            row.must_change_password = must_change_password
