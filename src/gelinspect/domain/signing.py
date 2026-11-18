"""签署前提：试验已完成、结果齐全且待复核已处理。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gelinspect.domain.entities import Experiment
from gelinspect.domain.state_machine import ExperimentState


class SigningBlocker(StrEnum):
    """不能签署的原因。"""

    ALREADY_SIGNED = "already_signed"
    NOT_COMPLETED = "not_completed"
    PENDING_REVIEW = "pending_review"
    RESULT_MISSING = "result_missing"


@dataclass(frozen=True, slots=True)
class SigningBlock:
    """一条不能签署的原因。"""

    blocker: SigningBlocker
    tubes: tuple[int, ...] = ()
    state: ExperimentState | None = None


def signing_blocks(
    experiment: Experiment, *, allow_signed_revision: bool = False
) -> tuple[SigningBlock, ...]:
    """列出试验当前不能签署的原因；空元组表示可以签署。"""
    if experiment.state is ExperimentState.SIGNED and not allow_signed_revision:
        return (SigningBlock(SigningBlocker.ALREADY_SIGNED),)
    if experiment.state is ExperimentState.PENDING_REVIEW:
        return (SigningBlock(SigningBlocker.PENDING_REVIEW, tubes=experiment.pending_review_tubes),)
    if experiment.state not in {ExperimentState.COMPLETED, ExperimentState.SIGNED}:
        return (SigningBlock(SigningBlocker.NOT_COMPLETED, state=experiment.state),)
    expected = {assignment.tube_no for assignment in experiment.assignments}
    missing = tuple(sorted(expected - set(experiment.results)))
    blocks: list[SigningBlock] = []
    if missing:
        blocks.append(SigningBlock(SigningBlocker.RESULT_MISSING, tubes=missing))
    pending = experiment.pending_review_tubes
    if pending:
        blocks.append(SigningBlock(SigningBlocker.PENDING_REVIEW, tubes=pending))
    return tuple(blocks)


def can_sign(experiment: Experiment) -> bool:
    """试验完成、结果齐全并处理完待复核管位后可以签署。"""
    return not signing_blocks(experiment)
