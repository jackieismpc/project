"""界面读取数据的服务入口：每个入口先校验权限，再查询存储。

界面只能通过这里读数据，不直接碰数据库。权限在这里再校验一次，
所以即使界面的入口没有被禁用（或被绕过），无权限的调用也会被拒绝。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import (
    AuditQuery,
    AuditRecord,
    AuditRepository,
    ExperimentQuery,
    ExperimentRepository,
    ExperimentSummary,
    Page,
    PageRequest,
    UserRepository,
)
from gelinspect.services.auth import AuthService, Session


class QueryStorage(Protocol):
    """查询需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    @property
    def audit(self) -> AuditRepository:
        """审计仓储。"""
        ...

    @property
    def users(self) -> UserRepository:
        """用户仓储（历史页的“操作人”下拉）。"""
        ...


class QueryService:
    """工作台等页面的只读查询。"""

    def __init__(self, auth: AuthService, storage: QueryStorage) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用来校验权限。
            storage: 存储。
        """
        self._auth = auth
        self._storage = storage

    def recent_experiments(
        self, session: Session | None, limit: int = 8
    ) -> list[ExperimentSummary]:
        """最近创建的试验。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            limit: 最多返回几条。

        Returns:
            按创建时间倒序的试验摘要。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        page = self._storage.experiments.query(ExperimentQuery(), PageRequest(limit=limit))
        return list(page.items)

    def faulted_experiments(
        self, session: Session | None, limit: int = 10
    ) -> list[ExperimentSummary]:
        """处于异常状态、需要人工处理的试验。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            limit: 最多返回几条。

        Returns:
            异常试验摘要。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        query = ExperimentQuery(state=ExperimentState.FAULTED)
        return list(self._storage.experiments.query(query, PageRequest(limit=limit)).items)

    def recent_audit(self, session: Session | None, limit: int = 20) -> Page[AuditRecord]:
        """最近的审计记录。

        Args:
            session: 调用者的会话，需要 ``audit_view`` 权限。
            limit: 最多返回几条。

        Returns:
            一页审计记录。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.AUDIT_VIEW)
        return self._storage.audit.query(AuditQuery(), PageRequest(limit=limit))

    def search_experiments(
        self, session: Session | None, query: ExperimentQuery, page: PageRequest
    ) -> Page[ExperimentSummary]:
        """历史记录页的查询：按试验类型、时间段、批号、状态和操作人筛选，分页。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            query: 筛选条件。
            page: 分页请求。

        Returns:
            一页试验摘要（按创建时间倒序）和满足条件的总数。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return self._storage.experiments.query(query, page)

    def latest_experiment_time(self, session: Session | None) -> datetime | None:
        """最新试验的创建时间，用来固定一次历史查询的范围。

        历史页开始一次新查询时先取它，之后这次查询的翻页都带上它做
        ``ExperimentQuery.max_created_at``，期间新建的试验不会让后面的页错位；
        点“查询”或“刷新”才看到新试验。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            创建时间；没有试验时为 None。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return self._storage.experiments.latest_created_at()

    def operators(self, session: Session | None) -> list[str]:
        """历史记录页“操作人”下拉里的登录名。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            全部账号的登录名，按字母顺序。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        return [user.username for user in self._storage.users.list_all()]
