"""试验状态机。

转换表 :data:`TRANSITIONS` 是唯一事实来源，不在表中的组合都是非法转换。
本模块只判断「状态和事件」是否合法；需要读取试验数据的前置条件（例如有待复核的试管时
不能签署）由 :class:`gelinspect.domain.entities.Experiment` 检查。

一个试验有 10 排独立倒计时，因此 ``INCUBATING``、``READING``、``ANALYZING`` 描述的是
试验当前的活动：某排读数和分析期间，其他排仍在孵育，分析结束后回到 ``INCUBATING``。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from gelinspect.domain.errors import IllegalTransitionError


class ExperimentState(StrEnum):
    """试验状态。"""

    DRAFT = "draft"
    """草稿，可编辑模板、样品和管位。"""
    REAGENT_CHECK = "reagent_check"
    """试剂校验中。"""
    REAGENT_LOCKED = "reagent_locked"
    """试剂过期或未备案，试验被锁定，不能启动。"""
    READY = "ready"
    """就绪，可以启动。"""
    INCUBATING = "incubating"
    """孵育中，等待下一排到时。"""
    READING = "reading"
    """读数中：翻转一排并抓帧。"""
    ANALYZING = "analyzing"
    """分析中：算法处理一排。"""
    PENDING_REVIEW = "pending_review"
    """待复核：全部排已分析，有试管等待人工复核。"""
    COMPLETED = "completed"
    """完成：全部试管都有明确结论，可以签署。"""
    SIGNED = "signed"
    """已签署，终态。"""
    STOPPED = "stopped"
    """已停止，终态。"""
    FAULTED = "faulted"
    """异常：运行中发生故障，可恢复或停止。"""


class ExperimentEvent(StrEnum):
    """触发状态转换的事件。"""

    SUBMIT_FOR_REAGENT_CHECK = "submit_for_reagent_check"
    """提交试剂校验。"""
    REAGENT_CHECK_PASSED = "reagent_check_passed"
    """试剂校验通过。"""
    REAGENT_CHECK_FAILED = "reagent_check_failed"
    """试剂校验不通过，锁定试验。"""
    RETRY_REAGENT_CHECK = "retry_reagent_check"
    """更换试剂后重新校验。"""
    EDIT = "edit"
    """退回草稿继续编辑。"""
    START = "start"
    """启动试验，开始孵育。"""
    ROW_DUE = "row_due"
    """某排孵育到时，开始读数。"""
    ROW_CAPTURED = "row_captured"
    """该排翻转和抓帧完成，开始分析。"""
    ROW_ANALYZED = "row_analyzed"
    """该排分析完成，还有未读的排，回到孵育。"""
    ALL_ANALYZED_CLEAN = "all_analyzed_clean"
    """全部排分析完成，没有待复核的试管。"""
    ALL_ANALYZED_NEEDS_REVIEW = "all_analyzed_needs_review"
    """全部排分析完成，有待复核的试管。"""
    REVIEW_COMPLETED = "review_completed"
    """全部待复核的试管已复核。"""
    REOPEN_REVIEW = "reopen_review"
    """签署前重新打开复核。"""
    SIGN = "sign"
    """签署。"""
    STOP = "stop"
    """人工停止。"""
    FAULT = "fault"
    """发生故障。"""
    RESUME_INCUBATION = "resume_incubation"
    """故障恢复，回到孵育。"""
    RESUME_READING = "resume_reading"
    """故障恢复，重新读数。"""
    RESUME_ANALYSIS = "resume_analysis"
    """故障恢复，重新分析。"""


_S = ExperimentState
_E = ExperimentEvent

TRANSITIONS: Mapping[tuple[ExperimentState, ExperimentEvent], ExperimentState] = MappingProxyType(
    {
        (_S.DRAFT, _E.SUBMIT_FOR_REAGENT_CHECK): _S.REAGENT_CHECK,
        (_S.DRAFT, _E.STOP): _S.STOPPED,
        (_S.REAGENT_CHECK, _E.REAGENT_CHECK_PASSED): _S.READY,
        (_S.REAGENT_CHECK, _E.REAGENT_CHECK_FAILED): _S.REAGENT_LOCKED,
        (_S.REAGENT_CHECK, _E.EDIT): _S.DRAFT,
        (_S.REAGENT_CHECK, _E.STOP): _S.STOPPED,
        (_S.REAGENT_LOCKED, _E.RETRY_REAGENT_CHECK): _S.REAGENT_CHECK,
        (_S.REAGENT_LOCKED, _E.EDIT): _S.DRAFT,
        (_S.REAGENT_LOCKED, _E.STOP): _S.STOPPED,
        (_S.READY, _E.START): _S.INCUBATING,
        (_S.READY, _E.EDIT): _S.DRAFT,
        (_S.READY, _E.STOP): _S.STOPPED,
        (_S.INCUBATING, _E.ROW_DUE): _S.READING,
        (_S.INCUBATING, _E.STOP): _S.STOPPED,
        (_S.INCUBATING, _E.FAULT): _S.FAULTED,
        (_S.READING, _E.ROW_CAPTURED): _S.ANALYZING,
        (_S.READING, _E.STOP): _S.STOPPED,
        (_S.READING, _E.FAULT): _S.FAULTED,
        (_S.ANALYZING, _E.ROW_ANALYZED): _S.INCUBATING,
        (_S.ANALYZING, _E.ROW_DUE): _S.READING,
        (_S.ANALYZING, _E.ALL_ANALYZED_CLEAN): _S.COMPLETED,
        (_S.ANALYZING, _E.ALL_ANALYZED_NEEDS_REVIEW): _S.PENDING_REVIEW,
        (_S.ANALYZING, _E.STOP): _S.STOPPED,
        (_S.ANALYZING, _E.FAULT): _S.FAULTED,
        (_S.PENDING_REVIEW, _E.REVIEW_COMPLETED): _S.COMPLETED,
        (_S.COMPLETED, _E.REOPEN_REVIEW): _S.PENDING_REVIEW,
        (_S.COMPLETED, _E.SIGN): _S.SIGNED,
        (_S.FAULTED, _E.RESUME_INCUBATION): _S.INCUBATING,
        (_S.FAULTED, _E.RESUME_READING): _S.READING,
        (_S.FAULTED, _E.RESUME_ANALYSIS): _S.ANALYZING,
        (_S.FAULTED, _E.STOP): _S.STOPPED,
    }
)
"""合法转换表：``(当前状态, 事件) → 下一状态``。"""

TERMINAL_STATES: frozenset[ExperimentState] = frozenset({_S.SIGNED, _S.STOPPED})
"""终态，不接受任何事件。"""


def next_state(state: ExperimentState, event: ExperimentEvent) -> ExperimentState:
    """计算事件发生后的状态。

    Args:
        state: 当前状态。
        event: 发生的事件。

    Returns:
        下一状态。

    Raises:
        IllegalTransitionError: 当前状态不接受该事件。
    """
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise IllegalTransitionError(state.value, event.value) from None


def allowed_events(state: ExperimentState) -> frozenset[ExperimentEvent]:
    """列出某状态接受的事件。

    Args:
        state: 要查询的状态。

    Returns:
        该状态接受的全部事件，终态返回空集合。
    """
    return frozenset(event for (source, event) in TRANSITIONS if source is state)
