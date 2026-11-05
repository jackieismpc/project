"""工作台的数据：进行中的试验、最近结果、需要处理的事项、温度曲线。

把分散在试验、试剂和温度日志里的数据整理成工作台直接显示的结构。每个入口先校验
``experiment_view``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gelinspect.domain.enums import ExperimentType, Interpretation, ReagentKind
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.interfaces.storage import (
    ExperimentQuery,
    ExperimentRepository,
    ExperimentSummary,
    PageRequest,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.monitor_service import ExperimentView, MonitorService
from gelinspect.services.reagent_service import ReagentService

EXPIRY_WARN_DAYS = 30
"""试剂距有效期不足这么多天时在工作台提醒。"""
_FINISHED = frozenset(
    {
        ExperimentState.PENDING_REVIEW,
        ExperimentState.COMPLETED,
        ExperimentState.SIGNED,
        ExperimentState.STOPPED,
    }
)


class DashboardStorage(Protocol):
    """工作台需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...


class AttentionKind(StrEnum):
    """需要处理的事项种类。"""

    REVIEW_PENDING = "review_pending"
    FAULTED = "faulted"
    REAGENT_EXPIRING = "reagent_expiring"
    REAGENT_EXPIRED = "reagent_expired"


@dataclass(frozen=True, slots=True)
class AttentionItem:
    """一条需要处理的事项。

    Attributes:
        kind: 种类。
        experiment_id: 相关试验，试剂事项为 None。
        count: 待复核的管数；试剂事项为剩余（或已过期）天数。
        label: 试剂种类，非试剂事项为 None。
        batch_no: 试剂批号。
    """

    kind: AttentionKind
    experiment_id: str | None = None
    count: int = 0
    label: ReagentKind | None = None
    batch_no: str = ""


@dataclass(frozen=True, slots=True)
class RecentResult:
    """最近结果表的一行。

    Attributes:
        experiment_id: 试验标识。
        experiment_type: 类型。
        tube_count: 试管数。
        state: 状态。
        positives: 阳性管数。
        negatives: 阴性管数。
        pending: 待复核管数。
        finished: 是否已有结果可以汇总。
        created_at: 创建时间。
    """

    experiment_id: str
    experiment_type: ExperimentType
    tube_count: int
    state: ExperimentState
    positives: int
    negatives: int
    pending: int
    finished: bool
    created_at: datetime


class DashboardService:
    """工作台数据。"""

    def __init__(
        self,
        auth: AuthService,
        storage: DashboardStorage,
        monitor: MonitorService,
        reagents: ReagentService,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务。
            storage: 存储。
            monitor: 监控服务，用来取进行中试验的视图。
            reagents: 试剂服务，用来取批次和有效期。
        """
        self._auth = auth
        self._storage = storage
        self._monitor = monitor
        self._reagents = reagents

    def running(self, session: Session | None) -> ExperimentView | None:
        """进行中的试验视图。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            视图，没有进行中的试验时为 None。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        active = self._monitor.active_experiment_id()
        return None if active is None else self._monitor.view(session, active)

    def recent_results(self, session: Session | None, limit: int = 8) -> list[RecentResult]:
        """最近的试验及其结果汇总。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。
            limit: 最多几条。

        Returns:
            按创建时间倒序的结果行。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        page = self._storage.experiments.query(ExperimentQuery(), PageRequest(limit=limit))
        return [self._summarize(item) for item in page.items]

    def _summarize(self, item: ExperimentSummary) -> RecentResult:
        experiment = self._storage.experiments.get(item.experiment_id)
        results = list(experiment.results.values()) if experiment is not None else []
        final = [r.final_interpretation for r in results]
        return RecentResult(
            experiment_id=item.experiment_id,
            experiment_type=item.experiment_type,
            tube_count=item.tube_count,
            state=item.state,
            positives=final.count(Interpretation.POSITIVE),
            negatives=final.count(Interpretation.NEGATIVE),
            pending=final.count(Interpretation.PENDING_REVIEW),
            finished=item.state in _FINISHED and bool(results),
            created_at=item.created_at,
        )

    def attention(self, session: Session | None) -> list[AttentionItem]:
        """需要处理的事项：待复核、异常的试验，临期和过期的试剂。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            事项列表，没有事项时为空。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        self._auth.authorize(session, Permission.EXPERIMENT_VIEW)
        items: list[AttentionItem] = []
        reviewing = self._storage.experiments.query(
            ExperimentQuery(state=ExperimentState.PENDING_REVIEW), PageRequest(limit=10)
        )
        for summary in reviewing.items:
            experiment = self._storage.experiments.get(summary.experiment_id)
            if experiment is not None:
                items.append(
                    AttentionItem(
                        AttentionKind.REVIEW_PENDING,
                        summary.experiment_id,
                        len(experiment.pending_review_tubes),
                    )
                )
        faulted = self._storage.experiments.query(
            ExperimentQuery(state=ExperimentState.FAULTED), PageRequest(limit=10)
        )
        items += [AttentionItem(AttentionKind.FAULTED, s.experiment_id) for s in faulted.items]
        for batch in self._reagents.list_batches(session):
            days = self._reagents.days_remaining(batch.expiry_date)
            if days is None or days > EXPIRY_WARN_DAYS:
                continue
            kind = AttentionKind.REAGENT_EXPIRED if days < 0 else AttentionKind.REAGENT_EXPIRING
            items.append(AttentionItem(kind, None, abs(days), batch.kind, batch.batch_no))
        return items

    def temperatures(self, session: Session | None) -> list[float]:
        """曲线用的温度：进行中（或最近一次）试验的记录。

        Args:
            session: 调用者的会话，需要 ``experiment_view`` 权限。

        Returns:
            摄氏度，按时间升序，没有记录时为空。

        Raises:
            PermissionDeniedError: 没有权限。
        """
        experiment_id = self._monitor.default_experiment_id(session)
        if experiment_id is None:
            return []
        return self._monitor.temperatures(session, experiment_id)


__all__ = [
    "AttentionItem",
    "AttentionKind",
    "DashboardService",
    "RecentResult",
]
