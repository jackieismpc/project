"""审计追踪页的服务：按时间段、操作者、关键字查询，校验哈希链。

权限：``audit_view``（管理员和分析员），服务层每次都重新校验。**查看审计本身也写审计**：

* 新开一次查询（第一页）在查询之后写一条 ``audit_viewed``，记下条件；同一次查询往后翻页不再重复写，
  否则每翻一页都会多出一条记录，把正在看的列表往后顶。导出另有 ``data_exported``。
* 校验完整性写一条 ``audit_verified``，记下结果和第一处异常的序号。这条事件在校验**之后**追加，
  所以不会影响本次校验的结果。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.entities import AuditEvent
from gelinspect.domain.enums import AuditAction
from gelinspect.domain.permissions import Permission
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ChainVerification,
    Page,
    PageRequest,
    UserRepository,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode

SYSTEM_ACTOR = "system"
"""系统自己写入的事件的操作者。"""


class AuditStorage(Protocol):
    """审计服务需要的存储。"""

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...

    @property
    def users(self) -> UserRepository:
        """用户仓储（用来列出可选的操作者）。"""
        ...


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """审计查询条件，条件之间为「且」，为 None 或空的条件不生效。

    Attributes:
        start: 起始时间（含）。
        end: 结束时间（不含）。
        actor: 操作者登录名，精确匹配。
        keyword: 在操作记录和试验标识里不区分大小写地做子串匹配。
        max_seq: 序号上限（含）。翻页时用第一页看到的最大序号，让后面的页不受期间新写入的
            记录影响。
    """

    start: datetime | None = None
    end: datetime | None = None
    actor: str | None = None
    keyword: str | None = None
    max_seq: int | None = None

    def to_query(self) -> AuditQuery:
        """转成存储层的查询条件。

        Returns:
            查询条件；空白的操作者和关键字视为没有。

        Raises:
            ServiceError: 起始时间不早于结束时间。
        """
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ServiceError("起始时间必须早于结束时间", ServiceErrorCode.AUDIT_QUERY_INVALID)
        return AuditQuery(
            start=self.start,
            end=self.end,
            actor=(self.actor or "").strip() or None,
            keyword=(self.keyword or "").strip() or None,
            max_seq=self.max_seq,
        )

    def describe(self) -> str:
        """条件的简体中文说明，写入审计。

        Returns:
            例如「操作者=admin，关键字=登录」；没有条件时为「无条件」。
        """
        parts: list[str] = []
        if self.start is not None:
            parts.append(f"起={self.start.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}Z")
        if self.end is not None:
            parts.append(f"止={self.end.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}Z")
        actor = (self.actor or "").strip()
        keyword = (self.keyword or "").strip()
        if actor:
            parts.append(f"操作者={actor}")
        if keyword:
            parts.append(f"关键字={keyword}")
        return "，".join(parts) if parts else "无条件"


class AuditService:
    """审计追踪服务。线程安全：界面把查询和校验放在后台线程调用。"""

    def __init__(
        self,
        auth: AuthService,
        storage: AuditStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限。
            storage: 存储。
            clock: 返回带时区当前时间的函数，测试注入。
        """
        self._auth = auth
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    def query(
        self, session: Session | None, audit_filter: AuditFilter, page: PageRequest
    ) -> Page[AuditRecord]:
        """分页查询审计记录，最新在前。新开一次查询（第一页）时写 ``audit_viewed``。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 权限。
            audit_filter: 查询条件。
            page: 分页请求。

        Returns:
            一页记录和满足条件的总数。

        Raises:
            PermissionDeniedError: 没有权限。
            ServiceError: 条件不合法。
            StorageBusyError: 数据库忙。
        """
        user = self._auth.authorize(session, Permission.AUDIT_VIEW)
        query = audit_filter.to_query()
        result = self._storage.audit.query(query, page)
        if page.offset == 0:
            # 先查后记：这次查看不会出现在自己的结果里（否则按关键字查什么都至少有一条命中）。
            self._storage.audit.append(
                AuditEvent(
                    occurred_at=self._clock(),
                    actor=user.username,
                    actor_role=user.role,
                    action=AuditAction.AUDIT_VIEWED,
                    detail=f"查看审计追踪：{audit_filter.describe()}",
                )
            )
        return result

    def actors(self, session: Session | None) -> list[str]:
        """可选的操作者：全部账号的登录名，加上系统自己。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 权限。

        Returns:
            登录名列表，系统在最后。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.AUDIT_VIEW)
        names = [u.username for u in self._storage.users.list_all()]
        return [*names, SYSTEM_ACTOR] if SYSTEM_ACTOR not in names else names

    def verify_chain(
        self,
        session: Session | None,
        progress: Callable[[int, int], None] | None = None,
    ) -> ChainVerification:
        """校验审计哈希链，并把结果写入审计。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 权限。
            progress: 每校验完一批后调用，参数为（已校验到的序号，总条数），在本线程上执行。

        Returns:
            校验结果；不通过时 ``first_bad_seq`` 是第一处异常的序号。

        Raises:
            PermissionDeniedError: 没有权限。
            StorageBusyError: 数据库忙，校验结果没有写入审计。
        """
        user = self._auth.authorize(session, Permission.AUDIT_VIEW)
        audit = self._storage.audit
        total = audit.query(AuditQuery(), PageRequest(limit=1)).total

        def report(done: int) -> None:
            if progress is not None:
                progress(done, total)

        result = audit.verify_chain(report)
        if result.ok:
            detail = f"校验审计完整性：通过，共 {result.checked} 条"
        else:
            where = (
                "" if result.first_bad_seq is None else f"，第一处异常在序号 {result.first_bad_seq}"
            )
            kind = "" if result.failure is None else f"（{result.failure.value}）"
            detail = f"校验审计完整性：未通过{where}{kind}"
        audit.append(
            AuditEvent(
                occurred_at=self._clock(),
                actor=user.username,
                actor_role=user.role,
                action=AuditAction.AUDIT_VERIFIED,
                detail=detail,
            )
        )
        return result
