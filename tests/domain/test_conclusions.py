"""试验结论规则：手算对照表、有效性、边界、不连续、待复核、四类未确认模板。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from gelinspect.domain.conclusions import (
    NOTE_TEMPLATES,
    RULES,
    ExperimentConclusion,
    NoteCode,
    conclude_experiment,
    conclusion_from_payload,
    conclusion_payload,
    evaluate_conclusion,
    finalize_conclusion,
    judge_lambda_c,
    labeled_sensitivity_of,
    lambda_c_from_endpoints,
    provisional_conclusion,
)
from gelinspect.domain.entities import (
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
)
from gelinspect.domain.enums import (
    ConclusionVerdict,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentKind,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentState
from gelinspect.domain.templates import BUILTIN_TEMPLATES, spike_from_lambda

MULTIPLES = (2.0, 1.0, 0.5, 0.25)
NOW = datetime(2026, 11, 5, 9, tzinfo=UTC)
P, N, R = Interpretation.POSITIVE, Interpretation.NEGATIVE, Interpretation.PENDING_REVIEW


def assignments(labeled: float) -> list[TubeAssignment]:
    """内置模板的 17 管：每档 4 管，管号竖向，17 号阴性对照。"""
    template = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
    return [
        TubeAssignment(
            tube_no=item.tube_no,
            sample=Sample(f"S{item.tube_no}", item.sample_name),
            role=item.role,
            spiked_endotoxin=item.spike_for(labeled),
        )
        for item in template.items
    ]


def result(tube_no: int, value: Interpretation) -> TubeResult:
    state = {
        P: GelState.FULLY_GELLED,
        N: GelState.NOT_GELLED,
        R: GelState.PARTIALLY_GELLED,
    }[value]
    return TubeResult.from_analysis(
        tube_no=tube_no, status=TubeAnalysisStatus.OK, state=state, mapping=GelStateMapping()
    )


def results_for(series: Sequence[str], control: Interpretation = N) -> dict[int, TubeResult]:
    """每个系列一个四字符串，依次是 2λ、λ、λ/2、λ/4，``+`` 阳性、``-`` 阴性、``?`` 待复核。"""
    marks = {"+": P, "-": N, "?": R}
    table: dict[int, TubeResult] = {}
    for index, pattern in enumerate(series):
        for level, mark in enumerate(pattern):
            tube_no = level * 4 + index + 1
            table[tube_no] = result(tube_no, marks[mark])
    table[17] = result(17, control)
    return table


def sensitivity(
    series: Sequence[str], labeled: float = 0.5, control: Interpretation = N
) -> ExperimentConclusion:
    return evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(labeled),
        results_for(series, control),
        labeled,
        layout_confirmed=True,
    )


# ---------------------------------------------------------------- 手算对照表


@pytest.mark.parametrize(
    ("series", "labeled", "expected"),
    [
        # 示例：λ=0.5，终点 0.5、0.5、0.5、1：antilg((3·lg0.5 + lg1)/4) = 2^(-3/4) ≈ 0.5946
        (("++--", "++--", "++--", "+---"), 0.5, 0.5946035575),
        # 全部终点 = λ
        (("++--", "++--", "++--", "++--"), 0.5, 0.5),
        # 两个 λ、两个 λ/2：antilg((2·lg0.25 + 2·lg0.5)/4) = sqrt(0.125) ≈ 0.3536
        (("++--", "++--", "+++-", "+++-"), 0.5, 0.3535533906),
        # λ=0.25，终点 0.25、0.25、0.25、0.5：0.25·2^(1/4) ≈ 0.2973
        (("++--", "++--", "++--", "+---"), 0.25, 0.2973017788),
        # λ=0.03，全部终点 λ/2 和 λ 各半：0.03·2^(-1/2) ≈ 0.02121
        (("++--", "++--", "+++-", "+++-"), 0.03, 0.0212132034356),
        # λ=0.125，四个不同组合：终点 2λ、λ、λ、λ/2：几何平均 = λ
        (("+---", "++--", "++--", "+++-"), 0.125, 0.125),
    ],
)
def test_hand_calculated_lambda_c(series: Sequence[str], labeled: float, expected: float) -> None:
    conclusion = sensitivity(series, labeled)
    assert conclusion.verdict is ConclusionVerdict.PASS
    assert conclusion.value == pytest.approx(expected, rel=1e-9)
    assert conclusion.labeled_sensitivity == labeled
    assert conclusion.rule_version == "sensitivity-v1"
    assert conclusion.unmet == ()


def test_the_worked_example_rounds_to_059() -> None:
    conclusion = sensitivity(("++--", "++--", "++--", "+---"))
    assert conclusion.value is not None and round(conclusion.value, 2) == 0.59


def test_basis_lists_the_calculation_and_the_tubes() -> None:
    conclusion = sensitivity(("++--", "++--", "++--", "+---"))
    text = "\n".join(note.text for note in conclusion.basis)
    assert "标示灵敏度 λ = 0.5" in text
    assert "第 1 系列（1、5、9、13 号管）" in text
    assert "2λ(+)  1λ(−)  0.5λ(−)  0.25λ(−)，终点 1 EU/mL（2λ）" in text
    assert "antilg" in text and "0.5946" in text
    assert "0.25–1" in text
    assert {t.tube_no for t in conclusion.tubes} == set(range(1, 18))
    assert next(t for t in conclusion.tubes if t.tube_no == 6).series == 2
    assert next(t for t in conclusion.tubes if t.tube_no == 17).series is None


@pytest.mark.parametrize("labeled", [0.03, 0.06, 0.125, 0.25, 0.5, 1.0])
@pytest.mark.parametrize(
    "series",
    [
        ("++--", "++--", "++--", "++--"),
        ("+---", "+---", "+++-", "+++-"),
        ("+---", "++--", "+++-", "++--"),
    ],
)
def test_lambda_c_matches_the_formula_for_any_lambda(series: Sequence[str], labeled: float) -> None:
    """属性：λc 是终点几何平均，且与 λ 成正比。"""
    endpoints = [labeled * MULTIPLES[pattern.rfind("+")] for pattern in series]
    expected = math.exp(sum(math.log(e) for e in endpoints) / len(endpoints))
    conclusion = sensitivity(series, labeled)
    assert conclusion.value == pytest.approx(expected, rel=1e-9)
    assert conclusion.value / labeled == pytest.approx(  # type: ignore[operator]
        sensitivity(series, 1.0).value, rel=1e-9
    )


# ---------------------------------------------------------------- 边界


@pytest.mark.parametrize("labeled", [0.03, 0.06, 0.125, 0.25, 0.5, 0.0625, 0.3, 0.7])
def test_boundaries_are_inclusive(labeled: float) -> None:
    low = sensitivity(("+++-",) * 4, labeled)
    high = sensitivity(("+---",) * 4, labeled)
    assert low.verdict is ConclusionVerdict.PASS
    assert low.value == pytest.approx(labeled / 2)
    assert high.verdict is ConclusionVerdict.PASS
    assert high.value == pytest.approx(labeled * 2)


def test_judge_lambda_c_is_inclusive_and_rejects_outside() -> None:
    assert judge_lambda_c(0.25, 0.5) is ConclusionVerdict.PASS
    assert judge_lambda_c(1.0, 0.5) is ConclusionVerdict.PASS
    assert judge_lambda_c(0.2499, 0.5) is ConclusionVerdict.FAIL
    assert judge_lambda_c(1.001, 0.5) is ConclusionVerdict.FAIL


def test_floating_point_noise_does_not_cross_a_boundary() -> None:
    # 0.1 + 0.2 这种尾巴：λ=0.3 时倍数换算不会把管位判成不在浓度档上。
    assert sensitivity(("+---",) * 4, 0.1 + 0.2).verdict is ConclusionVerdict.PASS
    assert judge_lambda_c(0.5 * (1 - 1e-12), 1.0) is ConclusionVerdict.PASS
    assert judge_lambda_c(2.0 * (1 + 1e-12), 1.0) is ConclusionVerdict.PASS


def test_lambda_c_has_no_binary_tail() -> None:
    assert lambda_c_from_endpoints([0.25, 0.25, 0.25, 0.25]) == 0.25
    assert lambda_c_from_endpoints([0.06, 0.06, 0.06, 0.06]) == 0.06
    assert str(lambda_c_from_endpoints([0.3, 0.3, 0.3, 0.3])) == "0.3"


def test_lambda_c_rejects_bad_endpoints() -> None:
    with pytest.raises(ValueError):
        lambda_c_from_endpoints([])
    with pytest.raises(ValueError):
        lambda_c_from_endpoints([0.5, 0.0])


# ---------------------------------------------------------------- 有效性


@pytest.mark.parametrize(
    ("series", "control", "code"),
    [
        (("++++",) * 4, N, NoteCode.LOWEST_NOT_ALL_NEGATIVE),
        (("----",) * 4, N, NoteCode.HIGHEST_NOT_ALL_POSITIVE),
        (("++--", "++--", "++--", "-+--"), N, NoteCode.HIGHEST_NOT_ALL_POSITIVE),
        (("++--", "++--", "++--", "+++-" + ""), P, NoteCode.NEGATIVE_CONTROL_NOT_NEGATIVE),
        (("++-+", "++--", "++--", "++--"), N, NoteCode.LOWEST_NOT_ALL_NEGATIVE),
    ],
)
def test_invalid_never_passes_and_has_no_value(
    series: Sequence[str], control: Interpretation, code: NoteCode
) -> None:
    conclusion = sensitivity(series, control=control)
    assert conclusion.verdict is ConclusionVerdict.INVALID
    assert conclusion.value is None
    assert code in {note.code for note in conclusion.unmet}


def test_invalid_reasons_name_the_tubes() -> None:
    conclusion = sensitivity(("++--", "++--", "++--", "-+--"), control=P)
    text = "\n".join(note.text for note in conclusion.unmet)
    assert "4 号管" in text and "17 号管" in text


# ---------------------------------------------------------------- 不连续


def test_discontinuous_series_is_pending_with_a_reference_value() -> None:
    """+ - + -：终点按最后一个阳性（λ/2）算，但不给合格，由 QA 判定。"""
    conclusion = sensitivity(("++--", "++--", "++--", "+-+-"))
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.value == pytest.approx(0.5 * 2 ** (-1 / 4))
    assert [note.code for note in conclusion.unmet] == [NoteCode.SERIES_DISCONTINUOUS]
    assert conclusion.unmet[0].params["series"] == "4"


def test_discontinuous_does_not_write_measured_sensitivity() -> None:
    experiment = sensitivity_experiment(("++--", "++--", "++--", "+-+-"))
    conclusion = finalize_conclusion(experiment, NOW)
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert experiment.measured_sensitivity is None


# ---------------------------------------------------------------- 待复核和缺结果


def test_pending_review_produces_no_final_conclusion() -> None:
    conclusion = sensitivity(("++--", "++--", "+?--", "+---"))
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.value is None
    assert conclusion.unmet[0].code is NoteCode.REVIEW_PENDING
    assert conclusion.unmet[0].params["tubes"] == "7"


def test_pending_control_produces_no_final_conclusion() -> None:
    conclusion = sensitivity(("++--",) * 4, control=R)
    assert conclusion.verdict is ConclusionVerdict.PENDING


def test_review_decides_the_final_interpretation() -> None:
    table = results_for(("++--", "++--", "+?--", "+---"))
    table[7] = table[7].with_review(interpretation=P, reviewed_by="a", reviewed_at=NOW, note="n")
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(0.5),
        table,
        0.5,
        layout_confirmed=True,
    )
    assert conclusion.verdict is ConclusionVerdict.PASS
    assert conclusion.value == pytest.approx(0.5946035575)
    evidence = next(t for t in conclusion.tubes if t.tube_no == 7)
    assert evidence.reviewed and evidence.interpretation is P


def test_missing_result_produces_no_final_conclusion() -> None:
    table = results_for(("++--",) * 4)
    del table[9]
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(0.5),
        table,
        0.5,
        layout_confirmed=True,
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.RESULT_MISSING


# ---------------------------------------------------------------- 布局前提


def test_missing_lambda_is_pending() -> None:
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(0.5),
        results_for(("++--",) * 4),
        None,
        layout_confirmed=True,
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.LAMBDA_MISSING


def test_spikes_that_do_not_match_lambda_are_pending() -> None:
    """旧模板写死的 1、0.5、0.25、0.125 配 λ=0.25：不能算出看似合理的 λc。"""
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(0.5),
        results_for(("++--",) * 4),
        0.25,
        layout_confirmed=True,
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.value is None
    assert NoteCode.SPIKE_NOT_ON_LEVEL in {note.code for note in conclusion.unmet}


def test_missing_negative_control_is_pending() -> None:
    tubes = assignments(0.5)[:-1]
    table = results_for(("++--",) * 4)
    del table[17]
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION, tubes, table, 0.5, layout_confirmed=True
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.NEGATIVE_CONTROL_MISSING


def test_wrong_tube_count_on_a_level_is_pending() -> None:
    tubes = [a for a in assignments(0.5) if a.tube_no != 16]
    table = results_for(("++--",) * 4)
    del table[16]
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION, tubes, table, 0.5, layout_confirmed=True
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.LEVEL_COUNT_MISMATCH


def test_series_follow_tube_order_when_positions_are_moved() -> None:
    """向导里换了管位：系列按同一浓度内的管号顺序，不靠固定管号。"""
    moved = [
        TubeAssignment(a.tube_no + 20, a.sample, a.role, a.dilution_factor, a.spiked_endotoxin)
        for a in assignments(0.5)
    ]
    table = {
        no + 20: TubeResult.from_analysis(
            tube_no=no + 20,
            status=TubeAnalysisStatus.OK,
            state=r.gel_state,
            mapping=GelStateMapping(),
        )
        for no, r in results_for(("++--", "++--", "++--", "+---")).items()
    }
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION, moved, table, 0.5, layout_confirmed=True
    )
    assert conclusion.verdict is ConclusionVerdict.PASS
    assert conclusion.value == pytest.approx(0.5946035575)


def test_unconfirmed_sensitivity_layout_is_pending() -> None:
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        assignments(0.5),
        results_for(("++--",) * 4),
        0.5,
        layout_confirmed=False,
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.LAYOUT_PRESUMED


def test_sensitivity_with_unmarked_roles_is_pending() -> None:
    tubes = [
        TubeAssignment(a.tube_no, a.sample, TubeRole.UNCONFIRMED, None, a.spiked_endotoxin)
        for a in assignments(0.5)
    ]
    conclusion = evaluate_conclusion(
        ExperimentType.SENSITIVITY_CONFIRMATION,
        tubes,
        results_for(("++--",) * 4),
        0.5,
        layout_confirmed=True,
    )
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.LAYOUT_ROLES_UNMARKED


# ---------------------------------------------------------------- 四类未确认模板

UNCONFIRMED_TYPES = [
    ExperimentType.INTERFERENCE,
    ExperimentType.RETEST,
    ExperimentType.SEMI_QUANTITATIVE,
    ExperimentType.LIMIT,
]


def unconfirmed_tubes(kind: ExperimentType) -> list[TubeAssignment]:
    return [
        TubeAssignment(item.tube_no, Sample(f"S{item.tube_no}", item.sample_name), item.role)
        for item in BUILTIN_TEMPLATES[kind].items
    ]


@pytest.mark.parametrize("kind", UNCONFIRMED_TYPES)
@pytest.mark.parametrize("reading", [P, N, R])
@pytest.mark.parametrize("confirmed", [True, False])
def test_builtin_unconfirmed_templates_only_give_pending(
    kind: ExperimentType, reading: Interpretation, confirmed: bool
) -> None:
    tubes = unconfirmed_tubes(kind)
    table = {t.tube_no: result(t.tube_no, reading) for t in tubes}
    conclusion = evaluate_conclusion(kind, tubes, table, 0.5, layout_confirmed=confirmed)
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.value is None
    assert conclusion.tubes == ()  # 没有标记用途的管一支都不读
    codes = {note.code for note in conclusion.unmet}
    assert codes & {NoteCode.LAYOUT_PRESUMED, NoteCode.LAYOUT_ROLES_UNMARKED}
    assert NoteCode.RULE_PENDING_QA in codes


@pytest.mark.parametrize("kind", UNCONFIRMED_TYPES)
@pytest.mark.parametrize(
    "pattern",
    [
        {TubeRole.SAMPLE: N, TubeRole.NEGATIVE_CONTROL: N, TubeRole.POSITIVE_CONTROL: P},
        {TubeRole.SAMPLE: P, TubeRole.NEGATIVE_CONTROL: N, TubeRole.POSITIVE_CONTROL: P},
        {TubeRole.SAMPLE: N, TubeRole.NEGATIVE_CONTROL: P, TubeRole.POSITIVE_CONTROL: N},
    ],
)
def test_even_fully_marked_layouts_stay_pending_until_qa(
    kind: ExperimentType, pattern: Mapping[TubeRole, Interpretation]
) -> None:
    """规则条文是凭记忆的，没有产生合格或不合格的代码路径。"""
    tubes = [
        TubeAssignment(index + 1, Sample(f"S{index}", "样品"), role)
        for index, role in enumerate(pattern)
    ]
    table = {t.tube_no: result(t.tube_no, pattern[t.role]) for t in tubes}
    conclusion = evaluate_conclusion(kind, tubes, table, 0.5, layout_confirmed=True)
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert NoteCode.RULE_PENDING_QA in {note.code for note in conclusion.unmet}
    assert len(conclusion.tubes) == 3
    summary = "\n".join(note.text for note in conclusion.basis)
    assert "供试品" in summary and "阴性对照" in summary


def test_marked_tubes_are_read_and_unmarked_ones_are_not() -> None:
    tubes = [
        TubeAssignment(1, Sample("S1", "样品"), TubeRole.SAMPLE),
        TubeAssignment(2, Sample("S2", "样品"), TubeRole.UNCONFIRMED),
    ]
    table = {1: result(1, N), 2: result(2, P)}
    conclusion = evaluate_conclusion(ExperimentType.LIMIT, tubes, table, 0.5, layout_confirmed=True)
    assert [t.tube_no for t in conclusion.tubes] == [1]
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.unmet[0].code is NoteCode.LAYOUT_ROLES_UNMARKED


def test_rules_mark_every_clause_from_memory_as_pending_qa() -> None:
    assert RULES[ExperimentType.SENSITIVITY_CONFIRMATION].verdict_enabled
    for kind in UNCONFIRMED_TYPES:
        rule = RULES[kind]
        assert not rule.verdict_enabled
        assert "通则 1143" in rule.source and "待 QA 核对" in rule.source
        assert rule.clauses
        assert all("待 QA 核对" in clause for clause in rule.clauses)


# ---------------------------------------------------------------- 固化和存取


def sensitivity_experiment(
    series: Sequence[str], labeled: float = 0.5, state: ExperimentState = ExperimentState.COMPLETED
) -> Experiment:
    return Experiment(
        experiment_id="EXP-1",
        experiment_type=ExperimentType.SENSITIVITY_CONFIRMATION,
        template_id="t",
        instrument_no="GI",
        operator="op",
        created_at=NOW,
        assignments=tuple(assignments(labeled)),
        reagents=(
            ReagentBatch(ReagentKind.LAL_REAGENT, "L", None, True, labeled),
            ReagentBatch(ReagentKind.WATER, "W", None, True),
        ),
        state=state,
        results=results_for(series),
        layout_confirmed=True,
    )


def test_finalize_records_completion_and_measured_sensitivity() -> None:
    experiment = sensitivity_experiment(("++--", "++--", "++--", "+---"))
    conclusion = finalize_conclusion(experiment, NOW)
    assert experiment.conclusion is conclusion
    assert experiment.completed_at == NOW and conclusion.computed_at == NOW
    assert experiment.measured_sensitivity == pytest.approx(0.5946035575)


def test_finalize_invalid_writes_no_measured_sensitivity() -> None:
    experiment = sensitivity_experiment(("++++",) * 4)
    finalize_conclusion(experiment, NOW)
    assert experiment.measured_sensitivity is None
    assert experiment.conclusion is not None
    assert experiment.conclusion.verdict is ConclusionVerdict.INVALID


def test_finalize_requires_completed_and_aware_time() -> None:
    experiment = sensitivity_experiment(("++--",) * 4, state=ExperimentState.PENDING_REVIEW)
    with pytest.raises(ValueError):
        finalize_conclusion(experiment, NOW)
    experiment.state = ExperimentState.COMPLETED
    with pytest.raises(ValueError):
        finalize_conclusion(experiment, datetime(2026, 11, 5))


def test_provisional_conclusion_never_has_a_value() -> None:
    running = sensitivity_experiment(("++--",) * 4, state=ExperimentState.ANALYZING)
    assert provisional_conclusion(running).verdict is ConclusionVerdict.PENDING
    assert provisional_conclusion(running).value is None


def test_provisional_for_old_completed_experiment_does_not_recalculate() -> None:
    old = sensitivity_experiment(("++--",) * 4)
    conclusion = provisional_conclusion(old)
    assert conclusion.verdict is ConclusionVerdict.PENDING
    assert conclusion.value is None
    assert [note.code for note in conclusion.unmet] == [NoteCode.NOT_RECORDED]


def test_provisional_for_stopped_experiment_says_so() -> None:
    stopped = sensitivity_experiment(("++--",) * 4, state=ExperimentState.STOPPED)
    assert provisional_conclusion(stopped).unmet[0].code is NoteCode.STOPPED


def test_payload_round_trip_keeps_everything() -> None:
    experiment = sensitivity_experiment(("++--", "++--", "++--", "+---"))
    conclusion = finalize_conclusion(experiment, NOW)
    restored = conclusion_from_payload(
        experiment_type=conclusion.experiment_type,
        verdict=conclusion.verdict,
        value=conclusion.value,
        rule_version=conclusion.rule_version,
        labeled_sensitivity=conclusion.labeled_sensitivity,
        computed_at=conclusion.computed_at,
        payload=conclusion_payload(conclusion),
    )
    assert restored == conclusion
    assert "0.5946" in conclusion_payload(conclusion)["basis"][-1]["text"]


def test_summary_text_carries_rule_lambda_and_tubes() -> None:
    conclusion = conclude_experiment(sensitivity_experiment(("++--",) * 4))
    text = conclusion.summary_text()
    assert "sensitivity-v1" in text and "λ 0.5" in text and "1,2,3" in text


def test_labeled_sensitivity_requires_one_positive_value() -> None:
    experiment = sensitivity_experiment(("++--",) * 4)
    assert labeled_sensitivity_of(experiment) == 0.5
    experiment.reagents = (
        ReagentBatch(ReagentKind.LAL_REAGENT, "A", None, True, 0.5),
        ReagentBatch(ReagentKind.LAL_REAGENT, "B", None, True, 0.25),
    )
    assert labeled_sensitivity_of(experiment) is None
    experiment.reagents = (ReagentBatch(ReagentKind.LAL_REAGENT, "A", None, True, 0.0),)
    assert labeled_sensitivity_of(experiment) is None
    experiment.reagents = ()
    assert labeled_sensitivity_of(experiment) is None


def test_every_note_code_has_a_template() -> None:
    assert set(NOTE_TEMPLATES) == set(NoteCode)


@pytest.mark.parametrize(
    ("labeled", "multiple", "expected"),
    [
        (0.5, 2.0, 1.0),
        (0.5, 0.25, 0.125),
        (0.25, 0.25, 0.0625),
        (0.03, 2.0, 0.06),
        (0.03, 0.5, 0.015),
        (0.03, 0.25, 0.0075),
        (0.1, 3.0, 0.3),
        (0.06, 0.25, 0.015),
    ],
)
def test_spike_from_lambda_is_exact(labeled: float, multiple: float, expected: float) -> None:
    value = spike_from_lambda(labeled, multiple)
    assert value == expected
    assert len(repr(value)) <= 8


def test_spike_from_lambda_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        spike_from_lambda(0.0, 1.0)
    with pytest.raises(ValueError):
        spike_from_lambda(0.5, -1.0)


@pytest.mark.parametrize(
    ("tubes", "expected"),
    [
        ([1, 5, 9, 13], "1、5、9、13"),
        ([1, 2], "1、2"),
        ([1, 2, 3], "1–3"),
        ([12, 10, 1, 2, 3, 4, 13, 14], "1–4、10、12–14"),
        (list(range(1, 97)), "1–96"),
    ],
)
def test_tube_list_merges_runs_of_three_or_more(tubes: list[int], expected: str) -> None:
    from gelinspect.domain.conclusions import _tube_list

    assert _tube_list(tubes) == expected
