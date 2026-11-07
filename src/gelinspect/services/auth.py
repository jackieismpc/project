"""登录、会话、锁屏、改密与服务层权限校验。

界面只负责禁用入口，真正的拒绝在这里：任何需要权限的服务入口都先调用
:meth:`AuthService.authorize`，所以界面被绕过（直接调用服务）时同样会被拒绝，
并写一条审计事件。

失败次数限制与锁定保存在内存里，重启进程后清零；每一次失败都写审计，可事后追查。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import NoReturn, Protocol

from gelinspect.domain.entities import AuditEvent, User
from gelinspect.domain.enums import AuditAction, Role
from gelinspect.domain.permissions import Permission, has_permission, permissions_of
from gelinspect.interfaces.storage import AuditRepository, StorageBusyError, UserRepository

_log = logging.getLogger(__name__)

MAX_USERNAME_LENGTH = 32
"""登录名最大长度。"""

_MAX_TRACKED_NAMES = 1000
"""内存里最多跟踪多少个登录名的失败记录，防止被无穷多的假用户名撑大。"""


def validate_username(username: str) -> str:
    """检查并规整登录名：不能为空、不能含空白、不超过 :data:`MAX_USERNAME_LENGTH` 个字符。

    Args:
        username: 输入的登录名。

    Returns:
        去掉首尾空白后的登录名。

    Raises:
        PasswordPolicyError: 登录名不合要求。
    """
    name = username.strip()
    if not name or len(name) > MAX_USERNAME_LENGTH or any(ch.isspace() for ch in name):
        raise PasswordPolicyError(
            "登录名不能为空、不能含空白，且不超过 32 个字符",
            AuthErrorCode.USERNAME_INVALID,
            max=MAX_USERNAME_LENGTH,
        )
    return name


class AuthErrorCode(StrEnum):
    """账号错误的分类，界面据此生成当前语言的提示（异常信息本身是简体中文，写审计和日志用）。"""

    INVALID_CREDENTIALS = "invalid_credentials"
    LOCKED = "locked"
    DISABLED = "disabled"
    USERNAME_INVALID = "username_invalid"
    PASSWORD_TOO_SHORT = "password_too_short"
    PASSWORD_SAME_AS_OLD = "password_same_as_old"
    PASSWORD_SAME_AS_USERNAME = "password_same_as_username"
    NOT_LOGGED_IN = "not_logged_in"
    SESSION_INVALID = "session_invalid"
    SCREEN_LOCKED = "screen_locked"
    MUST_CHANGE_PASSWORD = "must_change_password"
    DENIED = "denied"
    ALREADY_INITIALIZED = "already_initialized"
    SETUP_DISABLED = "setup_disabled"
    STORAGE_BUSY = "storage_busy"


class AuthError(Exception):
    """账号相关异常基类。

    Attributes:
        code: 错误分类。
        params: 提示文字里的参数，例如剩余次数、最短密码长度。
    """

    code: AuthErrorCode = AuthErrorCode.DENIED

    def __init__(self, message: str, code: AuthErrorCode | None = None, **params: float) -> None:
        """创建异常。

        Args:
            message: 简体中文说明。
            code: 错误分类，默认取类的默认分类。
            **params: 提示文字里的参数。
        """
        super().__init__(message)
        if code is not None:
            self.code = code
        self.params = params


class InvalidCredentialsError(AuthError):
    """用户名或密码错误。

    Attributes:
        remaining_attempts: 再失败几次会被锁定。
    """

    code = AuthErrorCode.INVALID_CREDENTIALS

    def __init__(self, remaining_attempts: int) -> None:
        """创建异常。

        Args:
            remaining_attempts: 还剩几次尝试机会。
        """
        super().__init__("用户名或密码错误", remaining=remaining_attempts)
        self.remaining_attempts = remaining_attempts


class AccountLockedError(AuthError):
    """失败次数过多，暂时锁定。

    Attributes:
        retry_after_s: 还需等待的秒数。
    """

    code = AuthErrorCode.LOCKED

    def __init__(self, retry_after_s: float) -> None:
        """创建异常。

        Args:
            retry_after_s: 还需等待的秒数。
        """
        super().__init__("登录失败次数过多，账号已暂时锁定", retry_after_s=retry_after_s)
        self.retry_after_s = retry_after_s


class AccountDisabledError(AuthError):
    """账号已停用。"""

    code = AuthErrorCode.DISABLED


class PasswordPolicyError(AuthError):
    """新密码或登录名不符合要求。"""


class PermissionDeniedError(AuthError):
    """没有权限，或会话已失效、已锁屏、必须先改密。"""


class SetupNotAllowedError(AuthError):
    """系统已经初始化，或当前配置不允许创建管理员。"""


class AuthStorageBusyError(AuthError):
    """数据库正忙（等写锁超时），操作没有生效，稍后重试即可。"""

    code = AuthErrorCode.STORAGE_BUSY


class PasswordHasher(Protocol):
    """密码哈希，由组装根注入（具体算法在基础设施层）。"""

    def hash(self, password: str) -> str:
        """计算哈希。"""
        ...

    def verify(self, password_hash: str, password: str) -> bool:
        """校验密码，哈希无效时返回 False。"""
        ...

    def needs_rehash(self, password_hash: str) -> bool:
        """哈希参数是否低于当前默认值。"""
        ...


class FirstAdminSeeder(Protocol):
    """创建首个管理员（并写入内置模板），由组装根注入。"""

    def seed_first_admin(self, *, username: str, display_name: str, password: str) -> None:
        """创建管理员。密码是管理员自己设的，直接生效，首次登录不强制改密。

        Args:
            username: 登录名。
            display_name: 显示名称。
            password: 管理员设置的密码。
        """
        ...


class AuthStorage(Protocol):
    """认证需要的存储：用户和审计。"""

    @property
    def users(self) -> UserRepository:
        """用户仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...


@dataclass(frozen=True, slots=True)
class LoginPolicy:
    """登录与密码策略。

    Attributes:
        max_attempts: 连续失败多少次后锁定。
        lockout_s: 锁定时长，秒。
        min_password_length: 密码最短长度。
    """

    max_attempts: int = 5
    lockout_s: float = 300.0
    min_password_length: int = 8


@dataclass(eq=False, slots=True)
class Session:
    """一次登录会话。

    Attributes:
        user: 登录的用户（登录时的快照）。
        must_change_password: 是否必须先改密，为真时除改密外的操作都被拒绝。
        started_at: 登录时间。
        session_id: 会话标识。
        locked: 是否已锁屏，锁屏时拒绝一切操作。
    """

    user: User
    must_change_password: bool
    started_at: datetime
    session_id: str
    locked: bool = False

    @property
    def role(self) -> Role:
        """用户角色。"""
        return self.user.role

    def permissions(self) -> frozenset[Permission]:
        """会话当前可用的权限，锁屏或必须改密时为空。

        Returns:
            权限集合。
        """
        if self.locked or self.must_change_password:
            return frozenset()
        return permissions_of(self.user.role)

    def can(self, permission: Permission) -> bool:
        """界面用来决定入口是否可用；真正的拒绝在 :meth:`AuthService.authorize`。

        Args:
            permission: 权限。

        Returns:
            可用时为 True。
        """
        return permission in self.permissions()


@dataclass(slots=True)
class _Attempts:
    failures: int = 0
    locked_until: datetime | None = None


class AuthService:
    """登录、退出、锁屏、改密、首个管理员和权限校验。线程安全。"""

    def __init__(
        self,
        storage: AuthStorage,
        hasher: PasswordHasher,
        *,
        policy: LoginPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        seeder: FirstAdminSeeder | None = None,
    ) -> None:
        """创建服务。

        Args:
            storage: 用户和审计存储。
            hasher: 密码哈希。
            policy: 登录策略，默认 5 次失败锁定 5 分钟。
            clock: 返回带时区当前时间的函数，测试注入。
            seeder: 创建首个管理员的实现；没有时“创建管理员”不可用。
        """
        self._storage = storage
        self._hasher = hasher
        self._policy = policy or LoginPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seeder = seeder
        self._lock = threading.RLock()
        self._attempts: OrderedDict[str, _Attempts] = OrderedDict()
        self._sessions: dict[str, Session] = {}
        self._dummy_hash: str | None = None

    @property
    def policy(self) -> LoginPolicy:
        """登录策略。"""
        return self._policy

    # ------------------------------------------------------------------ 登录与退出

    def login(self, username: str, password: str) -> Session:
        """登录。成功和失败都写审计。

        Args:
            username: 登录名。
            password: 明文密码。

        Returns:
            新会话。

        Raises:
            InvalidCredentialsError: 用户名或密码错误。
            AccountLockedError: 失败次数过多，暂时锁定。
            AccountDisabledError: 账号已停用（密码正确时才提示）。
            AuthStorageBusyError: 数据库正忙，没有登录，稍后重试。
        """
        name = username.strip()
        user, must_change = self._verify(name, password, purpose="登录")
        session = Session(
            user=user,
            must_change_password=must_change,
            started_at=self._clock(),
            session_id=uuid.uuid4().hex,
        )
        # 先留痕再登记会话：审计写不进去（数据库忙）时登录不成功，也不留下半个会话。
        self._audit(user, AuditAction.LOGIN, "登录成功" + ("，须先修改密码" if must_change else ""))
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def logout(self, session: Session) -> None:
        """退出登录，可重复调用，只写一次审计。

        Args:
            session: 会话。
        """
        with self._lock:
            known = self._sessions.pop(session.session_id, None)
        if known is not None:
            self._audit_best_effort(session.user, AuditAction.LOGOUT, "退出登录")

    def lock(self, session: Session) -> None:
        """锁屏：会话保留，但在解锁前拒绝一切操作。试验在后台继续。

        Args:
            session: 会话。
        """
        with self._lock:
            if session.session_id not in self._sessions or session.locked:
                return
            session.locked = True
        # 锁屏是保护动作：数据库忙时也要锁住，审计缺失只记日志。
        self._audit_best_effort(session.user, AuditAction.SCREEN_LOCKED, "锁定屏幕")

    def unlock(self, session: Session, password: str) -> None:
        """用密码解锁。失败计入登录失败次数。

        Args:
            session: 已锁定的会话。
            password: 明文密码。

        Raises:
            InvalidCredentialsError: 密码错误。
            AccountLockedError: 失败次数过多。
            AccountDisabledError: 账号已被停用。
            PermissionDeniedError: 会话已失效。
            AuthStorageBusyError: 数据库正忙，仍保持锁定，稍后重试。
        """
        with self._lock:
            if session.session_id not in self._sessions:
                raise PermissionDeniedError("会话已失效，请重新登录", AuthErrorCode.SESSION_INVALID)
        self._verify(session.user.username, password, purpose="解锁")
        self._audit(session.user, AuditAction.SCREEN_UNLOCKED, "解锁屏幕")  # 写不进去就保持锁定
        with self._lock:
            session.locked = False

    def change_password(self, session: Session, old_password: str, new_password: str) -> None:
        """修改自己的密码，成功后清除“必须改密”标记。

        Args:
            session: 会话（不能处于锁屏状态）。
            old_password: 当前密码。
            new_password: 新密码。

        Raises:
            PermissionDeniedError: 会话已失效或已锁屏。
            InvalidCredentialsError: 当前密码错误。
            AccountLockedError: 失败次数过多。
            PasswordPolicyError: 新密码过短，或与当前密码、登录名相同。
            AuthStorageBusyError: 数据库正忙，密码没有修改，稍后重试。
        """
        with self._lock:
            if session.session_id not in self._sessions or session.locked:
                raise PermissionDeniedError("会话已失效或已锁屏", AuthErrorCode.SESSION_INVALID)
        self._check_new_password(new_password, old_password, session.user.username)
        self._verify(session.user.username, old_password, purpose="修改密码")
        try:
            self._storage.users.set_password_hash(
                session.user.user_id,
                self._hasher.hash(new_password),
                must_change_password=False,
            )
        except StorageBusyError as error:
            raise AuthStorageBusyError("数据库正忙，密码没有修改") from error
        session.must_change_password = False
        # 密码已经改了，无法回滚；审计缺失只记日志，不让用户以为没改成。
        self._audit_best_effort(session.user, AuditAction.PASSWORD_CHANGED, "修改密码")

    def confirm_password(self, session: Session, password: str, *, purpose: str) -> User:
        """再次验证当前用户的密码，用于签署这类要留下个人责任的操作。

        和登录共用失败次数限制：连续输错会锁定账号，每次失败都写 ``login_failed`` 审计。
        调用者仍须先用 :meth:`authorize` 检查权限。

        Args:
            session: 调用者的会话（不能处于锁屏状态）。
            password: 明文密码。
            purpose: 用途，写入审计，例如“签署”。

        Returns:
            数据库里最新的用户。

        Raises:
            PermissionDeniedError: 会话已失效或已锁屏。
            InvalidCredentialsError: 密码错误。
            AccountLockedError: 失败次数过多。
            AccountDisabledError: 账号已停用。
            AuthStorageBusyError: 数据库正忙（只在需要升级哈希参数的写入时可能出现）。
        """
        with self._lock:
            known = self._sessions.get(session.session_id) is session
        if not known or session.locked:
            raise PermissionDeniedError("会话已失效或已锁屏", AuthErrorCode.SESSION_INVALID)
        user, _ = self._verify(session.user.username, password, purpose=purpose)
        return user

    # ------------------------------------------------------------------ 用户管理的配合

    def refresh_sessions(self, user: User) -> None:
        """账号的角色或显示名被改动后，让它已登录的会话立即采用新值。

        Args:
            user: 数据库里最新的用户。
        """
        with self._lock:
            for session in self._sessions.values():
                if session.user.user_id == user.user_id:
                    session.user = user

    def revoke_sessions(self, user_id: str) -> int:
        """让某账号的全部会话失效（账号被停用或重置密码时），之后的操作都要重新登录。

        Args:
            user_id: 用户标识。

        Returns:
            被注销的会话数。
        """
        with self._lock:
            ids = [sid for sid, s in self._sessions.items() if s.user.user_id == user_id]
            for sid in ids:
                del self._sessions[sid]
        return len(ids)

    def hash_password(self, password: str) -> str:
        """用登录同一套参数计算密码哈希，供用户管理创建账号和重置密码。

        Args:
            password: 明文密码。

        Returns:
            哈希字符串。
        """
        return self._hasher.hash(password)

    # ------------------------------------------------------------------ 权限

    def authorize(self, session: Session | None, permission: Permission) -> User:
        """校验会话是否有权限执行某操作；服务入口必须先调用它。

        每次都重新读取用户，所以账号被停用或改了角色会立即生效。
        拒绝时写 ``permission_denied`` 审计（会话失效时无法确定操作者，只记日志）。

        Args:
            session: 调用者的会话。
            permission: 操作所需的权限。

        Returns:
            数据库里最新的用户，可传给需要 ``User`` 的服务。

        Raises:
            PermissionDeniedError: 会话为空或已失效、已锁屏、必须先改密、账号已停用或没有权限。
        """
        if session is None:
            raise PermissionDeniedError("请先登录", AuthErrorCode.NOT_LOGGED_IN)
        with self._lock:
            valid = self._sessions.get(session.session_id) is session
        if not valid:
            raise PermissionDeniedError("会话已失效，请重新登录", AuthErrorCode.SESSION_INVALID)
        if session.locked:
            raise PermissionDeniedError("屏幕已锁定，请先解锁", AuthErrorCode.SCREEN_LOCKED)
        if session.must_change_password:
            raise PermissionDeniedError("请先修改初始密码", AuthErrorCode.MUST_CHANGE_PASSWORD)
        user = self._storage.users.get(session.user.user_id)
        if user is None or not user.active:
            self._deny(session.user, permission, "账号已停用")
        if not has_permission(user.role, permission):
            self._deny(user, permission, "角色无此权限")
        return user

    def _deny(self, user: User, permission: Permission, why: str) -> NoReturn:
        # 拒绝本身总是安全的：审计写不进去（数据库忙）时仍然拒绝，只记日志。
        self._audit_best_effort(
            user, AuditAction.PERMISSION_DENIED, f"拒绝 {permission.value}：{why}"
        )
        raise PermissionDeniedError(f"没有权限：{permission.value}", AuthErrorCode.DENIED)

    # ------------------------------------------------------------------ 首个管理员

    def needs_first_admin(self) -> bool:
        """系统里还没有任何账号时为 True，界面应显示“创建管理员”页。

        Returns:
            没有账号时为 True。
        """
        return not self._storage.users.list_all()

    def create_first_admin(self, *, username: str, display_name: str, password: str) -> None:
        """创建首个管理员。只在没有任何账号时可用，密码直接生效，首次登录不强制改密。

        Args:
            username: 登录名。
            display_name: 显示名称，留空则用登录名。
            password: 初始密码。

        Raises:
            SetupNotAllowedError: 已有账号，或没有配置创建方式。
            PasswordPolicyError: 登录名或密码不符合要求。
            AuthStorageBusyError: 数据库正忙，稍后重试。
        """
        if self._seeder is None:
            raise SetupNotAllowedError(
                "当前配置不允许在界面创建管理员", AuthErrorCode.SETUP_DISABLED
            )
        name = validate_username(username)
        if len(password) < self._policy.min_password_length:
            raise PasswordPolicyError(
                f"密码不能少于 {self._policy.min_password_length} 个字符",
                AuthErrorCode.PASSWORD_TOO_SHORT,
                min=self._policy.min_password_length,
            )
        with self._lock:
            if not self.needs_first_admin():
                raise SetupNotAllowedError(
                    "系统已经初始化，不能再创建首个管理员", AuthErrorCode.ALREADY_INITIALIZED
                )
            try:
                self._seeder.seed_first_admin(
                    username=name, display_name=display_name.strip() or name, password=password
                )
            except StorageBusyError as error:
                raise AuthStorageBusyError("数据库正忙，管理员没有创建") from error

    # ------------------------------------------------------------------ 内部

    def _check_new_password(self, new: str, old: str, username: str) -> None:
        if len(new) < self._policy.min_password_length:
            raise PasswordPolicyError(
                f"密码不能少于 {self._policy.min_password_length} 个字符",
                AuthErrorCode.PASSWORD_TOO_SHORT,
                min=self._policy.min_password_length,
            )
        if new == old:
            raise PasswordPolicyError(
                "新密码不能与当前密码相同", AuthErrorCode.PASSWORD_SAME_AS_OLD
            )
        if new.casefold() == username.casefold():
            raise PasswordPolicyError(
                "新密码不能与登录名相同", AuthErrorCode.PASSWORD_SAME_AS_USERNAME
            )

    def _verify(self, name: str, password: str, *, purpose: str) -> tuple[User, bool]:
        """校验用户名和密码，处理失败计数与锁定。成功时清除计数。"""
        now = self._clock()
        with self._lock:
            state = self._attempts.get(name)
            if state is not None and state.locked_until is not None:
                if state.locked_until > now:
                    remaining = (state.locked_until - now).total_seconds()
                    self._audit_name_best_effort(
                        name, AuditAction.LOGIN_FAILED, f"{purpose}被拒绝：账号锁定中"
                    )
                    raise AccountLockedError(remaining)
                state.failures = 0
                state.locked_until = None
        credentials = self._storage.users.get_credentials(name) if name else None
        if credentials is None:
            if self._dummy_hash is None:
                self._dummy_hash = self._hasher.hash("gelinspect-dummy")
            self._hasher.verify(self._dummy_hash, password)  # 抹平响应时间，不泄露用户是否存在
            self._fail(name, None, purpose, "用户不存在")
        if not self._hasher.verify(credentials.password_hash, password):
            self._fail(name, credentials.user, purpose, "密码错误")
        with self._lock:
            self._attempts.pop(name, None)
        if not credentials.user.active:
            self._audit_best_effort(
                credentials.user, AuditAction.LOGIN_FAILED, f"{purpose}被拒绝：账号已停用"
            )
            raise AccountDisabledError("账号已停用，请联系管理员")
        if self._hasher.needs_rehash(credentials.password_hash):
            try:
                self._storage.users.set_password_hash(
                    credentials.user.user_id,
                    self._hasher.hash(password),
                    must_change_password=credentials.must_change_password,
                )
            except StorageBusyError:
                _log.warning("数据库正忙，暂不升级 %s 的密码哈希参数，下次登录再试", name)
        return credentials.user, credentials.must_change_password

    def _fail(self, name: str, user: User | None, purpose: str, why: str) -> NoReturn:
        """记一次失败；达到上限则锁定。总是抛出异常。"""
        now = self._clock()
        with self._lock:
            state = self._attempts.setdefault(name, _Attempts())
            self._attempts.move_to_end(name)
            while len(self._attempts) > _MAX_TRACKED_NAMES:
                self._attempts.popitem(last=False)
            state.failures += 1
            locked = state.failures >= self._policy.max_attempts
            if locked:
                state.locked_until = now + timedelta(seconds=self._policy.lockout_s)
            count = state.failures
        detail = f"{purpose}失败：{why}（第 {count} 次）"
        if locked:
            detail += f"，账号锁定 {self._policy.lockout_s:g} 秒"
        # 失败的结果已经确定，审计写不进去（数据库忙）时不改变结果，只记日志。
        if user is not None:
            self._audit_best_effort(user, AuditAction.LOGIN_FAILED, detail)
        else:
            self._audit_name_best_effort(name or "(空)", AuditAction.LOGIN_FAILED, detail)
        if locked:
            raise AccountLockedError(self._policy.lockout_s)
        raise InvalidCredentialsError(self._policy.max_attempts - count)

    def _audit(self, user: User, action: AuditAction, detail: str) -> None:
        self._write(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=action,
                detail=detail,
            )
        )

    def _audit_name(self, name: str, action: AuditAction, detail: str) -> None:
        self._write(
            AuditEvent(
                occurred_at=self._clock(),
                actor=name,
                actor_role=None,
                action=action,
                detail=detail,
            )
        )

    def _write(self, event: AuditEvent) -> None:
        # 审计写入失败时让异常传出：登录等操作没有留痕就不应该成功。
        # 等写锁超时（数据库被别的写入者占着）转成 AuthStorageBusyError，界面据此提示稍后重试。
        try:
            self._storage.audit.append(event)
        except StorageBusyError as error:
            raise AuthStorageBusyError("数据库正忙，审计没有写入，操作未生效") from error

    def _audit_name_best_effort(self, name: str, action: AuditAction, detail: str) -> None:
        """同 :meth:`_audit_best_effort`，操作者只有名字（用户不存在或尚未确认）。"""
        try:
            self._audit_name(name, action, detail)
        except AuthStorageBusyError:
            _log.error("数据库正忙，审计缺失：%s %s（%s）", name, action.value, detail)

    def _audit_best_effort(self, user: User, action: AuditAction, detail: str) -> None:
        """写审计，数据库忙时只记日志：用于拒绝、锁屏、退出这类不应因审计失败而改变结果的动作。"""
        try:
            self._audit(user, action, detail)
        except AuthStorageBusyError:
            _log.error("数据库正忙，审计缺失：%s %s（%s）", user.username, action.value, detail)
