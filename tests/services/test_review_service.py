"""人工复核服务：权限、意见必填、状态机、审计、算法结果不被覆盖。"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest

from gelinspect.domain.entities import Experiment, TubeResult
from gelinspect.domain.enums import (
    AuditAction,
    GelState,
    Interpretation,
    ReviewStatus,
    TubeAnalysisStatus,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.review_service import ReviewService, ReviewStorage
from tests.services.conftest import NOW, Stack
from tests.services.engine_helpers import ready_experiment

PARTIAL = (3, 5)


def _result(tube_no: int, state: GelState) -> TubeResult:
    return TubeResult.from_analysis(
        tube_no=tube_no,
        status=TubeAnalysisStatus.OK,
        state=state,
        mapping=GelStateMapping(),
        score=0.4,
        model_version="m1",
        config_version="c1",
        elapsed_ms=12.0,
    )


def make_pending(stack: Stack, *, finish: bool = True) -> Experiment:
    """八管试验：3、5 号部分凝固（待复核），其余明确。"""
    experiment = ready_experiment(range(1, 9), "EXP-REV-1")
    for event in (ExperimentEvent.START, ExperimentEvent.ROW_DUE, ExperimentEvent.ROW_CAPTURED):
        experiment.apply(event)
    for no in range(1, 9):
        state = GelState.PARTIALLY_GELLED if no in PARTIAL else GelState.FULLY_GELLED
        experiment.record_result(_result(no, state))
    if finish:
        experiment.apply(ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW)
    stack.database.experiments.save(experiment)
    return experiment


def service(stack: Stack) -> ReviewService:
    return ReviewService(stack.auth, stack.database, clock=lambda: NOW)


def stored(stack: Stack) -> Experiment:
    experiment = stack.database.experiments.get("EXP-REV-1")
    assert experiment is not None
    return experiment


def test_analyst_reviews_and_the_last_review_makes_the_experiment_signable(stack: Stack) -> None:
    make_pending(stack)
    reviews = service(stack)
    analyst = stack.login("analyst")

    first = reviews.review_tube(analyst, "EXP-REV-1", 3, Interpretation.POSITIVE, "  凝胶完整  ")
    assert first.remaining == (5,) and first.state is ExperimentState.PENDING_REVIEW
    result = stored(stack).results[3]
    assert result.reviewed_interpretation is Interpretation.POSITIVE
    assert result.reviewed_by == "analyst" and result.reviewed_at == NOW
    assert result.review_note == "凝胶完整"  # 去掉首尾空白
    assert result.review_status is ReviewStatus.REVIEWED
    # 算法的原始结果保留
    assert result.gel_state is GelState.PARTIALLY_GELLED
    assert result.auto_interpretation is Interpretation.PENDING_REVIEW
    assert result.final_interpretation is Interpretation.POSITIVE
    assert stored(stack).state is ExperimentState.PENDING_REVIEW

    last = reviews.review_tube(analyst, "EXP-REV-1", 5, Interpretation.NEGATIVE, "流动明显")
    assert last.remaining == () and last.state is ExperimentState.COMPLETED
    assert stored(stack).state is ExperimentState.COMPLETED
    details = stack.audit(AuditAction.TUBE_REVIEWED)
    assert len(details) == 2 and "部分凝固" not in details[0]
    assert "partially_gelled" in details[0] and "人工 positive" in details[0]
    assert any("review_completed" in d for d in stack.audit(AuditAction.EXPERIMENT_STATE_CHANGED))


def test_other_roles_cannot_review_and_are_audited(stack: Stack) -> None:
    make_pending(stack)
    reviews = service(stack)
    from gelinspect.domain.enums import PermissionGroup

    admin = stack.login("admin")
    user = stack.database.users.get_by_username("experimenter")
    assert user is not None
    from gelinspect.services.user_service import UserService

    UserService(stack.auth, stack.database).set_permission_groups(
        admin, user.user_id, frozenset({PermissionGroup.VIEW})
    )
    with pytest.raises(PermissionDeniedError):
        reviews.review_tube(
            stack.login("experimenter"), "EXP-REV-1", 3, Interpretation.POSITIVE, "x"
        )
    with pytest.raises(PermissionDeniedError):
        reviews.review_tube(None, "EXP-REV-1", 3, Interpretation.POSITIVE, "x")
    assert stored(stack).results[3].reviewed_by is None
    assert len(stack.audit(AuditAction.PERMISSION_DENIED)) == 1


@pytest.mark.parametrize("note", ["", "   ", "\n\t"])
def test_the_note_is_required(stack: Stack, note: str) -> None:
    make_pending(stack)
    with pytest.raises(ServiceError) as caught:
        service(stack).review_tube(
            stack.login("analyst"), "EXP-REV-1", 3, Interpretation.POSITIVE, note
        )
    assert caught.value.code is ServiceErrorCode.REVIEW_NOTE_REQUIRED
    assert stored(stack).results[3].reviewed_by is None


def test_the_conclusion_must_be_positive_or_negative(stack: Stack) -> None:
    make_pending(stack)
    with pytest.raises(ServiceError) as caught:
        service(stack).review_tube(
            stack.login("analyst"), "EXP-REV-1", 3, Interpretation.PENDING_REVIEW, "x"
        )
    assert caught.value.code is ServiceErrorCode.REVIEW_CONCLUSION_INVALID


def test_existing_result_can_be_reviewed_while_other_rows_are_still_analyzing(stack: Stack) -> None:
    make_pending(stack, finish=False)  # 仍在分析中，但这支管已有结果
    analyst = stack.login("analyst")
    reviewed = service(stack).review_tube(
        analyst, "EXP-REV-1", 3, Interpretation.POSITIVE, "已有结果复核原因"
    )
    assert reviewed.state is ExperimentState.ANALYZING
    assert stored(stack).results[3].reviewed_by == "analyst"
    with pytest.raises(ServiceError) as missing:
        service(stack).review_tube(analyst, "EXP-REV-1", 40, Interpretation.POSITIVE, "x")
    assert missing.value.code is ServiceErrorCode.TUBE_NOT_FOUND
    with pytest.raises(ServiceError) as gone:
        service(stack).review_tube(analyst, "NOPE", 1, Interpretation.POSITIVE, "x")
    assert gone.value.code is ServiceErrorCode.EXPERIMENT_NOT_FOUND


class _StaleStorage:
    """读完试验之后，别处把试验状态改了。"""

    def __init__(self, stack: Stack) -> None:
        self._stack = stack

    @property
    def experiments(self) -> object:
        stack = self._stack
        real = stack.database.experiments

        class Repo:
            def get(self, experiment_id: str) -> Experiment | None:
                experiment = real.get(experiment_id)
                real.update_state(experiment_id, ExperimentState.COMPLETED)
                return experiment

        return Repo()

    def unit_of_work(self) -> object:
        return self._stack.database.unit_of_work()


def test_a_stale_state_is_reported_as_a_conflict(stack: Stack) -> None:
    make_pending(stack)
    reviews = ReviewService(
        stack.auth,
        _StaleStorage(stack),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    with pytest.raises(ServiceError) as caught:
        reviews.review_tube(stack.login("analyst"), "EXP-REV-1", 3, Interpretation.POSITIVE, "x")
    assert caught.value.code is ServiceErrorCode.REVIEW_CONFLICT
    assert stored(stack).results[3].reviewed_by is None  # 同一事务回滚


def test_a_concurrent_same_state_review_is_not_allowed_to_overwrite_history(stack: Stack) -> None:
    make_pending(stack)
    real_storage = stack.database

    class ConcurrentReviewStorage:
        @property
        def experiments(self) -> object:
            class Repo:
                def get(self, experiment_id: str) -> Experiment | None:
                    stale = real_storage.experiments.get(experiment_id)
                    assert stale is not None
                    concurrent = real_storage.experiments.get(experiment_id)
                    assert concurrent is not None
                    concurrent.review_tube(
                        3,
                        interpretation=Interpretation.NEGATIVE,
                        reviewed_by="other-reviewer",
                        reviewed_at=NOW + timedelta(seconds=1),
                        note="并发复核",
                    )
                    real_storage.experiments.save_results(experiment_id, [concurrent.results[3]])
                    return stale

            return Repo()

        def unit_of_work(self) -> object:
            return real_storage.unit_of_work()

    reviews = ReviewService(
        stack.auth,
        cast(ReviewStorage, ConcurrentReviewStorage()),
        clock=lambda: NOW,
    )
    with pytest.raises(ServiceError) as caught:
        reviews.review_tube(
            stack.login("analyst"), "EXP-REV-1", 3, Interpretation.POSITIVE, "过期页面复核"
        )
    assert caught.value.code is ServiceErrorCode.REVIEW_CONFLICT
    result = stored(stack).results[3]
    assert result.final_interpretation is Interpretation.NEGATIVE
    assert [entry.reason for entry in result.review_history] == ["并发复核"]


def test_completed_result_can_be_re_reviewed_without_changing_conclusion_and_keeps_history(
    stack: Stack,
) -> None:
    make_pending(stack)
    reviewer = stack.login("analyst")
    reviews = service(stack)
    reviews.review_tube(reviewer, "EXP-REV-1", 3, Interpretation.POSITIVE, "首次原因")
    reviews.review_tube(reviewer, "EXP-REV-1", 5, Interpretation.NEGATIVE, "完成待复核")
    assert stored(stack).state is ExperimentState.COMPLETED

    result = reviews.review_tube(
        reviewer, "EXP-REV-1", 3, Interpretation.POSITIVE, "结论未变，再核原因"
    ).result
    assert result.final_interpretation is Interpretation.POSITIVE
    assert [entry.reason for entry in result.review_history] == ["首次原因", "结论未变，再核原因"]
    assert [entry.reviewed_by for entry in result.review_history] == ["analyst", "analyst"]
    assert len(stack.audit(AuditAction.TUBE_REVIEWED)) == 3
    assert stored(stack).state is ExperimentState.COMPLETED
