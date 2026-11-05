"""领域实体：样品、试剂批次、单管结果、试验、用户和审计事件。

除 :class:`Experiment` 外都是不可变值对象。:class:`Experiment` 是聚合根，
状态和结果只能通过它的方法修改，由它保证业务前置条件。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentCheckStatus,
    ReagentKind,
    ReviewStatus,
    Role,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.errors import TransitionGuardError
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.plate import tube_to_position
from gelinspect.domain.state_machine import ExperimentEvent, ExperimentState, next_state

if TYPE_CHECKING:
    from gelinspect.domain.conclusions import ExperimentConclusion

FeatureValue = float | bool | tuple[float, float] | None
"""算法特征快照的取值类型。"""


def _require_aware(value: datetime, field_name: str) -> None:
    """要求时间带时区，保证跨平台存储和比较无歧义。

    Args:
        value: 要检查的时间。
        field_name: 字段名，用于错误信息。

    Raises:
        ValueError: 时间不带时区。
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} 必须带时区")


@dataclass(frozen=True, slots=True)
class Sample:
    """样品。

    Attributes:
        sample_id: 样品标识。
        name: 样品名称。
        serial_no: 序列号。
        batch_no: 样品批号。
    """

    sample_id: str
    name: str
    serial_no: str | None = None
    batch_no: str | None = None


@dataclass(frozen=True, slots=True)
class ReagentBatch:
    """试剂批次。

    Attributes:
        kind: 试剂种类。
        batch_no: 批号。
        expiry_date: 有效期（含当日），未知时为 None。
        registered: 是否已备案。
        labeled_sensitivity: 标示灵敏度 λ，单位 EU/mL，仅鲎试剂需要。
    """

    kind: ReagentKind
    batch_no: str
    expiry_date: date | None
    registered: bool
    labeled_sensitivity: float | None = None

    def check(self, on_date: date) -> ReagentCheckStatus:
        """校验该批次在指定日期是否可用。

        未备案优先于有效期问题报告。

        Args:
            on_date: 校验日期。

        Returns:
            校验结果。
        """
        if not self.registered:
            return ReagentCheckStatus.NOT_REGISTERED
        if self.expiry_date is None:
            return ReagentCheckStatus.EXPIRY_UNKNOWN
        if on_date > self.expiry_date:
            return ReagentCheckStatus.EXPIRED
        return ReagentCheckStatus.VALID


@dataclass(frozen=True, slots=True)
class ReagentCheckReport:
    """一次试剂校验的结果。

    Attributes:
        statuses: 每种试剂的校验结果。
    """

    statuses: Mapping[ReagentKind, ReagentCheckStatus]

    @property
    def passed(self) -> bool:
        """三种试剂是否全部合格。"""
        return all(status is ReagentCheckStatus.VALID for status in self.statuses.values())

    @property
    def event(self) -> ExperimentEvent:
        """应向试验发送的状态机事件。"""
        if self.passed:
            return ExperimentEvent.REAGENT_CHECK_PASSED
        return ExperimentEvent.REAGENT_CHECK_FAILED


def check_reagents(batches: Iterable[ReagentBatch], on_date: date) -> ReagentCheckReport:
    """校验一个试验的全部试剂。

    三种试剂都需要提供。同一种试剂有多个批次时，只要有一批不合格，该种类就不合格。

    Args:
        batches: 试验使用的试剂批次。
        on_date: 校验日期。

    Returns:
        每种试剂的校验结果。
    """
    statuses = dict.fromkeys(ReagentKind, ReagentCheckStatus.MISSING)
    seen: set[ReagentKind] = set()
    for batch in batches:
        result = batch.check(on_date)
        if batch.kind not in seen or result is not ReagentCheckStatus.VALID:
            statuses[batch.kind] = result
        seen.add(batch.kind)
    return ReagentCheckReport(statuses=MappingProxyType(statuses))


@dataclass(frozen=True, slots=True)
class TubeAssignment:
    """试验中一支试管的安排，对应结果表的一行。

    Attributes:
        tube_no: 管号，1–80。
        sample: 样品。
        role: 试管用途。
        dilution_factor: 稀释倍数。
        spiked_endotoxin: 外加内毒素。
    """

    tube_no: int
    sample: Sample
    role: TubeRole
    dilution_factor: float | None = None
    spiked_endotoxin: float | None = None

    def __post_init__(self) -> None:
        """校验管号范围。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        tube_to_position(self.tube_no)


@dataclass(frozen=True, slots=True)
class TubeResult:
    """单管结果。

    Attributes:
        tube_no: 管号，1–80。
        analysis_status: 算法状态。
        gel_state: 算法三态，无法判定时为 None。
        auto_interpretation: 按映射规则得出的判读。
        review_status: 复核状态。
        score: 算法融合评分。
        confidence: 算法置信度。
        features: 算法特征快照，软件只存储和展示。
        model_version: 产生该结果的模型版本。
        config_version: 产生该结果的阈值配置版本。
        message: 算法给出的失败原因。
        reviewed_interpretation: 人工复核结论。
        reviewed_by: 复核人用户名。
        reviewed_at: 复核时间。
        review_note: 复核备注。
        elapsed_ms: 算法分析该排的耗时（毫秒），同一排的试管取同一个值。
    """

    tube_no: int
    analysis_status: TubeAnalysisStatus
    gel_state: GelState | None
    auto_interpretation: Interpretation
    review_status: ReviewStatus
    score: float | None = None
    confidence: float | None = None
    features: Mapping[str, FeatureValue] = field(default_factory=dict)
    model_version: str | None = None
    config_version: str | None = None
    message: str | None = None
    reviewed_interpretation: Interpretation | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    elapsed_ms: float | None = None

    @classmethod
    def from_analysis(
        cls,
        *,
        tube_no: int,
        status: TubeAnalysisStatus,
        state: GelState | None,
        mapping: GelStateMapping,
        score: float | None = None,
        confidence: float | None = None,
        features: Mapping[str, FeatureValue] | None = None,
        model_version: str | None = None,
        config_version: str | None = None,
        message: str | None = None,
        elapsed_ms: float | None = None,
    ) -> TubeResult:
        """由算法输出构造单管结果，并得出判读和复核状态。

        Args:
            tube_no: 管号。
            status: 算法状态。
            state: 算法三态。
            mapping: 映射规则。
            score: 融合评分。
            confidence: 置信度。
            features: 特征快照。
            model_version: 模型版本。
            config_version: 阈值配置版本。
            message: 失败原因。
            elapsed_ms: 分析耗时（毫秒）。

        Returns:
            单管结果。判读为待复核时，复核状态为 ``PENDING``。

        Raises:
            InvalidPlatePositionError: 管号超出范围。
        """
        tube_to_position(tube_no)
        interpretation = mapping.interpret(status, state, confidence)
        if (
            message is None
            and interpretation is Interpretation.PENDING_REVIEW
            and state is not None
            and mapping.for_state(state) is not Interpretation.PENDING_REVIEW
            and mapping.is_low_confidence(confidence)
        ):
            shown = "缺失" if confidence is None else f"{confidence:.2f}"
            message = f"置信度 {shown} 低于阈值 {mapping.low_confidence_threshold:g}，转人工复核"
        return cls(
            tube_no=tube_no,
            analysis_status=status,
            gel_state=state,
            auto_interpretation=interpretation,
            review_status=(
                ReviewStatus.PENDING
                if interpretation is Interpretation.PENDING_REVIEW
                else ReviewStatus.NOT_REQUIRED
            ),
            score=score,
            confidence=confidence,
            features=dict(features or {}),
            model_version=model_version,
            config_version=config_version,
            message=message,
            elapsed_ms=elapsed_ms,
        )

    @property
    def final_interpretation(self) -> Interpretation:
        """最终判读：有人工复核结论时以复核为准。"""
        return self.reviewed_interpretation or self.auto_interpretation

    @property
    def needs_review(self) -> bool:
        """是否仍在等待人工复核。"""
        return self.final_interpretation is Interpretation.PENDING_REVIEW

    def with_review(
        self,
        *,
        interpretation: Interpretation,
        reviewed_by: str,
        reviewed_at: datetime,
        note: str | None = None,
    ) -> TubeResult:
        """生成带人工复核结论的新结果。可重复复核，以最后一次为准。

        Args:
            interpretation: 复核结论，只能是阳性或阴性。
            reviewed_by: 复核人用户名。
            reviewed_at: 复核时间，必须带时区。
            note: 复核备注。

        Returns:
            新的单管结果。

        Raises:
            ValueError: 复核结论为待复核，或时间不带时区。
        """
        if interpretation is Interpretation.PENDING_REVIEW:
            raise ValueError("复核结论只能是阳性或阴性")
        _require_aware(reviewed_at, "reviewed_at")
        return replace(
            self,
            review_status=ReviewStatus.REVIEWED,
            reviewed_interpretation=interpretation,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_note=note,
        )


@dataclass(slots=True)
class Experiment:
    """试验聚合根。

    Attributes:
        experiment_id: 试验标识。
        experiment_type: 试验类型。
        template_id: 所用模板标识。
        instrument_no: 仪器编号。
        operator: 操作人用户名。
        created_at: 创建时间，必须带时区。
        assignments: 试管安排，管号不重复。
        reagents: 试剂批次。
        state: 当前状态，只应通过 :meth:`apply` 和 :meth:`sign` 改变。
        results: 单管结果，按管号索引，只应通过方法写入。
        measured_sensitivity: 实测灵敏度 λc，只有结论为合格或不合格时才有值。
        mvd: 最大有效稀释倍数。
        endotoxin_limit: 内毒素限值。
        signed_by: 签署人用户名。
        signed_at: 签署时间。
        layout_confirmed: 创建时模板布局是否已确认（快照，之后修改模板不影响本试验）。
            默认 False：没有明确确认的布局不能得出合格或不合格。
        completed_at: 试验转为「已完成」的时间。
        conclusion: 入库的试验结论，试验完成时固化，之后修改规则或阈值不会改写它。
    """

    experiment_id: str
    experiment_type: ExperimentType
    template_id: str
    instrument_no: str
    operator: str
    created_at: datetime
    assignments: tuple[TubeAssignment, ...]
    reagents: tuple[ReagentBatch, ...] = ()
    state: ExperimentState = ExperimentState.DRAFT
    results: dict[int, TubeResult] = field(default_factory=dict)
    measured_sensitivity: float | None = None
    mvd: float | None = None
    endotoxin_limit: float | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None
    layout_confirmed: bool = False
    completed_at: datetime | None = None
    conclusion: ExperimentConclusion | None = None

    def __post_init__(self) -> None:
        """校验创建时间和管号唯一性。

        Raises:
            ValueError: 时间不带时区或管号重复。
        """
        _require_aware(self.created_at, "created_at")
        tube_nos = [assignment.tube_no for assignment in self.assignments]
        if len(set(tube_nos)) != len(tube_nos):
            raise ValueError("试验的管号重复")

    @property
    def pending_review_tubes(self) -> tuple[int, ...]:
        """仍在等待人工复核的管号，升序排列。"""
        return tuple(sorted(no for no, result in self.results.items() if result.needs_review))

    def _require_all_results(self) -> None:
        """要求每支试管都有结果。

        Raises:
            TransitionGuardError: 有试管没有结果。
        """
        missing = {assignment.tube_no for assignment in self.assignments} - set(self.results)
        if missing:
            raise TransitionGuardError(f"以下管号尚无结果：{sorted(missing)}")

    def _require_no_pending_review(self) -> None:
        """要求没有待复核的试管。

        Raises:
            TransitionGuardError: 有试管等待复核。
        """
        if self.pending_review_tubes:
            raise TransitionGuardError(f"以下管号等待复核：{list(self.pending_review_tubes)}")

    def _check_guards(self, event: ExperimentEvent) -> None:
        """检查事件的业务前置条件。

        Args:
            event: 要应用的事件。

        Raises:
            TransitionGuardError: 前置条件不满足。
        """
        if event is ExperimentEvent.START and not self.assignments:
            raise TransitionGuardError("试验没有安排任何试管")
        if event in (
            ExperimentEvent.ALL_ANALYZED_CLEAN,
            ExperimentEvent.REVIEW_COMPLETED,
            ExperimentEvent.SIGN,
        ):
            self._require_all_results()
            self._require_no_pending_review()
        if event is ExperimentEvent.ALL_ANALYZED_NEEDS_REVIEW:
            self._require_all_results()
            if not self.pending_review_tubes:
                raise TransitionGuardError("没有待复核的试管")

    def apply(self, event: ExperimentEvent) -> ExperimentState:
        """应用状态机事件。

        签署必须使用 :meth:`sign`，以保证记录签署人和时间。

        Args:
            event: 要应用的事件。

        Returns:
            转换后的状态。

        Raises:
            IllegalTransitionError: 当前状态不接受该事件。
            TransitionGuardError: 业务前置条件不满足。
        """
        target = next_state(self.state, event)
        self._check_guards(event)
        if event is ExperimentEvent.SIGN and self.signed_by is None:
            raise TransitionGuardError("签署必须通过 sign() 记录签署人")
        self.state = target
        return target

    def record_result(self, result: TubeResult) -> None:
        """写入算法产生的单管结果，只能在分析状态调用。

        Args:
            result: 单管结果。

        Raises:
            TransitionGuardError: 当前不在分析状态，或该管号不属于本试验。
        """
        if self.state is not ExperimentState.ANALYZING:
            raise TransitionGuardError(f"状态 {self.state.value} 下不能写入分析结果")
        if result.tube_no not in {assignment.tube_no for assignment in self.assignments}:
            raise TransitionGuardError(f"管号 {result.tube_no} 不属于本试验")
        self.results[result.tube_no] = result

    def review_tube(
        self,
        tube_no: int,
        *,
        interpretation: Interpretation,
        reviewed_by: str,
        reviewed_at: datetime,
        note: str | None = None,
    ) -> TubeResult:
        """记录人工复核结论，只能在待复核状态调用。

        权限由服务层用 ``Permission.RESULT_REVIEW`` 检查。

        Args:
            tube_no: 管号。
            interpretation: 复核结论，只能是阳性或阴性。
            reviewed_by: 复核人用户名。
            reviewed_at: 复核时间，必须带时区。
            note: 复核备注。

        Returns:
            更新后的单管结果。

        Raises:
            TransitionGuardError: 当前不在待复核状态，或该管号没有结果。
            ValueError: 复核结论或时间不合法。
        """
        if self.state is not ExperimentState.PENDING_REVIEW:
            raise TransitionGuardError(f"状态 {self.state.value} 下不能复核")
        if tube_no not in self.results:
            raise TransitionGuardError(f"管号 {tube_no} 没有结果")
        reviewed = self.results[tube_no].with_review(
            interpretation=interpretation,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            note=note,
        )
        self.results[tube_no] = reviewed
        return reviewed

    def sign(self, signed_by: str, signed_at: datetime) -> ExperimentState:
        """签署试验。

        权限由服务层用 ``Permission.EXPERIMENT_SIGN`` 检查。

        Args:
            signed_by: 签署人用户名。
            signed_at: 签署时间，必须带时区。

        Returns:
            转换后的状态，即已签署。

        Raises:
            IllegalTransitionError: 当前状态不接受签署。
            TransitionGuardError: 有试管没有结果或等待复核。
            ValueError: 时间不带时区。
        """
        _require_aware(signed_at, "signed_at")
        target = next_state(self.state, ExperimentEvent.SIGN)
        self._check_guards(ExperimentEvent.SIGN)
        self.signed_by = signed_by
        self.signed_at = signed_at
        self.state = target
        return target


@dataclass(frozen=True, slots=True)
class User:
    """用户。密码哈希属于数据层，不进入领域实体。

    Attributes:
        user_id: 用户标识。
        username: 登录名。
        display_name: 显示名称。
        role: 角色。
        active: 账号是否启用。
    """

    user_id: str
    username: str
    display_name: str
    role: Role
    active: bool = True


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """审计事件。序号和哈希链由数据层在写入时生成。

    Attributes:
        occurred_at: 发生时间，必须带时区。
        actor: 操作者用户名，系统事件为 ``system``。
        actor_role: 操作时的角色，系统事件为 None。
        action: 事件类型。
        detail: 操作记录文本。
        experiment_id: 关联的试验，可为空。
    """

    occurred_at: datetime
    actor: str
    actor_role: Role | None
    action: AuditAction
    detail: str
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        """校验发生时间带时区。

        Raises:
            ValueError: 时间不带时区。
        """
        _require_aware(self.occurred_at, "occurred_at")
