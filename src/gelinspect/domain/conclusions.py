"""试验结论：从单管结果算出合格、不合格、无效或待定。

纯函数，不依赖 Qt 或数据库。公式、手算例和每条规则的来源见 ``docs/features/conclusions.md``。

**灵敏度复核**的规则由任务书给出（见 :data:`SENSITIVITY_RULE`）。**干扰、复试、半定量、限度**的布局
未确认（PLAN 第 7 节第 4、14 项），这里只写好规则结构（需要哪些用途的管、条文和来源），
凭记忆的条文一律标“待 QA 核对”。这四类**没有任何产生合格或不合格的代码路径**：QA 核对条文和布局后
再实现判定，并升级规则版本。

依据和未满足的前提用 :class:`ConclusionNote` 表达：稳定的代码加参数，中文原文存库，界面按当前语言
翻译。结论入库后不再随规则变化。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from gelinspect.domain.entities import Experiment, TubeAssignment, TubeResult
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    Interpretation,
    ReagentKind,
    TubeRole,
)
from gelinspect.domain.state_machine import ExperimentState

SENSITIVITY_MULTIPLES = (2.0, 1.0, 0.5, 0.25)
"""灵敏度复核的浓度档：λ 的倍数，从高到低。"""
SENSITIVITY_SERIES_COUNT = 4
"""灵敏度复核的平行系列数。"""
LAMBDA_C_MIN_RATIO = 0.5
"""λc 下限，相对 λ，含边界。"""
LAMBDA_C_MAX_RATIO = 2.0
"""λc 上限，相对 λ，含边界。"""
_REL_TOLERANCE = 1e-9
"""比较 λ 倍数和边界时的相对容差，只用来吸收二进制浮点误差。"""


class NoteCode(StrEnum):
    """依据和前提的稳定代码，存库，界面据此翻译。"""

    LAMBDA_VALUE = "lambda_value"
    SERIES_READING = "series_reading"
    SERIES_RESULT = "series_result"
    NEGATIVE_CONTROL_READING = "negative_control_reading"
    LAMBDA_C_FORMULA = "lambda_c_formula"
    LAMBDA_C_RANGE = "lambda_c_range"
    ROLE_SUMMARY = "role_summary"
    LAYOUT_EMPTY = "layout_empty"
    LAYOUT_PRESUMED = "layout_presumed"
    LAYOUT_ROLES_UNMARKED = "layout_roles_unmarked"
    RULE_PENDING_QA = "rule_pending_qa"
    RESULT_MISSING = "result_missing"
    REVIEW_PENDING = "review_pending"
    LAMBDA_MISSING = "lambda_missing"
    CONTROL_SPIKED = "control_spiked"
    SPIKE_NOT_ON_LEVEL = "spike_not_on_level"
    LEVEL_COUNT_MISMATCH = "level_count_mismatch"
    NEGATIVE_CONTROL_MISSING = "negative_control_missing"
    HIGHEST_NOT_ALL_POSITIVE = "highest_not_all_positive"
    LOWEST_NOT_ALL_NEGATIVE = "lowest_not_all_negative"
    NEGATIVE_CONTROL_NOT_NEGATIVE = "negative_control_not_negative"
    SERIES_DISCONTINUOUS = "series_discontinuous"
    LAMBDA_C_OUT_OF_RANGE = "lambda_c_out_of_range"
    NOT_RECORDED = "not_recorded"
    STOPPED = "stopped"


NOTE_TEMPLATES: Mapping[NoteCode, str] = MappingProxyType(
    {
        NoteCode.LAMBDA_VALUE: "标示灵敏度 λ = {lam} EU/mL",
        NoteCode.SERIES_READING: "第 {series} 系列（{tubes} 号管）：{readings}",
        NoteCode.SERIES_RESULT: (
            "第 {series} 系列（{tubes} 号管）：{readings}，终点 {endpoint} EU/mL（{multiple}λ）"
        ),
        NoteCode.NEGATIVE_CONTROL_READING: "阴性对照（{tubes} 号管）：全部阴性",
        NoteCode.LAMBDA_C_FORMULA: (
            "λc = antilg(mean(lg 终点)) = antilg(({terms}) / {count}) ≈ {value}"
        ),
        NoteCode.LAMBDA_C_RANGE: "λc = {value} EU/mL，判定范围 0.5λ–2λ（含）= {low}–{high} EU/mL",
        NoteCode.ROLE_SUMMARY: (
            "{role}（{tubes} 号管）：阳性 {positive}，阴性 {negative}，待定 {other}"
        ),
        NoteCode.LAYOUT_EMPTY: "模板布局待确认：没有安排试管",
        NoteCode.LAYOUT_PRESUMED: "模板布局待确认：模板标记为推定布局，等待 QA 确认",
        NoteCode.LAYOUT_ROLES_UNMARKED: "模板布局待确认：{tubes} 号管没有标记用途",
        NoteCode.RULE_PENDING_QA: "判定规则待 QA 核对（中国药典通则 1143），不给出合格或不合格",
        NoteCode.RESULT_MISSING: "{tubes} 号管还没有结果",
        NoteCode.REVIEW_PENDING: "{tubes} 号管等待人工复核",
        NoteCode.LAMBDA_MISSING: "没有可用的鲎试剂标示灵敏度 λ",
        NoteCode.CONTROL_SPIKED: "管位与规则要求的布局不符：阴性对照 {tubes} 号管加了内毒素",
        NoteCode.SPIKE_NOT_ON_LEVEL: (
            "管位与规则要求的布局不符：{tubes} 号管的外加内毒素不是 2λ、λ、λ/2、λ/4 之一"
        ),
        NoteCode.LEVEL_COUNT_MISMATCH: (
            "管位与规则要求的布局不符：{multiple}λ 应有 {expected} 管，实际 {actual} 管"
        ),
        NoteCode.NEGATIVE_CONTROL_MISSING: "模板没有标记阴性对照",
        NoteCode.HIGHEST_NOT_ALL_POSITIVE: (
            "最大浓度 {level} EU/mL 不是全部阳性（{tubes} 号管不是阳性）"
        ),
        NoteCode.LOWEST_NOT_ALL_NEGATIVE: (
            "最低浓度 {level} EU/mL 不是全部阴性（{tubes} 号管不是阴性）"
        ),
        NoteCode.NEGATIVE_CONTROL_NOT_NEGATIVE: "阴性对照不是阴性（{tubes} 号管）",
        NoteCode.SERIES_DISCONTINUOUS: (
            "第 {series} 系列不连续（阴性之后又出现阳性），需 QA 判定；上面的 λc 只作参考"
        ),
        NoteCode.LAMBDA_C_OUT_OF_RANGE: "λc = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）",
        NoteCode.NOT_RECORDED: "这个试验完成时还没有结论功能，没有入库的结论",
        NoteCode.STOPPED: "试验已停止，不产生结论",
    }
)
"""每个代码的简体中文原文，``{参数}`` 由 :class:`ConclusionNote` 的 ``params`` 提供。"""


def format_quantity(value: float) -> str:
    """格式化浓度：最多 4 位有效数字，不带二进制浮点尾巴。

    Args:
        value: 数值。

    Returns:
        例如 ``0.5946``、``0.125``、``0.0075``。
    """
    return f"{value:.4g}"


@dataclass(frozen=True, slots=True)
class ConclusionNote:
    """一条依据或前提。

    Attributes:
        code: 稳定代码。
        params: 参数，全部已格式化为文字（管号列表、浓度等）。
    """

    code: NoteCode
    params: Mapping[str, str]

    @property
    def text(self) -> str:
        """简体中文原文，写入审计。"""
        params = dict(self.params)
        if "role" in params:
            params["role"] = _ROLE_NAMES[TubeRole(params["role"])]
        return NOTE_TEMPLATES[self.code].format(**params)


_ROLE_NAMES = {
    TubeRole.TEST: "试验管",
    TubeRole.NEGATIVE_CONTROL: "阴性对照",
    TubeRole.SAMPLE: "供试品",
    TubeRole.POSITIVE_CONTROL: "阳性对照",
    TubeRole.SAMPLE_POSITIVE_CONTROL: "供试品阳性对照",
    TubeRole.UNCONFIRMED: "用途待确认",
}


def _note(code: NoteCode, **params: object) -> ConclusionNote:
    return ConclusionNote(code, {key: str(value) for key, value in params.items()})


@dataclass(frozen=True, slots=True)
class TubeEvidence:
    """结论用到的一支试管，随结论固化。

    Attributes:
        tube_no: 管号。
        role: 用途。
        spiked_endotoxin: 外加内毒素，EU/mL。
        interpretation: 计算时的最终判读。
        reviewed: 判读是否来自人工复核。
        series: 平行系列序号（从 1 起），不属于任何系列时为 None。
    """

    tube_no: int
    role: TubeRole
    spiked_endotoxin: float | None
    interpretation: Interpretation | None
    reviewed: bool = False
    series: int | None = None


@dataclass(frozen=True, slots=True)
class ConclusionRule:
    """一类试验的规则定义。

    Attributes:
        experiment_type: 试验类型。
        version: 规则版本，规则或阈值变更时必须升级，随结论入库。
        source: 来源。
        verdict_enabled: 规则能否产生合格或不合格。凭记忆、未经 QA 核对的规则为 False。
        tracked_roles: 规则读取的用途，只读取明确标记了用途的管。
        clauses: 规则条文。凭记忆的条文都带“待 QA 核对”。
    """

    experiment_type: ExperimentType
    version: str
    source: str
    verdict_enabled: bool
    tracked_roles: tuple[TubeRole, ...]
    clauses: tuple[str, ...]


_SOURCE_1143 = "中国药典通则 1143"
_PENDING = "（凭记忆，待 QA 核对）"
_PLACEHOLDER_ROLES = (
    TubeRole.SAMPLE,
    TubeRole.SAMPLE_POSITIVE_CONTROL,
    TubeRole.POSITIVE_CONTROL,
    TubeRole.NEGATIVE_CONTROL,
)

SENSITIVITY_RULE = ConclusionRule(
    experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
    version="sensitivity-v1",
    source=f"项目任务书；{_SOURCE_1143}（条文原文待 QA 逐字核对）",
    verdict_enabled=True,
    tracked_roles=(TubeRole.TEST, TubeRole.NEGATIVE_CONTROL),
    clauses=(
        "4 个平行系列，每个系列的浓度档为 2λ、λ、λ/2、λ/4，另有阴性对照。",
        "有效性：最大浓度全部阳性、最低浓度全部阴性、阴性对照为阴性，否则无效。",
        "系列终点：从高浓度到低浓度，最后一个阳性对应的浓度。",
        "λc = antilg(mean(lg 终点))；λc 在 0.5λ–2λ（含）内为合格。",
        "不连续（阴性之后又阳性）：本软件给“待定”，由 QA 判定，这一条不是药典条文，待 QA 确认。",
    ),
)

_PENDING_RULES = (
    ConclusionRule(
        experiment_type=ExperimentType.INTERFERENCE,
        version="interference-pending-qa-v1",
        source=f"{_SOURCE_1143}，待 QA 核对",
        verdict_enabled=False,
        tracked_roles=_PLACEHOLDER_ROLES,
        clauses=(
            f"{_PENDING}用供试品溶液和检查用水分别做 2λ、λ、λ/2、λ/4 系列，比较两者的 λc。",
            f"{_PENDING}两条系列的终点几何平均值都在 0.5λ–2λ 内，判定不存在干扰。",
        ),
    ),
    ConclusionRule(
        experiment_type=ExperimentType.RETEST,
        version="retest-pending-qa-v1",
        source=f"{_SOURCE_1143}，待 QA 核对",
        verdict_enabled=False,
        tracked_roles=_PLACEHOLDER_ROLES,
        clauses=(
            f"{_PENDING}限度试验一管阳性一管阴性时复试，供试品取 4 管。",
            f"{_PENDING}复试的供试品全部阴性符合规定，否则不符合规定。",
        ),
    ),
    ConclusionRule(
        experiment_type=ExperimentType.SEMI_QUANTITATIVE,
        version="semi-quantitative-pending-qa-v1",
        source=f"{_SOURCE_1143}，待 QA 核对",
        verdict_enabled=False,
        tracked_roles=_PLACEHOLDER_ROLES,
        clauses=(
            f"{_PENDING}供试品按系列稀释，由终点计算内毒素含量。",
            f"{_PENDING}对照成立时，以含量与限值比较判定。内置模板的 16 管未能对应到记忆中的布局。",
        ),
    ),
    ConclusionRule(
        experiment_type=ExperimentType.LIMIT,
        version="limit-pending-qa-v1",
        source=f"{_SOURCE_1143}，待 QA 核对",
        verdict_enabled=False,
        tracked_roles=_PLACEHOLDER_ROLES,
        clauses=(
            f"{_PENDING}供试品、供试品阳性对照、阳性对照、阴性对照各 2 管。",
            f"{_PENDING}阴性对照全阴、两种阳性对照全阳时有效；供试品全阴符合规定，全阳不符合规定，"
            "一阳一阴复试。",
        ),
    ),
)

RULES: Mapping[ExperimentType, ConclusionRule] = MappingProxyType(
    {
        ExperimentType.SENSITIVITY_CONFIRMATION: SENSITIVITY_RULE,
        **{rule.experiment_type: rule for rule in _PENDING_RULES},
    }
)
"""五类试验的规则。"""


@dataclass(frozen=True, slots=True)
class ExperimentConclusion:
    """试验结论。

    Attributes:
        experiment_type: 试验类型。
        verdict: 结论。
        value: 数值，灵敏度复核是 λc（EU/mL）。无效时为 None；待定时可能是参考值。
        basis: 依据：可读的计算过程。
        unmet: 未满足的前提或无效原因。
        rule_version: 规则版本。
        rule_source: 规则来源。
        rule_verdict_enabled: 规则能否产生合格或不合格。
        labeled_sensitivity: 计算时的标示灵敏度 λ。
        tubes: 用到的单管。
        computed_at: 计算时间；还没有入库的临时结论为 None。
    """

    experiment_type: ExperimentType
    verdict: ConclusionVerdict
    value: float | None
    basis: tuple[ConclusionNote, ...]
    unmet: tuple[ConclusionNote, ...]
    rule_version: str
    rule_source: str
    rule_verdict_enabled: bool
    labeled_sensitivity: float | None
    tubes: tuple[TubeEvidence, ...]
    computed_at: datetime | None = None

    def summary_text(self) -> str:
        """写入审计的完整文字。

        Returns:
            结论、数值、规则版本、λ、用到的管、依据和未满足的前提。
        """
        value = "-" if self.value is None else format_quantity(self.value)
        lam = "-" if self.labeled_sensitivity is None else format_quantity(self.labeled_sensitivity)
        tubes = ",".join(str(t.tube_no) for t in self.tubes) or "-"
        parts = [f"结论 {self.verdict.value}；数值 {value}；规则 {self.rule_version}"]
        parts.append(f"λ {lam}；管 {tubes}")
        parts.extend(note.text for note in self.basis)
        parts.extend(f"未满足：{note.text}" for note in self.unmet)
        return "；".join(parts)


def labeled_sensitivity_of(experiment: Experiment) -> float | None:
    """试验鲎试剂的标示灵敏度。

    Args:
        experiment: 试验。

    Returns:
        唯一的 λ；没有、不大于 0，或多个批次的 λ 不一致时为 None。
    """
    values = {
        batch.labeled_sensitivity
        for batch in experiment.reagents
        if batch.kind is ReagentKind.LAL_REAGENT
    }
    if len(values) != 1:
        return None
    value = next(iter(values))
    return value if value is not None and value > 0 else None


def judge_lambda_c(lambda_c: float, labeled_sensitivity: float) -> ConclusionVerdict:
    """按 0.5λ–2λ（含边界）判定 λc。

    Args:
        lambda_c: 实测灵敏度。
        labeled_sensitivity: 标示灵敏度。

    Returns:
        合格或不合格。
    """
    ratio = lambda_c / labeled_sensitivity
    low = LAMBDA_C_MIN_RATIO * (1 - _REL_TOLERANCE)
    high = LAMBDA_C_MAX_RATIO * (1 + _REL_TOLERANCE)
    return ConclusionVerdict.PASS if low <= ratio <= high else ConclusionVerdict.FAIL


def lambda_c_from_endpoints(endpoints: Sequence[float]) -> float:
    """λc = antilg(mean(lg 终点))。

    Args:
        endpoints: 每个系列的终点浓度，都大于 0。

    Returns:
        λc，保留 12 位有效数字，去掉对数运算带来的二进制尾巴。

    Raises:
        ValueError: 没有终点，或终点不大于 0。
    """
    if not endpoints or any(value <= 0 for value in endpoints):
        raise ValueError(f"终点必须大于 0：{endpoints}")
    mean_lg = math.fsum(math.log10(value) for value in endpoints) / len(endpoints)
    return float(f"{10**mean_lg:.12g}")


def _tube_list(tubes: Sequence[int]) -> str:
    """把管号排成文字：连续 3 个以上合并为“起–止”，其余用顿号隔开。

    整板 96 管时逐个列出会占好几行，合并后“1–96”一眼可读。

    Args:
        tubes: 管号。

    Returns:
        例如 ``1–8、10、12–14``。
    """
    ordered = sorted(set(tubes))
    parts: list[str] = []
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1] == ordered[end] + 1:
            end += 1
        if end - index >= 2:
            parts.append(f"{ordered[index]}–{ordered[end]}")
        else:
            parts.extend(str(no) for no in ordered[index : end + 1])
        index = end + 1
    return "、".join(parts)


def _final(result: TubeResult | None) -> Interpretation | None:
    return None if result is None else result.final_interpretation


def _evidence(
    assignment: TubeAssignment, result: TubeResult | None, series: int | None = None
) -> TubeEvidence:
    return TubeEvidence(
        tube_no=assignment.tube_no,
        role=assignment.role,
        spiked_endotoxin=assignment.spiked_endotoxin,
        interpretation=_final(result),
        reviewed=result is not None and result.reviewed_interpretation is not None,
        series=series,
    )


def _result_problems(
    assignments: Sequence[TubeAssignment], results: Mapping[int, TubeResult]
) -> list[ConclusionNote]:
    """缺结果和待复核的管，任何一项都不产生最终结论。"""
    missing = [a.tube_no for a in assignments if a.tube_no not in results]
    pending = [
        a.tube_no for a in assignments if a.tube_no in results and results[a.tube_no].needs_review
    ]
    notes: list[ConclusionNote] = []
    if missing:
        notes.append(_note(NoteCode.RESULT_MISSING, tubes=_tube_list(missing)))
    if pending:
        notes.append(_note(NoteCode.REVIEW_PENDING, tubes=_tube_list(pending)))
    return notes


def _layout_problem(
    assignments: Sequence[TubeAssignment], layout_confirmed: bool
) -> ConclusionNote | None:
    unmarked = [a.tube_no for a in assignments if a.role is TubeRole.UNCONFIRMED]
    if not assignments:
        return _note(NoteCode.LAYOUT_EMPTY)
    if not layout_confirmed:
        return _note(NoteCode.LAYOUT_PRESUMED)
    if unmarked:
        return _note(NoteCode.LAYOUT_ROLES_UNMARKED, tubes=_tube_list(unmarked))
    return None


def _match_multiple(value: float, labeled: float) -> float | None:
    ratio = value / labeled
    for multiple in SENSITIVITY_MULTIPLES:
        if math.isclose(ratio, multiple, rel_tol=_REL_TOLERANCE):
            return multiple
    return None


@dataclass(frozen=True, slots=True)
class _SensitivityLayout:
    """灵敏度复核的布局：浓度档（倍数 → 按管号排序的管）和阴性对照。"""

    levels: Mapping[float, tuple[TubeAssignment, ...]]
    controls: tuple[TubeAssignment, ...]


def _sensitivity_layout(
    assignments: Sequence[TubeAssignment], labeled: float
) -> tuple[_SensitivityLayout | None, list[ConclusionNote]]:
    """把管位归入浓度档，布局与规则不符时返回原因。

    系列由“同一浓度档内按管号升序的第 k 支”定义，所以向导里换了管位也能对应上。
    """
    notes: list[ConclusionNote] = []
    controls = tuple(a for a in assignments if a.role is TubeRole.NEGATIVE_CONTROL)
    if not controls:
        notes.append(_note(NoteCode.NEGATIVE_CONTROL_MISSING))
    for control in controls:
        if control.spiked_endotoxin not in (None, 0.0):
            notes.append(_note(NoteCode.CONTROL_SPIKED, tubes=control.tube_no))
    grouped: dict[float, list[TubeAssignment]] = {m: [] for m in SENSITIVITY_MULTIPLES}
    for assignment in assignments:
        if assignment.role is TubeRole.NEGATIVE_CONTROL:
            continue
        multiple = (
            None
            if assignment.spiked_endotoxin is None or assignment.role is not TubeRole.TEST
            else _match_multiple(assignment.spiked_endotoxin, labeled)
        )
        if multiple is None:
            notes.append(_note(NoteCode.SPIKE_NOT_ON_LEVEL, tubes=assignment.tube_no))
        else:
            grouped[multiple].append(assignment)
    for multiple, tubes in grouped.items():
        if len(tubes) != SENSITIVITY_SERIES_COUNT:
            notes.append(
                _note(
                    NoteCode.LEVEL_COUNT_MISMATCH,
                    multiple=f"{multiple:g}",
                    expected=SENSITIVITY_SERIES_COUNT,
                    actual=len(tubes),
                )
            )
    if notes:
        return None, notes
    return (
        _SensitivityLayout(
            {m: tuple(sorted(t, key=lambda a: a.tube_no)) for m, t in grouped.items()}, controls
        ),
        [],
    )


_READING_MARKS = {
    Interpretation.POSITIVE: "+",
    Interpretation.NEGATIVE: "−",
    Interpretation.PENDING_REVIEW: "?",
}


def _evaluate_sensitivity(
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
    layout_confirmed: bool,
) -> ExperimentConclusion:
    rule = SENSITIVITY_RULE

    def conclude(
        verdict: ConclusionVerdict,
        basis: Sequence[ConclusionNote] = (),
        unmet: Sequence[ConclusionNote] = (),
        value: float | None = None,
        evidence: Sequence[TubeEvidence] | None = None,
    ) -> ExperimentConclusion:
        tubes = (
            tuple(_evidence(a, results.get(a.tube_no)) for a in assignments)
            if evidence is None
            else tuple(sorted(evidence, key=lambda t: t.tube_no))
        )
        return ExperimentConclusion(
            experiment_type=rule.experiment_type,
            verdict=verdict,
            value=value,
            basis=tuple(basis),
            unmet=tuple(unmet),
            rule_version=rule.version,
            rule_source=rule.source,
            rule_verdict_enabled=rule.verdict_enabled,
            labeled_sensitivity=labeled,
            tubes=tubes,
        )

    problem = _layout_problem(assignments, layout_confirmed)
    if problem is not None:
        return conclude(ConclusionVerdict.PENDING, unmet=[problem])
    if labeled is None:
        return conclude(ConclusionVerdict.PENDING, unmet=[_note(NoteCode.LAMBDA_MISSING)])
    layout, mismatch = _sensitivity_layout(assignments, labeled)
    if layout is None:
        return conclude(ConclusionVerdict.PENDING, unmet=mismatch)
    problems = _result_problems(assignments, results)
    if problems:
        return conclude(ConclusionVerdict.PENDING, unmet=problems)

    basis = [_note(NoteCode.LAMBDA_VALUE, lam=format_quantity(labeled))]
    evidence: list[TubeEvidence] = []
    for assignment in layout.controls:
        evidence.append(_evidence(assignment, results[assignment.tube_no]))
    endpoints: list[float] = []
    discontinuous: list[int] = []
    series_notes: list[ConclusionNote] = []
    for index in range(SENSITIVITY_SERIES_COUNT):
        series = index + 1
        tubes = [layout.levels[m][index] for m in SENSITIVITY_MULTIPLES]
        readings = [results[t.tube_no].final_interpretation for t in tubes]
        evidence.extend(_evidence(t, results[t.tube_no], series) for t in tubes)
        marks = "  ".join(
            f"{m:g}λ({_READING_MARKS[r]})"
            for m, r in zip(SENSITIVITY_MULTIPLES, readings, strict=True)
        )
        tube_text = _tube_list([t.tube_no for t in tubes])
        positive = [i for i, r in enumerate(readings) if r is Interpretation.POSITIVE]
        if not positive:
            series_notes.append(
                _note(NoteCode.SERIES_READING, series=series, tubes=tube_text, readings=marks)
            )
            continue
        last = positive[-1]
        concentration = tubes[last].spiked_endotoxin
        assert concentration is not None  # 布局检查已保证
        endpoints.append(concentration)
        if len(positive) != last + 1:
            discontinuous.append(series)
        series_notes.append(
            _note(
                NoteCode.SERIES_RESULT,
                series=series,
                tubes=tube_text,
                readings=marks,
                endpoint=format_quantity(concentration),
                multiple=f"{SENSITIVITY_MULTIPLES[last]:g}",
            )
        )

    basis.extend(series_notes)
    invalid = _sensitivity_invalid_reasons(layout, results)
    if invalid:
        return conclude(ConclusionVerdict.INVALID, basis, invalid, evidence=evidence)

    control_tubes = _tube_list([a.tube_no for a in layout.controls])
    basis.append(_note(NoteCode.NEGATIVE_CONTROL_READING, tubes=control_tubes))
    lambda_c = lambda_c_from_endpoints(endpoints)
    shown = format_quantity(lambda_c)
    low = format_quantity(labeled * LAMBDA_C_MIN_RATIO)
    high = format_quantity(labeled * LAMBDA_C_MAX_RATIO)
    basis.append(
        _note(
            NoteCode.LAMBDA_C_FORMULA,
            terms=" + ".join(f"lg {format_quantity(e)}" for e in endpoints),
            count=len(endpoints),
            value=shown,
        )
    )
    basis.append(_note(NoteCode.LAMBDA_C_RANGE, value=shown, low=low, high=high))
    if discontinuous:
        return conclude(
            ConclusionVerdict.PENDING,
            basis,
            [_note(NoteCode.SERIES_DISCONTINUOUS, series=s) for s in discontinuous],
            value=lambda_c,
            evidence=evidence,
        )
    verdict = judge_lambda_c(lambda_c, labeled)
    unmet = (
        []
        if verdict is ConclusionVerdict.PASS
        else [_note(NoteCode.LAMBDA_C_OUT_OF_RANGE, value=shown, low=low, high=high)]
    )
    return conclude(verdict, basis, unmet, value=lambda_c, evidence=evidence)


def _sensitivity_invalid_reasons(
    layout: _SensitivityLayout, results: Mapping[int, TubeResult]
) -> list[ConclusionNote]:
    """有效性：最大浓度全阳、最低浓度全阴、阴性对照为阴性。"""
    notes: list[ConclusionNote] = []
    highest = layout.levels[SENSITIVITY_MULTIPLES[0]]
    lowest = layout.levels[SENSITIVITY_MULTIPLES[-1]]
    not_positive = [
        t.tube_no
        for t in highest
        if results[t.tube_no].final_interpretation is not Interpretation.POSITIVE
    ]
    not_negative = [
        t.tube_no
        for t in lowest
        if results[t.tube_no].final_interpretation is not Interpretation.NEGATIVE
    ]
    control = [
        t.tube_no
        for t in layout.controls
        if results[t.tube_no].final_interpretation is not Interpretation.NEGATIVE
    ]
    if not_positive:
        level = format_quantity(highest[0].spiked_endotoxin or 0.0)
        notes.append(
            _note(NoteCode.HIGHEST_NOT_ALL_POSITIVE, level=level, tubes=_tube_list(not_positive))
        )
    if not_negative:
        level = format_quantity(lowest[0].spiked_endotoxin or 0.0)
        notes.append(
            _note(NoteCode.LOWEST_NOT_ALL_NEGATIVE, level=level, tubes=_tube_list(not_negative))
        )
    if control:
        notes.append(_note(NoteCode.NEGATIVE_CONTROL_NOT_NEGATIVE, tubes=_tube_list(control)))
    return notes


def _evaluate_pending_rule(
    rule: ConclusionRule,
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
    layout_confirmed: bool,
) -> ExperimentConclusion:
    """规则未经 QA 核对的试验：只汇总明确标记了用途的管，结论恒为待定。"""
    marked = [a for a in assignments if a.role is not TubeRole.UNCONFIRMED]
    unmet: list[ConclusionNote] = []
    problem = _layout_problem(assignments, layout_confirmed)
    if problem is not None:
        unmet.append(problem)
    unmet.extend(_result_problems(marked, results))
    unmet.append(_note(NoteCode.RULE_PENDING_QA))
    basis: list[ConclusionNote] = []
    for role in sorted({a.role for a in marked}, key=lambda r: r.value):
        group = [a for a in marked if a.role is role]
        finals = [_final(results.get(a.tube_no)) for a in group]
        positive = sum(f is Interpretation.POSITIVE for f in finals)
        negative = sum(f is Interpretation.NEGATIVE for f in finals)
        basis.append(
            _note(
                NoteCode.ROLE_SUMMARY,
                role=role.value,
                tubes=_tube_list([a.tube_no for a in group]),
                positive=positive,
                negative=negative,
                other=len(group) - positive - negative,
            )
        )
    return ExperimentConclusion(
        experiment_type=rule.experiment_type,
        verdict=ConclusionVerdict.PENDING,
        value=None,
        basis=tuple(basis),
        unmet=tuple(unmet),
        rule_version=rule.version,
        rule_source=rule.source,
        rule_verdict_enabled=rule.verdict_enabled,
        labeled_sensitivity=labeled,
        tubes=tuple(_evidence(a, results.get(a.tube_no)) for a in marked),
    )


def evaluate_conclusion(
    experiment_type: ExperimentType,
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled_sensitivity: float | None,
    *,
    layout_confirmed: bool,
) -> ExperimentConclusion:
    """由单管结果计算结论。

    Args:
        experiment_type: 试验类型。
        assignments: 试管安排。
        results: 单管结果，按管号索引。
        labeled_sensitivity: 标示灵敏度 λ。
        layout_confirmed: 模板布局是否已确认。

    Returns:
        结论，``computed_at`` 为 None。有管缺结果或待复核时一定是待定。
    """
    rule = RULES[experiment_type]
    if experiment_type is ExperimentType.SENSITIVITY_CONFIRMATION:
        return _evaluate_sensitivity(assignments, results, labeled_sensitivity, layout_confirmed)
    return _evaluate_pending_rule(rule, assignments, results, labeled_sensitivity, layout_confirmed)


def conclude_experiment(experiment: Experiment) -> ExperimentConclusion:
    """按试验当前内容计算结论，不修改试验。

    Args:
        experiment: 试验。

    Returns:
        结论，``computed_at`` 为 None。
    """
    return evaluate_conclusion(
        experiment.experiment_type,
        experiment.assignments,
        experiment.results,
        labeled_sensitivity_of(experiment),
        layout_confirmed=experiment.layout_confirmed,
    )


def provisional_conclusion(experiment: Experiment) -> ExperimentConclusion:
    """界面用的临时结论（没有入库）：试验还没完成，或完成时没有记录结论。

    已完成但没有入库结论的旧试验**不按当前规则补算**：规则和 λ 的含义可能已经变了。

    Args:
        experiment: 试验。

    Returns:
        待定结论，不含数值。
    """
    rule = RULES[experiment.experiment_type]
    current = conclude_experiment(experiment)
    extra: tuple[ConclusionNote, ...] = ()
    if experiment.state in (ExperimentState.COMPLETED, ExperimentState.SIGNED):
        extra = (_note(NoteCode.NOT_RECORDED),)
        current = ExperimentConclusion(
            experiment_type=rule.experiment_type,
            verdict=ConclusionVerdict.PENDING,
            value=None,
            basis=(),
            unmet=(),
            rule_version=rule.version,
            rule_source=rule.source,
            rule_verdict_enabled=rule.verdict_enabled,
            labeled_sensitivity=current.labeled_sensitivity,
            tubes=(),
        )
    elif experiment.state is ExperimentState.STOPPED:
        extra = (_note(NoteCode.STOPPED),)
    pending = current.verdict is ConclusionVerdict.PENDING
    return ExperimentConclusion(
        experiment_type=current.experiment_type,
        verdict=ConclusionVerdict.PENDING,
        value=None,
        basis=current.basis if pending else (),
        unmet=(*extra, *current.unmet),
        rule_version=current.rule_version,
        rule_source=current.rule_source,
        rule_verdict_enabled=current.rule_verdict_enabled,
        labeled_sensitivity=current.labeled_sensitivity,
        tubes=current.tubes,
    )


def finalize_conclusion(experiment: Experiment, at: datetime) -> ExperimentConclusion:
    """试验转为已完成时调用：计算结论并固化到试验聚合。

    只改内存，由调用方与“已完成”状态在同一事务写库。只有合格或不合格才写实测灵敏度：
    无效和待定不能被当作实测值。

    Args:
        experiment: 已转为已完成的试验。
        at: 完成时间，必须带时区。

    Returns:
        结论。

    Raises:
        ValueError: 试验不是已完成状态，或时间不带时区。
    """
    if experiment.state is not ExperimentState.COMPLETED:
        raise ValueError(f"试验状态是 {experiment.state.value}，不是已完成")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("完成时间必须带时区")
    computed = conclude_experiment(experiment)
    conclusion = ExperimentConclusion(
        experiment_type=computed.experiment_type,
        verdict=computed.verdict,
        value=computed.value,
        basis=computed.basis,
        unmet=computed.unmet,
        rule_version=computed.rule_version,
        rule_source=computed.rule_source,
        rule_verdict_enabled=computed.rule_verdict_enabled,
        labeled_sensitivity=computed.labeled_sensitivity,
        tubes=computed.tubes,
        computed_at=at,
    )
    experiment.conclusion = conclusion
    experiment.completed_at = at
    final = computed.verdict in (ConclusionVerdict.PASS, ConclusionVerdict.FAIL)
    experiment.measured_sensitivity = computed.value if final else None
    return conclusion


def finalize_completion(
    experiment: Experiment, at: datetime, *, conclusions_enabled: bool
) -> ExperimentConclusion | None:
    """试验转为已完成时调用：结论功能打开时计算并固化结论，关闭时只记完成时间。

    关闭时不计算、不留下任何结论或实测灵敏度（判定规则尚未确认，不能留下未确认的判定）。

    Args:
        experiment: 已转为已完成的试验。
        at: 完成时间，必须带时区。
        conclusions_enabled: 结论功能是否打开。

    Returns:
        固化的结论；功能关闭时为 None。

    Raises:
        ValueError: 试验不是已完成状态，或时间不带时区。
    """
    if conclusions_enabled:
        return finalize_conclusion(experiment, at)
    if experiment.state is not ExperimentState.COMPLETED:
        raise ValueError(f"试验状态是 {experiment.state.value}，不是已完成")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("完成时间必须带时区")
    experiment.conclusion = None
    experiment.measured_sensitivity = None
    experiment.completed_at = at
    return None


def conclusion_payload(conclusion: ExperimentConclusion) -> dict[str, Any]:
    """结论中不单独成列的内容，转成可存 JSON 的字典。

    Args:
        conclusion: 结论。

    Returns:
        依据、前提（代码、参数和当时的中文原文）、来源和用到的单管。
    """

    def notes(items: tuple[ConclusionNote, ...]) -> list[dict[str, Any]]:
        return [{"code": n.code.value, "params": dict(n.params), "text": n.text} for n in items]

    return {
        "basis": notes(conclusion.basis),
        "unmet": notes(conclusion.unmet),
        "rule_source": conclusion.rule_source,
        "rule_verdict_enabled": conclusion.rule_verdict_enabled,
        "tubes": [
            {
                "tube_no": t.tube_no,
                "role": t.role.value,
                "spiked_endotoxin": t.spiked_endotoxin,
                "interpretation": None if t.interpretation is None else t.interpretation.value,
                "reviewed": t.reviewed,
                "series": t.series,
            }
            for t in conclusion.tubes
        ],
    }


def conclusion_from_payload(
    *,
    experiment_type: ExperimentType,
    verdict: ConclusionVerdict,
    value: float | None,
    rule_version: str,
    labeled_sensitivity: float | None,
    computed_at: datetime | None,
    payload: Mapping[str, Any],
) -> ExperimentConclusion:
    """从入库内容还原结论。不重新计算：历史结论不随规则变化。

    Args:
        experiment_type: 试验类型。
        verdict: 结论。
        value: 数值。
        rule_version: 规则版本。
        labeled_sensitivity: 当时的 λ。
        computed_at: 计算时间。
        payload: :func:`conclusion_payload` 生成的内容。

    Returns:
        结论。
    """

    def notes(key: str) -> tuple[ConclusionNote, ...]:
        return tuple(
            ConclusionNote(NoteCode(item["code"]), dict(item["params"])) for item in payload[key]
        )

    return ExperimentConclusion(
        experiment_type=experiment_type,
        verdict=verdict,
        value=value,
        basis=notes("basis"),
        unmet=notes("unmet"),
        rule_version=rule_version,
        rule_source=str(payload["rule_source"]),
        rule_verdict_enabled=bool(payload["rule_verdict_enabled"]),
        labeled_sensitivity=labeled_sensitivity,
        tubes=tuple(
            TubeEvidence(
                tube_no=int(t["tube_no"]),
                role=TubeRole(t["role"]),
                spiked_endotoxin=t["spiked_endotoxin"],
                interpretation=(
                    None if t["interpretation"] is None else Interpretation(t["interpretation"])
                ),
                reviewed=bool(t["reviewed"]),
                series=t["series"],
            )
            for t in payload["tubes"]
        ),
        computed_at=computed_at,
    )
