"""签署前提：哪些情况下试验不能签署。

纯函数，不依赖 Qt 或数据库。签署会把结果变成放行依据，所以前提比状态机更严格：

* 有待复核的试管，或有试管没有结果：不能签署。
* 结论功能启用时，缺少已入库结论、结论为“待定”或“无效”：不能签署。未判定的结果不能
  被当成放行依据。
* “合格”和“不合格”的试验都可以签署；不合格的试验同样需要留下签署记录。

结论相关的前提只在结论功能打开时（配置 ``conclusions.enabled``）才检查。软件配置默认启用；
显式关闭时不要求结论，签署仍要求试验已完成、所有试管均有结果且待复核已处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gelinspect.domain.entities import Experiment
from gelinspect.domain.enums import ConclusionVerdict
from gelinspect.domain.state_machine import ExperimentState


class SigningBlocker(StrEnum):
    """不能签署的原因，界面据此生成当前语言的说明。"""

    ALREADY_SIGNED = "already_signed"
    """已经签署。"""
    NOT_COMPLETED = "not_completed"
    """试验还没有完成（运行中、异常、已停止等）。"""
    PENDING_REVIEW = "pending_review"
    """有试管等待人工复核。"""
    RESULT_MISSING = "result_missing"
    """有试管没有结果。"""
    NO_CONCLUSION = "no_conclusion"
    """没有入库的结论。"""
    VERDICT_PENDING = "verdict_pending"
    """结论为待定。"""
    VERDICT_INVALID = "verdict_invalid"
    """结论为无效。"""


@dataclass(frozen=True, slots=True)
class SigningBlock:
    """一条不能签署的原因。

    Attributes:
        blocker: 原因分类。
        tubes: 相关的管号（待复核、缺结果），其余为空。
        state: 试验当前状态（只对 ``NOT_COMPLETED`` 有意义）。
    """

    blocker: SigningBlocker
    tubes: tuple[int, ...] = ()
    state: ExperimentState | None = None


SIGNABLE_VERDICTS = frozenset(
    {ConclusionVerdict.PASS, ConclusionVerdict.FAIL, ConclusionVerdict.NOT_APPLICABLE}
)
"""可以签署的结论。"""


def signing_blocks(
    experiment: Experiment, *, conclusions_enabled: bool, allow_signed_revision: bool = False
) -> tuple[SigningBlock, ...]:
    """列出试验当前不能签署的全部原因。

    Returns:
        原因列表；空表示可以签署。不允许修订时，已签署的试验只返回 ``ALREADY_SIGNED``。

    Args:
        experiment: 试验聚合。
        conclusions_enabled: 结论功能是否打开。关闭时不检查结论。
        allow_signed_revision: 是否允许复核后的已签署版本重新签署。
    """
    if experiment.state is ExperimentState.SIGNED and not allow_signed_revision:
        return (SigningBlock(SigningBlocker.ALREADY_SIGNED),)
    if experiment.state is ExperimentState.PENDING_REVIEW:
        return (SigningBlock(SigningBlocker.PENDING_REVIEW, tubes=experiment.pending_review_tubes),)
    if experiment.state not in {ExperimentState.COMPLETED, ExperimentState.SIGNED}:
        return (SigningBlock(SigningBlocker.NOT_COMPLETED, state=experiment.state),)
    blocks: list[SigningBlock] = []
    expected = {assignment.tube_no for assignment in experiment.assignments}
    missing = tuple(sorted(expected - set(experiment.results)))
    if missing:
        blocks.append(SigningBlock(SigningBlocker.RESULT_MISSING, tubes=missing))
    pending = experiment.pending_review_tubes
    if pending:
        blocks.append(SigningBlock(SigningBlocker.PENDING_REVIEW, tubes=pending))
    conclusion = experiment.conclusion
    if not conclusions_enabled:
        pass
    elif conclusion is None:
        blocks.append(SigningBlock(SigningBlocker.NO_CONCLUSION))
    elif conclusion.verdict is ConclusionVerdict.PENDING:
        blocks.append(SigningBlock(SigningBlocker.VERDICT_PENDING))
    elif conclusion.verdict is ConclusionVerdict.INVALID:
        blocks.append(SigningBlock(SigningBlocker.VERDICT_INVALID))
    return tuple(blocks)


def can_sign(experiment: Experiment, *, conclusions_enabled: bool) -> bool:
    """试验当前能否签署。

    Args:
        experiment: 试验。
        conclusions_enabled: 结论功能是否打开。

    Returns:
        没有任何阻止原因时为 True。
    """
    return not signing_blocks(experiment, conclusions_enabled=conclusions_enabled)
