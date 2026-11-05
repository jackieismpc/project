"""人工复核：分析员对单管结果判阳性或阴性，意见必填，写审计。

遵守试验状态机：只有「待复核」状态（全部排已分析）才能复核；最后一支待复核的试管处理完，
同一个事务里把试验转为「已完成」（可签署）并固化试验结论，签署由 P11 实现。算法的原始结果不被
覆盖：复核结论存在 ``reviewed_*`` 字段，``final_interpretation`` 以它为准。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from gelinspect.domain.conclusions import finalize_conclusion
from gelinspect.domain.entities import TubeResult
from gelinspect.domain.enums import AuditAction, Interpretation
from gelinspect.domain.errors import TransitionGuardError
from gelinspect.domain.permissions import Permission
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.interfaces.storage import (
    ExperimentRepository,
    StateConflictError,
    UnitOfWork,
)
from gelinspect.services.auth import AuthService, Session
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.monitor_service import audit_event

MAX_NOTE_LENGTH = 1000
"""复核意见的最大长度。"""


class ReviewStorage(Protocol):
    """复核服务需要的存储。"""

    @property
    def experiments(self) -> ExperimentRepository:
        """试验仓储。"""
        ...

    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]:
        """开启工作单元。"""
        ...


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """一次复核的结果。

    Attributes:
        result: 更新后的单管结果。
        remaining: 还在等待复核的管号。
        state: 复核后的试验状态（全部处理完为已完成）。
    """

    result: TubeResult
    remaining: tuple[int, ...]
    state: ExperimentState


class ReviewService:
    """单管人工复核。"""

    def __init__(
        self,
        auth: AuthService,
        storage: ReviewStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """创建服务。

        Args:
            auth: 认证服务，用于校验权限。
            storage: 存储。
            clock: 返回带时区当前时间的函数，测试注入，默认系统时间。
        """
        self._auth = auth
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    def review_tube(
        self,
        session: Session | None,
        experiment_id: str,
        tube_no: int,
        interpretation: Interpretation,
        note: str,
    ) -> ReviewOutcome:
        """复核一支试管。

        Args:
            session: 调用者的会话，需要 ``result_review`` 权限。
            experiment_id: 试验标识。
            tube_no: 管号。
            interpretation: 复核结论，只能是阳性或阴性。
            note: 复核意见，必填。

        Returns:
            复核结果。

        Raises:
            PermissionDeniedError: 没有权限（已写 ``permission_denied`` 审计）。
            ServiceError: 试验或试管不存在、意见为空、结论无效、状态不允许复核，
                或试验被别处改过（``REVIEW_CONFLICT``，请刷新后重试）。
            StorageBusyError: 数据库忙，什么都没有写入，可重试。
        """
        user = self._auth.authorize(session, Permission.RESULT_REVIEW)
        text = note.strip()
        if not text:
            raise ServiceError("复核意见必填", ServiceErrorCode.REVIEW_NOTE_REQUIRED)
        if len(text) > MAX_NOTE_LENGTH:
            raise ServiceError(
                "复核意见过长", ServiceErrorCode.REVIEW_NOTE_REQUIRED, max=MAX_NOTE_LENGTH
            )
        if interpretation is Interpretation.PENDING_REVIEW:
            raise ServiceError(
                "复核结论只能是阳性或阴性", ServiceErrorCode.REVIEW_CONCLUSION_INVALID
            )
        experiment = self._storage.experiments.get(experiment_id)
        if experiment is None:
            raise ServiceError(
                f"试验不存在：{experiment_id}",
                ServiceErrorCode.EXPERIMENT_NOT_FOUND,
                id=experiment_id,
            )
        if tube_no not in experiment.results:
            raise ServiceError(
                f"管号 {tube_no} 没有结果", ServiceErrorCode.TUBE_NOT_FOUND, tube=tube_no
            )
        before = experiment.state
        if before is ExperimentState.SIGNED:
            raise ServiceError(
                "试验已签署，结果和复核已锁定，不能再修改",
                ServiceErrorCode.EXPERIMENT_SIGNED,
                id=experiment_id,
            )
        now = self._clock()
        auto = experiment.results[tube_no]
        try:
            reviewed = experiment.review_tube(
                tube_no,
                interpretation=interpretation,
                reviewed_by=user.username,
                reviewed_at=now,
                note=text,
            )
        except TransitionGuardError as exc:
            raise ServiceError(
                str(exc), ServiceErrorCode.REVIEW_NOT_ALLOWED, state=before.value
            ) from exc
        events = [
            audit_event(
                user,
                AuditAction.TUBE_REVIEWED,
                f"{tube_no} 号管：算法 {auto.gel_state.value if auto.gel_state else '-'}"
                f"（{auto.auto_interpretation.value}）→ 人工 {interpretation.value}；意见：{text}",
                experiment_id,
                now,
            )
        ]
        if not experiment.pending_review_tubes:
            current = experiment.apply(ExperimentEvent.REVIEW_COMPLETED)
            events.append(
                audit_event(
                    user,
                    AuditAction.EXPERIMENT_STATE_CHANGED,
                    f"{before.value} → {current.value}"
                    f"（{ExperimentEvent.REVIEW_COMPLETED.value}）：全部待复核的试管已处理，可以签署",
                    experiment_id,
                    now,
                )
            )
            events.append(
                audit_event(
                    user,
                    AuditAction.CONCLUSION_RECORDED,
                    finalize_conclusion(experiment, now).summary_text(),
                    experiment_id,
                    now,
                )
            )
        try:
            with self._storage.unit_of_work() as uow:
                # 总是带上「预期状态」写一次：别处改过试验状态时在这里发现，整个事务回滚。
                uow.experiments.update_state(experiment_id, experiment.state, expected=before)
                uow.experiments.save_results(experiment_id, [reviewed])
                if experiment.conclusion is not None and experiment.completed_at is not None:
                    uow.experiments.record_completion(
                        experiment_id,
                        completed_at=experiment.completed_at,
                        measured_sensitivity=experiment.measured_sensitivity,
                        conclusion=experiment.conclusion,
                    )
                uow.audit.append_many(events)
        except StateConflictError as exc:
            raise ServiceError(
                "试验状态已被其他操作改变，请刷新后重试", ServiceErrorCode.REVIEW_CONFLICT
            ) from exc
        return ReviewOutcome(reviewed, experiment.pending_review_tubes, experiment.state)
