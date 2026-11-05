"""向导草稿：数量对得上、管号对应样品行、编辑状态。"""

from __future__ import annotations

import pytest

from gelinspect.domain.enums import ExperimentType, ReagentKind
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.templates import BUILTIN_TEMPLATES, ExperimentTemplate
from gelinspect.services.experiment_draft import ExperimentDraft

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]
LIMIT = BUILTIN_TEMPLATES[ExperimentType.LIMIT]


def draft_with(template: ExperimentTemplate = SENSITIVITY) -> ExperimentDraft:
    draft = ExperimentDraft()
    draft.choose_template(template)
    return draft


def test_rows_come_from_the_template_in_tube_order() -> None:
    draft = draft_with()
    assert draft.expected_count == 17
    assert [r.spiked_endotoxin for r in draft.rows[:5]] == [None] * 5  # λ 还不知道
    draft.set_labeled_sensitivity(0.5)
    assert [r.spiked_endotoxin for r in draft.rows[:5]] == [1.0, 1.0, 1.0, 1.0, 0.5]
    assert draft.rows[16].name == "阴性对照" and draft.rows[16].spiked_endotoxin == 0.0


def test_count_difference_and_match() -> None:
    draft = draft_with()
    assert draft.difference == -17 and not draft.tubes_match
    draft.set_selected(range(1, 15))
    assert draft.difference == -3 and not draft.tubes_match
    draft.set_selected(range(1, 21))
    assert draft.difference == 3 and not draft.tubes_match
    draft.set_selected(range(1, 18))
    assert draft.difference == 0 and draft.tubes_match


def test_nothing_chosen_never_matches() -> None:
    assert not ExperimentDraft().tubes_match
    assert ExperimentDraft().expected_count == 0


def test_auto_select_uses_the_template_tubes_and_clear_empties() -> None:
    draft = draft_with(LIMIT)
    assert draft.auto_select() == list(range(1, 9))
    assert draft.selected == list(range(1, 9)) and draft.tubes_match
    draft.clear_selection()
    assert draft.selected == [] and draft.selected_count == 0


def test_selected_tubes_map_to_rows_in_ascending_order() -> None:
    draft = draft_with(LIMIT)
    draft.set_selected([30, 12, 5, 77, 9, 40, 2, 60])
    assert draft.selected == [2, 5, 9, 12, 30, 40, 60, 77]
    assert [draft.tube_of_row(i) for i in range(8)] == draft.selected
    assert draft.tube_of_row(8) is None and draft.tube_of_row(-1) is None


def test_invalid_tube_numbers_are_rejected_and_selection_unchanged() -> None:
    draft = draft_with(LIMIT)
    draft.set_selected([1, 2])
    for bad in (0, 81, -3):
        with pytest.raises(InvalidPlatePositionError):
            draft.set_selected([1, bad])
    assert draft.selected == [1, 2]


def test_duplicates_collapse() -> None:
    draft = draft_with(LIMIT)
    draft.set_selected([3, 3, 3, 4])
    assert draft.selected == [3, 4]


def test_change_tracking_for_the_leave_confirmation() -> None:
    draft = draft_with()
    assert not draft.is_modified() and not draft.has_sample_input()
    draft.rows[0].batch_no = "B1"
    assert draft.is_modified() and draft.has_sample_input()
    draft.rows[0].batch_no = ""
    assert not draft.is_modified()
    draft.reagent_codes[ReagentKind.WATER] = "x"
    assert draft.is_modified() and not draft.has_sample_input()
    draft.reagent_codes.clear()
    draft.set_selected([1])
    assert draft.is_modified() and draft.has_sample_input()


def test_choosing_another_template_resets_rows_and_selection() -> None:
    draft = draft_with()
    draft.set_selected(range(1, 18))
    draft.choose_template(LIMIT)
    assert draft.selected == [] and draft.expected_count == 8


def test_blank_sample_names_are_reported_by_row() -> None:
    draft = draft_with(LIMIT)
    draft.rows[2].name = "  "
    draft.rows[5].name = ""
    assert draft.sample_problems() == [3, 6]


def test_request_pairs_tubes_with_trimmed_sample_rows() -> None:
    draft = draft_with(LIMIT)
    draft.rows[0].name = "  样品A "
    draft.rows[0].batch_no = " B-1 "
    draft.rows[0].spiked_endotoxin = 0.5
    draft.set_selected([10, 11, 12, 13, 14, 15, 16, 17])
    request = draft.to_request()
    assert request.template_id == LIMIT.template_id
    first = request.assignments[0]
    assert (first.tube_no, first.name, first.batch_no, first.spiked_endotoxin) == (
        10, "样品A", "B-1", 0.5,
    )  # fmt: skip
    assert [a.tube_no for a in request.assignments] == [10, 11, 12, 13, 14, 15, 16, 17]


def test_request_without_a_template_is_an_error() -> None:
    with pytest.raises(ValueError, match="模板"):
        ExperimentDraft().to_request()
