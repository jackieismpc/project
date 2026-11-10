"""试验结论：从单管结果算出合格、不合格、无效或待定。

纯函数，不依赖 Qt 或数据库。规则按中国药典通则 1143 凝胶法：

* 灵敏度复核：λc = antilg(mean(lg 终点))，0.5λ–2λ 为合格；
* 干扰试验：检查用水系列的 Es 在 0.5λ–2λ 内试验有效，供试品系列的 Et 在 0.5Es–2Es 内
  为无干扰（合格）；
* 限度试验：对照成立时供试品全阴为符合规定（合格），全阳为不符合规定（不合格），初试一阳一阴需复试；
  复试（供试品 4 管）全阴符合规定，否则不符合规定；
* 半定量试验：每个系列的浓度 = 终点稀释倍数 × λ，都小于内毒素限值为符合规定，任何一个不小于限值为
  不符合规定；浓度的几何平均值为供试品内毒素浓度。

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
LIMIT_INITIAL_SAMPLES = 2
"""限度试验初试的供试品管数；一阳一阴时需复试。"""
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
    GROUP_MISSING = "group_missing"
    GROUP_NOT_NEGATIVE = "group_not_negative"
    GROUP_NOT_POSITIVE = "group_not_positive"
    GROUP_ALL_NEGATIVE = "group_all_negative"
    GROUP_ALL_POSITIVE = "group_all_positive"
    LEVEL_COUNT_UNEVEN = "level_count_uneven"
    ROLE_SERIES_RESULT = "role_series_result"
    ROLE_SERIES_NO_POSITIVE = "role_series_no_positive"
    ROLE_SERIES_DISCONTINUOUS = "role_series_discontinuous"
    ES_VALUE = "es_value"
    ES_OUT_OF_RANGE = "es_out_of_range"
    ET_VALUE = "et_value"
    NO_INTERFERENCE = "no_interference"
    INTERFERENCE_PRESENT = "interference_present"
    INTERFERENCE_NO_ENDPOINT = "interference_no_endpoint"
    LIMIT_COMPLIES = "limit_complies"
    LIMIT_NOT_COMPLIES = "limit_not_complies"
    LIMIT_RETEST = "limit_retest"
    DILUTION_MISSING = "dilution_missing"
    SAMPLE_SERIES_RESULT = "sample_series_result"
    SAMPLE_SERIES_BELOW = "sample_series_below"
    SAMPLE_SERIES_ABOVE = "sample_series_above"
    SAMPLE_CONCENTRATION = "sample_concentration"
    ENDOTOXIN_LIMIT_MISSING = "endotoxin_limit_missing"
    CONCENTRATION_BELOW_LIMIT = "concentration_below_limit"
    CONCENTRATION_NOT_BELOW_LIMIT = "concentration_not_below_limit"
    CONCENTRATION_UNDETERMINED = "concentration_undetermined"
    GENERAL_SUMMARY = "general_summary"


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
        NoteCode.LAYOUT_EMPTY: "没有安排试管",
        NoteCode.LAYOUT_PRESUMED: "试验使用的旧模板没有标明每管用途，不能判定",
        NoteCode.LAYOUT_ROLES_UNMARKED: "{tubes} 号管没有指定用途",
        NoteCode.RULE_PENDING_QA: "旧版本软件没有这类试验的判定规则，不给出合格或不合格",
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
            "第 {series} 系列不连续（阴性之后又出现阳性），需人工判定；上面的 λc 只作参考"
        ),
        NoteCode.LAMBDA_C_OUT_OF_RANGE: "λc = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）",
        NoteCode.NOT_RECORDED: "这个试验完成时还没有结论功能，没有入库的结论",
        NoteCode.STOPPED: "试验已停止，不产生结论",
        NoteCode.GROUP_MISSING: "模板没有标记{role}",
        NoteCode.GROUP_NOT_NEGATIVE: "{role}不是全部阴性（{tubes} 号管）",
        NoteCode.GROUP_NOT_POSITIVE: "{role}不是全部阳性（{tubes} 号管）",
        NoteCode.GROUP_ALL_NEGATIVE: "{role}（{tubes} 号管）：全部阴性",
        NoteCode.GROUP_ALL_POSITIVE: "{role}（{tubes} 号管）：全部阳性",
        NoteCode.LEVEL_COUNT_UNEVEN: "{role}：各浓度或稀释度的管数应相同，实际为 {counts}",
        NoteCode.ROLE_SERIES_RESULT: (
            "{role}第 {series} 系列（{tubes} 号管）：{readings}，终点 {endpoint} EU/mL"
        ),
        NoteCode.ROLE_SERIES_NO_POSITIVE: (
            "{role}第 {series} 系列（{tubes} 号管）：{readings}，没有阳性"
        ),
        NoteCode.ROLE_SERIES_DISCONTINUOUS: (
            "{role}第 {series} 系列不连续（阴性之后又出现阳性），需人工判定"
        ),
        NoteCode.ES_VALUE: (
            "Es = antilg(mean(lg 终点)) ≈ {value} EU/mL，应在 0.5λ–2λ（{low}–{high} EU/mL）内"
        ),
        NoteCode.ES_OUT_OF_RANGE: "Es = {value} EU/mL 超出 0.5λ–2λ（{low}–{high} EU/mL）",
        NoteCode.ET_VALUE: (
            "Et = antilg(mean(lg 终点)) ≈ {value} EU/mL，判定范围 0.5Es–2Es（{low}–{high} EU/mL）"
        ),
        NoteCode.NO_INTERFERENCE: "Et 在 0.5Es–2Es 内：供试品在该浓度下无干扰作用",
        NoteCode.INTERFERENCE_PRESENT: "Et 超出 0.5Es–2Es：供试品在该浓度下存在干扰作用",
        NoteCode.INTERFERENCE_NO_ENDPOINT: (
            "供试品内毒素系列第 {series} 系列没有阳性，无法计算 Et：供试品在该浓度下存在干扰作用"
        ),
        NoteCode.LIMIT_COMPLIES: "供试品（{tubes} 号管）全部阴性：符合规定",
        NoteCode.LIMIT_NOT_COMPLIES: "供试品（{tubes} 号管）有阳性：不符合规定",
        NoteCode.LIMIT_RETEST: "供试品两管一阳一阴，需用“限度试验（复试）”模板复试",
        NoteCode.DILUTION_MISSING: "供试品 {tubes} 号管没有稀释倍数",
        NoteCode.SAMPLE_SERIES_RESULT: (
            "供试品第 {series} 系列（{tubes} 号管）：{readings}，终点稀释 {dilution} 倍，"
            "浓度 {value} EU/mL"
        ),
        NoteCode.SAMPLE_SERIES_BELOW: (
            "供试品第 {series} 系列（{tubes} 号管）：全部阴性，浓度 < {value} EU/mL"
        ),
        NoteCode.SAMPLE_SERIES_ABOVE: (
            "供试品第 {series} 系列（{tubes} 号管）：全部阳性，浓度 ≥ {value} EU/mL"
        ),
        NoteCode.SAMPLE_CONCENTRATION: (
            "供试品内毒素浓度 = antilg(mean(lg 各系列浓度)) ≈ {value} EU/mL"
        ),
        NoteCode.ENDOTOXIN_LIMIT_MISSING: "没有录入内毒素限值，无法判定是否符合规定",
        NoteCode.CONCENTRATION_BELOW_LIMIT: "各系列浓度均小于内毒素限值 {limit} EU/mL：符合规定",
        NoteCode.CONCENTRATION_NOT_BELOW_LIMIT: (
            "第 {series} 系列浓度不小于内毒素限值 {limit} EU/mL：不符合规定"
        ),
        NoteCode.GENERAL_SUMMARY: (
            "共 {total} 管：阳性 {positive}，阴性 {negative}；通用检测按每管阴阳性出具结果，"
            "不做试验级判定"
        ),
        NoteCode.CONCENTRATION_UNDETERMINED: (
            "第 {series} 系列无法与内毒素限值 {limit} EU/mL 比较，需调整稀释倍数后重新试验"
        ),
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
    TubeRole.TEST: "内毒素标准系列",
    TubeRole.NEGATIVE_CONTROL: "阴性对照",
    TubeRole.SAMPLE: "供试品",
    TubeRole.POSITIVE_CONTROL: "阳性对照",
    TubeRole.SAMPLE_POSITIVE_CONTROL: "供试品阳性对照",
    TubeRole.SAMPLE_SERIES: "供试品内毒素系列",
    TubeRole.UNCONFIRMED: "未指定",
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
        verdict_enabled: 规则能否产生合格或不合格。
        tracked_roles: 规则读取的用途，只读取明确标记了用途的管。
        clauses: 规则条文，界面和文档展示用。
    """

    experiment_type: ExperimentType
    version: str
    source: str
    verdict_enabled: bool
    tracked_roles: tuple[TubeRole, ...]
    clauses: tuple[str, ...]


_SOURCE_1143 = "中国药典通则 1143"
SENSITIVITY_RULE = ConclusionRule(
    experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
    version="sensitivity-v1",
    source=_SOURCE_1143,
    verdict_enabled=True,
    tracked_roles=(TubeRole.TEST, TubeRole.NEGATIVE_CONTROL),
    clauses=(
        "2λ、λ、0.5λ、0.25λ 四个浓度，每个浓度平行 4 管，另做阴性对照。",
        "有效性：最大浓度全部阳性、最低浓度全部阴性、阴性对照全部阴性，否则无效。",
        "系列终点：从高浓度到低浓度，最后一个阳性对应的浓度。",
        "λc = antilg(mean(lg 终点))；λc 在 0.5λ–2λ（含）内为合格。",
        "系列不连续（阴性之后又出现阳性）时给出参考值，结论待定，由人工判定。",
    ),
)

INTERFERENCE_RULE = ConclusionRule(
    experiment_type=ExperimentType.INTERFERENCE,
    version="interference-v1",
    source=_SOURCE_1143,
    verdict_enabled=True,
    tracked_roles=(
        TubeRole.SAMPLE,
        TubeRole.SAMPLE_SERIES,
        TubeRole.TEST,
        TubeRole.NEGATIVE_CONTROL,
    ),
    clauses=(
        "A 供试品、D 阴性对照全部阴性，且 C 检查用水系列符合灵敏度复核要求时，试验有效。",
        "Es、Et 分别为 C、B 系列终点浓度的几何平均值；Es 应在 0.5λ–2λ 内。",
        "Et 在 0.5Es–2Es（含）内，供试品在该浓度下无干扰作用（合格），否则存在干扰（不合格）。",
    ),
)

LIMIT_RULE = ConclusionRule(
    experiment_type=ExperimentType.LIMIT,
    version="limit-v1",
    source=_SOURCE_1143,
    verdict_enabled=True,
    tracked_roles=(
        TubeRole.SAMPLE,
        TubeRole.SAMPLE_POSITIVE_CONTROL,
        TubeRole.POSITIVE_CONTROL,
        TubeRole.NEGATIVE_CONTROL,
    ),
    clauses=(
        "D 阴性对照全部阴性、B 供试品阳性对照和 C 阳性对照全部阳性时，试验有效。",
        "初试供试品 2 管：全部阴性符合规定，全部阳性不符合规定，一阳一阴需复试。",
        "复试供试品 4 管：全部阴性符合规定，否则不符合规定。",
    ),
)

SEMI_QUANTITATIVE_RULE = ConclusionRule(
    experiment_type=ExperimentType.SEMI_QUANTITATIVE,
    version="semi-quantitative-v1",
    source=_SOURCE_1143,
    verdict_enabled=True,
    tracked_roles=(
        TubeRole.SAMPLE,
        TubeRole.SAMPLE_POSITIVE_CONTROL,
        TubeRole.TEST,
        TubeRole.NEGATIVE_CONTROL,
    ),
    clauses=(
        (
            "D 阴性对照全部阴性、B 供试品阳性对照全部阳性、"
            "C 检查用水系列的 Es 在 0.5λ–2λ 内时，试验有效。"
        ),
        "供试品每个系列的浓度 = 终点稀释倍数 × λ；全部阴性记为小于最小稀释倍数 × λ。",
        "各系列浓度都小于内毒素限值为符合规定，任何系列不小于限值为不符合规定。",
        "各系列浓度的几何平均值为供试品溶液的内毒素浓度。",
    ),
)

GENERAL_RULE = ConclusionRule(
    experiment_type=ExperimentType.GENERAL,
    version="general-v1",
    source="通用检测，按每管阴阳性出具结果",
    verdict_enabled=False,
    tracked_roles=(TubeRole.SAMPLE,),
    clauses=(
        "每支管按算法凝固状态标记阴阳性：完全凝固阳性，未凝固阴性，部分凝固待人工复核。",
        "不做试验级判定，结论为“不适用”；全部待复核处理完后即可签署。",
    ),
)

RULES: Mapping[ExperimentType, ConclusionRule] = MappingProxyType(
    {
        ExperimentType.GENERAL: GENERAL_RULE,
        ExperimentType.SENSITIVITY_CONFIRMATION: SENSITIVITY_RULE,
        ExperimentType.INTERFERENCE: INTERFERENCE_RULE,
        ExperimentType.LIMIT: LIMIT_RULE,
        ExperimentType.SEMI_QUANTITATIVE: SEMI_QUANTITATIVE_RULE,
    }
)
"""各类试验的规则。限度试验的复试使用限度试验的规则；通用检测不做试验级判定。"""


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


def _role_name(role: TubeRole) -> str:
    return role.value


def _group(assignments: Sequence[TubeAssignment], role: TubeRole) -> list[TubeAssignment]:
    return sorted((a for a in assignments if a.role is role), key=lambda a: a.tube_no)


def _readings(
    tubes: Sequence[TubeAssignment], results: Mapping[int, TubeResult]
) -> list[Interpretation | None]:
    return [results[t.tube_no].final_interpretation for t in tubes]


def _all(
    tubes: Sequence[TubeAssignment], results: Mapping[int, TubeResult], value: Interpretation
) -> list[int]:
    """不是 ``value`` 的管号。"""
    return [t.tube_no for t in tubes if results[t.tube_no].final_interpretation is not value]


def _geometric_mean(values: Sequence[float]) -> float:
    mean_lg = math.fsum(math.log10(v) for v in values) / len(values)
    return float(f"{10**mean_lg:.12g}")


def _within(value: float, low: float, high: float) -> bool:
    return low * (1 - _REL_TOLERANCE) <= value <= high * (1 + _REL_TOLERANCE)


def _spike_levels(
    group: Sequence[TubeAssignment], role: TubeRole, labeled: float
) -> tuple[list[list[TubeAssignment]] | None, list[ConclusionNote]]:
    """把内毒素系列按 2λ、λ、λ/2、λ/4 分档，每档管数相同；返回各档（高到低）和不符的原因。"""
    notes: list[ConclusionNote] = []
    grouped: dict[float, list[TubeAssignment]] = {m: [] for m in SENSITIVITY_MULTIPLES}
    for tube in group:
        multiple = (
            None
            if tube.spiked_endotoxin is None
            else _match_multiple(tube.spiked_endotoxin, labeled)
        )
        if multiple is None:
            notes.append(_note(NoteCode.SPIKE_NOT_ON_LEVEL, tubes=tube.tube_no))
        else:
            grouped[multiple].append(tube)
    counts = [len(grouped[m]) for m in SENSITIVITY_MULTIPLES]
    if len(set(counts)) != 1 or counts[0] == 0:
        notes.append(
            _note(
                NoteCode.LEVEL_COUNT_UNEVEN,
                role=_role_name(role),
                counts="、".join(
                    f"{m:g}λ {c} 管" for m, c in zip(SENSITIVITY_MULTIPLES, counts, strict=True)
                ),
            )
        )
    if notes:
        return None, notes
    return [sorted(grouped[m], key=lambda a: a.tube_no) for m in SENSITIVITY_MULTIPLES], []


@dataclass(frozen=True, slots=True)
class _SeriesOutcome:
    """一组内毒素系列的终点。"""

    endpoints: tuple[float | None, ...]
    discontinuous: tuple[int, ...]
    notes: tuple[ConclusionNote, ...]
    evidence: tuple[TubeEvidence, ...]


def _spike_series(
    levels: Sequence[Sequence[TubeAssignment]],
    results: Mapping[int, TubeResult],
    role: TubeRole,
) -> _SeriesOutcome:
    """同一浓度内按管号排第 k 支的组成第 k 个系列，终点为最后一个阳性对应的浓度。"""
    endpoints: list[float | None] = []
    discontinuous: list[int] = []
    notes: list[ConclusionNote] = []
    evidence: list[TubeEvidence] = []
    for index in range(len(levels[0])):
        series = index + 1
        tubes = [level[index] for level in levels]
        readings = _readings(tubes, results)
        evidence.extend(_evidence(t, results[t.tube_no], series) for t in tubes)
        marks = "  ".join(
            f"{m:g}λ({_READING_MARKS.get(r, '?') if r else '?'})"
            for m, r in zip(SENSITIVITY_MULTIPLES, readings, strict=True)
        )
        tube_text = _tube_list([t.tube_no for t in tubes])
        positive = [i for i, r in enumerate(readings) if r is Interpretation.POSITIVE]
        if not positive:
            endpoints.append(None)
            notes.append(
                _note(
                    NoteCode.ROLE_SERIES_NO_POSITIVE,
                    role=_role_name(role),
                    series=series,
                    tubes=tube_text,
                    readings=marks,
                )
            )
            continue
        last = positive[-1]
        concentration = tubes[last].spiked_endotoxin
        assert concentration is not None
        endpoints.append(concentration)
        if len(positive) != last + 1:
            discontinuous.append(series)
        notes.append(
            _note(
                NoteCode.ROLE_SERIES_RESULT,
                role=_role_name(role),
                series=series,
                tubes=tube_text,
                readings=marks,
                endpoint=format_quantity(concentration),
            )
        )
    return _SeriesOutcome(tuple(endpoints), tuple(discontinuous), tuple(notes), tuple(evidence))


def _standard_validity(
    levels: Sequence[Sequence[TubeAssignment]],
    results: Mapping[int, TubeResult],
) -> list[ConclusionNote]:
    """检查用水系列的有效性：最大浓度全阳、最低浓度全阴。"""
    notes: list[ConclusionNote] = []
    not_positive = _all(levels[0], results, Interpretation.POSITIVE)
    not_negative = _all(levels[-1], results, Interpretation.NEGATIVE)
    if not_positive:
        notes.append(
            _note(
                NoteCode.HIGHEST_NOT_ALL_POSITIVE,
                level=format_quantity(levels[0][0].spiked_endotoxin or 0.0),
                tubes=_tube_list(not_positive),
            )
        )
    if not_negative:
        notes.append(
            _note(
                NoteCode.LOWEST_NOT_ALL_NEGATIVE,
                level=format_quantity(levels[-1][0].spiked_endotoxin or 0.0),
                tubes=_tube_list(not_negative),
            )
        )
    return notes


def _check_group(
    tubes: Sequence[TubeAssignment],
    role: TubeRole,
    results: Mapping[int, TubeResult],
    expected: Interpretation,
) -> tuple[ConclusionNote | None, ConclusionNote]:
    """一组对照管应全阴或全阳：返回（不满足的原因或 None，依据）。"""
    tube_text = _tube_list([t.tube_no for t in tubes])
    wrong = _all(tubes, results, expected)
    if expected is Interpretation.NEGATIVE:
        basis = _note(NoteCode.GROUP_ALL_NEGATIVE, role=_role_name(role), tubes=tube_text)
        code = NoteCode.GROUP_NOT_NEGATIVE
    else:
        basis = _note(NoteCode.GROUP_ALL_POSITIVE, role=_role_name(role), tubes=tube_text)
        code = NoteCode.GROUP_ALL_POSITIVE if not wrong else NoteCode.GROUP_NOT_POSITIVE
    problem = None if not wrong else _note(code, role=_role_name(role), tubes=_tube_list(wrong))
    return problem, basis


class _Builder:
    """收集依据、前提和单管证据，最后生成结论。"""

    def __init__(
        self,
        rule: ConclusionRule,
        assignments: Sequence[TubeAssignment],
        results: Mapping[int, TubeResult],
        labeled: float | None,
    ) -> None:
        self.rule = rule
        self.assignments = assignments
        self.results = results
        self.labeled = labeled
        self.basis: list[ConclusionNote] = []
        self.evidence: dict[int, TubeEvidence] = {}

    def add_evidence(self, items: Sequence[TubeEvidence]) -> None:
        for item in items:
            self.evidence[item.tube_no] = item

    def done(
        self,
        verdict: ConclusionVerdict,
        unmet: Sequence[ConclusionNote] = (),
        value: float | None = None,
    ) -> ExperimentConclusion:
        tubes = {a.tube_no: _evidence(a, self.results.get(a.tube_no)) for a in self.assignments}
        tubes.update(self.evidence)
        return ExperimentConclusion(
            experiment_type=self.rule.experiment_type,
            verdict=verdict,
            value=value,
            basis=tuple(self.basis),
            unmet=tuple(unmet),
            rule_version=self.rule.version,
            rule_source=self.rule.source,
            rule_verdict_enabled=self.rule.verdict_enabled,
            labeled_sensitivity=self.labeled,
            tubes=tuple(tubes[no] for no in sorted(tubes)),
        )


def _common_prerequisites(
    build: _Builder,
    layout_confirmed: bool,
    roles: Sequence[TubeRole],
    *,
    needs_lambda: bool,
) -> list[ConclusionNote]:
    """布局、λ、必需的用途组和单管结果是否齐全。"""
    problem = _layout_problem(build.assignments, layout_confirmed)
    if problem is not None:
        return [problem]
    if needs_lambda and build.labeled is None:
        return [_note(NoteCode.LAMBDA_MISSING)]
    missing = [
        _note(NoteCode.GROUP_MISSING, role=_role_name(role))
        for role in roles
        if not _group(build.assignments, role)
    ]
    if missing:
        return missing
    return _result_problems(build.assignments, build.results)


def _evaluate_interference(
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
    layout_confirmed: bool,
) -> ExperimentConclusion:
    build = _Builder(INTERFERENCE_RULE, assignments, results, labeled)
    roles = (TubeRole.SAMPLE, TubeRole.SAMPLE_SERIES, TubeRole.TEST, TubeRole.NEGATIVE_CONTROL)
    unmet = _common_prerequisites(build, layout_confirmed, roles, needs_lambda=True)
    if unmet:
        return build.done(ConclusionVerdict.PENDING, unmet)
    assert labeled is not None
    sample_levels, notes_b = _spike_levels(
        _group(assignments, TubeRole.SAMPLE_SERIES), TubeRole.SAMPLE_SERIES, labeled
    )
    water_levels, notes_c = _spike_levels(
        _group(assignments, TubeRole.TEST), TubeRole.TEST, labeled
    )
    if sample_levels is None or water_levels is None:
        return build.done(ConclusionVerdict.PENDING, [*notes_b, *notes_c])
    build.basis.append(_note(NoteCode.LAMBDA_VALUE, lam=format_quantity(labeled)))

    invalid: list[ConclusionNote] = []
    for role in (TubeRole.SAMPLE, TubeRole.NEGATIVE_CONTROL):
        problem, basis = _check_group(
            _group(assignments, role), role, results, Interpretation.NEGATIVE
        )
        build.basis.append(basis) if problem is None else invalid.append(problem)
    water = _spike_series(water_levels, results, TubeRole.TEST)
    build.basis.extend(water.notes)
    build.add_evidence(water.evidence)
    invalid.extend(_standard_validity(water_levels, results))
    if invalid:
        return build.done(ConclusionVerdict.INVALID, invalid)
    if water.discontinuous:
        return build.done(
            ConclusionVerdict.PENDING,
            [
                _note(NoteCode.ROLE_SERIES_DISCONTINUOUS, role=_role_name(TubeRole.TEST), series=s)
                for s in water.discontinuous
            ],
        )
    es = _geometric_mean([e for e in water.endpoints if e is not None])
    low, high = labeled * LAMBDA_C_MIN_RATIO, labeled * LAMBDA_C_MAX_RATIO
    es_note = dict(value=format_quantity(es), low=format_quantity(low), high=format_quantity(high))
    build.basis.append(_note(NoteCode.ES_VALUE, **es_note))
    if not _within(es, low, high):
        return build.done(ConclusionVerdict.INVALID, [_note(NoteCode.ES_OUT_OF_RANGE, **es_note)])

    sample = _spike_series(sample_levels, results, TubeRole.SAMPLE_SERIES)
    build.basis.extend(sample.notes)
    build.add_evidence(sample.evidence)
    if sample.discontinuous:
        return build.done(
            ConclusionVerdict.PENDING,
            [
                _note(
                    NoteCode.ROLE_SERIES_DISCONTINUOUS,
                    role=_role_name(TubeRole.SAMPLE_SERIES),
                    series=s,
                )
                for s in sample.discontinuous
            ],
        )
    no_endpoint = [i + 1 for i, e in enumerate(sample.endpoints) if e is None]
    if no_endpoint:
        return build.done(
            ConclusionVerdict.FAIL,
            [_note(NoteCode.INTERFERENCE_NO_ENDPOINT, series=s) for s in no_endpoint],
        )
    et = _geometric_mean([e for e in sample.endpoints if e is not None])
    build.basis.append(
        _note(
            NoteCode.ET_VALUE,
            value=format_quantity(et),
            low=format_quantity(es * 0.5),
            high=format_quantity(es * 2),
        )
    )
    if _within(et, es * 0.5, es * 2):
        build.basis.append(_note(NoteCode.NO_INTERFERENCE))
        return build.done(ConclusionVerdict.PASS, value=et)
    return build.done(ConclusionVerdict.FAIL, [_note(NoteCode.INTERFERENCE_PRESENT)], value=et)


def _evaluate_limit(
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
    layout_confirmed: bool,
) -> ExperimentConclusion:
    build = _Builder(LIMIT_RULE, assignments, results, labeled)
    roles = (
        TubeRole.SAMPLE,
        TubeRole.SAMPLE_POSITIVE_CONTROL,
        TubeRole.POSITIVE_CONTROL,
        TubeRole.NEGATIVE_CONTROL,
    )
    unmet = _common_prerequisites(build, layout_confirmed, roles, needs_lambda=False)
    if unmet:
        return build.done(ConclusionVerdict.PENDING, unmet)
    invalid: list[ConclusionNote] = []
    for role, expected in (
        (TubeRole.NEGATIVE_CONTROL, Interpretation.NEGATIVE),
        (TubeRole.SAMPLE_POSITIVE_CONTROL, Interpretation.POSITIVE),
        (TubeRole.POSITIVE_CONTROL, Interpretation.POSITIVE),
    ):
        problem, basis = _check_group(_group(assignments, role), role, results, expected)
        build.basis.append(basis) if problem is None else invalid.append(problem)
    if invalid:
        return build.done(ConclusionVerdict.INVALID, invalid)
    samples = _group(assignments, TubeRole.SAMPLE)
    tube_text = _tube_list([t.tube_no for t in samples])
    positive = [
        t.tube_no
        for t in samples
        if results[t.tube_no].final_interpretation is Interpretation.POSITIVE
    ]
    if not positive:
        build.basis.append(_note(NoteCode.LIMIT_COMPLIES, tubes=tube_text))
        return build.done(ConclusionVerdict.PASS)
    build.basis.append(
        _note(
            NoteCode.ROLE_SUMMARY,
            role=TubeRole.SAMPLE.value,
            tubes=tube_text,
            positive=len(positive),
            negative=len(samples) - len(positive),
            other=0,
        )
    )
    if len(samples) == LIMIT_INITIAL_SAMPLES and len(positive) == 1:
        return build.done(ConclusionVerdict.PENDING, [_note(NoteCode.LIMIT_RETEST)])
    return build.done(ConclusionVerdict.FAIL, [_note(NoteCode.LIMIT_NOT_COMPLIES, tubes=tube_text)])


def _evaluate_semi_quantitative(
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
    layout_confirmed: bool,
    endotoxin_limit: float | None,
) -> ExperimentConclusion:
    build = _Builder(SEMI_QUANTITATIVE_RULE, assignments, results, labeled)
    roles = (
        TubeRole.SAMPLE,
        TubeRole.SAMPLE_POSITIVE_CONTROL,
        TubeRole.TEST,
        TubeRole.NEGATIVE_CONTROL,
    )
    unmet = _common_prerequisites(build, layout_confirmed, roles, needs_lambda=True)
    if unmet:
        return build.done(ConclusionVerdict.PENDING, unmet)
    assert labeled is not None
    samples = _group(assignments, TubeRole.SAMPLE)
    no_dilution = [t.tube_no for t in samples if not t.dilution_factor]
    if no_dilution:
        return build.done(
            ConclusionVerdict.PENDING,
            [_note(NoteCode.DILUTION_MISSING, tubes=_tube_list(no_dilution))],
        )
    by_dilution: dict[float, list[TubeAssignment]] = {}
    for tube in samples:
        assert tube.dilution_factor is not None
        by_dilution.setdefault(tube.dilution_factor, []).append(tube)
    dilutions = sorted(by_dilution)
    counts = [len(by_dilution[d]) for d in dilutions]
    water_levels, notes_c = _spike_levels(
        _group(assignments, TubeRole.TEST), TubeRole.TEST, labeled
    )
    layout_notes = list(notes_c)
    if len(set(counts)) != 1:
        layout_notes.append(
            _note(
                NoteCode.LEVEL_COUNT_UNEVEN,
                role=TubeRole.SAMPLE.value,
                counts="、".join(
                    f"{d:g} 倍 {c} 管" for d, c in zip(dilutions, counts, strict=True)
                ),
            )
        )
    if water_levels is None or layout_notes:
        return build.done(ConclusionVerdict.PENDING, layout_notes)
    build.basis.append(_note(NoteCode.LAMBDA_VALUE, lam=format_quantity(labeled)))

    invalid: list[ConclusionNote] = []
    for role, expected in (
        (TubeRole.NEGATIVE_CONTROL, Interpretation.NEGATIVE),
        (TubeRole.SAMPLE_POSITIVE_CONTROL, Interpretation.POSITIVE),
    ):
        problem, basis = _check_group(_group(assignments, role), role, results, expected)
        build.basis.append(basis) if problem is None else invalid.append(problem)
    water = _spike_series(water_levels, results, TubeRole.TEST)
    build.basis.extend(water.notes)
    build.add_evidence(water.evidence)
    invalid.extend(_standard_validity(water_levels, results))
    if invalid:
        return build.done(ConclusionVerdict.INVALID, invalid)
    if water.discontinuous:
        return build.done(
            ConclusionVerdict.PENDING,
            [
                _note(NoteCode.ROLE_SERIES_DISCONTINUOUS, role=_role_name(TubeRole.TEST), series=s)
                for s in water.discontinuous
            ],
        )
    es = _geometric_mean([e for e in water.endpoints if e is not None])
    low, high = labeled * LAMBDA_C_MIN_RATIO, labeled * LAMBDA_C_MAX_RATIO
    es_note = dict(value=format_quantity(es), low=format_quantity(low), high=format_quantity(high))
    build.basis.append(_note(NoteCode.ES_VALUE, **es_note))
    if not _within(es, low, high):
        return build.done(ConclusionVerdict.INVALID, [_note(NoteCode.ES_OUT_OF_RANGE, **es_note)])

    # 供试品系列：每个稀释度内按管号排第 k 支的组成第 k 个系列
    exact: list[float] = []
    below: dict[int, float] = {}
    above: dict[int, float] = {}
    discontinuous: list[int] = []
    for index in range(counts[0]):
        series = index + 1
        tubes = [by_dilution[d][index] for d in dilutions]
        readings = _readings(tubes, results)
        build.add_evidence([_evidence(t, results[t.tube_no], series) for t in tubes])
        tube_text = _tube_list([t.tube_no for t in tubes])
        marks = "  ".join(
            f"{d:g}×({_READING_MARKS.get(r, '?') if r else '?'})"
            for d, r in zip(dilutions, readings, strict=True)
        )
        positive = [i for i, r in enumerate(readings) if r is Interpretation.POSITIVE]
        if not positive:
            bound = dilutions[0] * labeled
            below[series] = bound
            build.basis.append(
                _note(
                    NoteCode.SAMPLE_SERIES_BELOW,
                    series=series,
                    tubes=tube_text,
                    value=format_quantity(bound),
                )
            )
            continue
        last = positive[-1]
        if len(positive) != last + 1:
            discontinuous.append(series)
        if last == len(dilutions) - 1 and len(positive) == len(dilutions):
            bound = dilutions[-1] * labeled
            above[series] = bound
            build.basis.append(
                _note(
                    NoteCode.SAMPLE_SERIES_ABOVE,
                    series=series,
                    tubes=tube_text,
                    value=format_quantity(bound),
                )
            )
            continue
        value = dilutions[last] * labeled
        exact.append(value)
        build.basis.append(
            _note(
                NoteCode.SAMPLE_SERIES_RESULT,
                series=series,
                tubes=tube_text,
                readings=marks,
                dilution=f"{dilutions[last]:g}",
                value=format_quantity(value),
            )
        )
    if discontinuous:
        return build.done(
            ConclusionVerdict.PENDING,
            [
                _note(NoteCode.ROLE_SERIES_DISCONTINUOUS, role=TubeRole.SAMPLE.value, series=s)
                for s in discontinuous
            ],
        )
    concentration = _geometric_mean(exact) if exact and not below and not above else None
    if concentration is not None:
        build.basis.append(
            _note(NoteCode.SAMPLE_CONCENTRATION, value=format_quantity(concentration))
        )
    if endotoxin_limit is None or endotoxin_limit <= 0:
        return build.done(
            ConclusionVerdict.PENDING,
            [_note(NoteCode.ENDOTOXIN_LIMIT_MISSING)],
            value=concentration,
        )
    limit_text = format_quantity(endotoxin_limit)
    failed: list[int] = []
    undetermined: list[int] = []
    exact_by_series = {
        s: v
        for s, v in zip(
            [i + 1 for i in range(counts[0]) if i + 1 not in below and i + 1 not in above],
            exact,
            strict=True,
        )
    }
    for series in range(1, counts[0] + 1):
        if series in below:
            if below[series] > endotoxin_limit * (1 + _REL_TOLERANCE):
                undetermined.append(series)
        elif series in above:
            if above[series] >= endotoxin_limit * (1 - _REL_TOLERANCE):
                failed.append(series)
            else:
                undetermined.append(series)
        elif exact_by_series[series] >= endotoxin_limit * (1 - _REL_TOLERANCE):
            failed.append(series)
    if failed:
        return build.done(
            ConclusionVerdict.FAIL,
            [
                _note(NoteCode.CONCENTRATION_NOT_BELOW_LIMIT, series=s, limit=limit_text)
                for s in failed
            ],
            value=concentration,
        )
    if undetermined:
        return build.done(
            ConclusionVerdict.PENDING,
            [
                _note(NoteCode.CONCENTRATION_UNDETERMINED, series=s, limit=limit_text)
                for s in undetermined
            ],
            value=concentration,
        )
    build.basis.append(_note(NoteCode.CONCENTRATION_BELOW_LIMIT, limit=limit_text))
    return build.done(ConclusionVerdict.PASS, value=concentration)


def _evaluate_general(
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled: float | None,
) -> ExperimentConclusion:
    """通用检测：没有试验级判定，全部单管结果齐全且复核完后结论为“不适用”。"""
    build = _Builder(GENERAL_RULE, assignments, results, labeled)
    if not assignments:
        return build.done(ConclusionVerdict.PENDING, [_note(NoteCode.LAYOUT_EMPTY)])
    problems = _result_problems(assignments, results)
    if problems:
        return build.done(ConclusionVerdict.PENDING, problems)
    finals = [results[a.tube_no].final_interpretation for a in assignments]
    build.basis.append(
        _note(
            NoteCode.GENERAL_SUMMARY,
            total=len(assignments),
            positive=sum(f is Interpretation.POSITIVE for f in finals),
            negative=sum(f is Interpretation.NEGATIVE for f in finals),
        )
    )
    return build.done(ConclusionVerdict.NOT_APPLICABLE)


def evaluate_conclusion(
    experiment_type: ExperimentType,
    assignments: Sequence[TubeAssignment],
    results: Mapping[int, TubeResult],
    labeled_sensitivity: float | None,
    *,
    layout_confirmed: bool,
    endotoxin_limit: float | None = None,
) -> ExperimentConclusion:
    """由单管结果计算结论。

    Args:
        experiment_type: 试验类型。
        assignments: 试管安排。
        results: 单管结果，按管号索引。
        labeled_sensitivity: 标示灵敏度 λ。
        layout_confirmed: 模板布局是否完整（旧数据可能为 False）。
        endotoxin_limit: 内毒素限值（EU/mL），半定量试验用。

    Returns:
        结论，``computed_at`` 为 None。有管缺结果或待复核时一定是待定。
    """
    if experiment_type is ExperimentType.SENSITIVITY_CONFIRMATION:
        return _evaluate_sensitivity(assignments, results, labeled_sensitivity, layout_confirmed)
    if experiment_type is ExperimentType.INTERFERENCE:
        return _evaluate_interference(assignments, results, labeled_sensitivity, layout_confirmed)
    if experiment_type is ExperimentType.LIMIT:
        return _evaluate_limit(assignments, results, labeled_sensitivity, layout_confirmed)
    if experiment_type is ExperimentType.GENERAL:
        return _evaluate_general(assignments, results, labeled_sensitivity)
    return _evaluate_semi_quantitative(
        assignments, results, labeled_sensitivity, layout_confirmed, endotoxin_limit
    )


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
        endotoxin_limit=experiment.endotoxin_limit,
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
