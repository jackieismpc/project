"""向导草稿：数量对得上、管号对应样品行、编辑状态。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from gelinspect.domain.enums import ExperimentType, ReagentKind, TubeRole
from gelinspect.domain.errors import InvalidPlatePositionError
from gelinspect.domain.plate import TUBE_COUNT
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
    assert draft.expected_count == 18
    assert [r.spiked_endotoxin for r in draft.rows[:5]] == [None] * 5  # λ 还不知道
    draft.set_labeled_sensitivity(0.5)
    assert [r.spiked_endotoxin for r in draft.rows[:5]] == [1.0, 1.0, 1.0, 1.0, 0.5]
    assert draft.rows[16].name == "阴性对照" and draft.rows[16].spiked_endotoxin == 0.0


def test_customized_snapshot_preserves_template_creator_metadata() -> None:
    created_at = datetime(2026, 11, 18, 1, 2, tzinfo=UTC)
    source = replace(LIMIT, created_by="admin", created_at=created_at)
    draft = draft_with(source)
    draft.assign_tube(draft.rows[0].row_id, 9)
    request = draft.to_request()
    assert request.template_snapshot is not None
    assert request.template_snapshot.created_by == "admin"
    assert request.template_snapshot.created_at == created_at


def test_count_difference_and_match() -> None:
    draft = draft_with()
    assert draft.difference == 0 and draft.tubes_match
    draft.set_selected(range(1, 15))
    assert draft.difference == -4 and not draft.tubes_match
    draft.set_selected(range(1, 22))
    assert draft.difference == 3 and not draft.tubes_match
    draft.set_selected(range(1, 19))
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
    for bad in (0, TUBE_COUNT + 1, -3):
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
    draft.set_selected(range(1, 19))
    draft.choose_template(LIMIT)
    assert draft.selected == list(range(1, 9)) and draft.expected_count == 8


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
    assert request.template_id.startswith("draft.")
    assert request.template_snapshot is not None
    assert [item.tube_no for item in request.template_snapshot.items] == list(range(1, 9))
    first = request.assignments[0]
    assert (first.tube_no, first.name, first.batch_no, first.spiked_endotoxin) == (
        10, "样品A", "B-1", 0.5,
    )  # fmt: skip
    assert [a.tube_no for a in request.assignments] == [10, 11, 12, 13, 14, 15, 16, 17]


def test_sort_keeps_sample_parameters_and_tube_binding_together() -> None:
    draft = draft_with(LIMIT)
    draft.set_selected(range(1, 9))
    draft.rows[0].name = "Z sample"
    draft.rows[0].batch_no = "B9"
    draft.rows[0].lambda_multiple = 0.5
    stable_id = draft.rows[0].row_id

    draft.sort_rows("name")

    row = next(item for item in draft.rows if item.row_id == stable_id)
    assert (row.name, row.batch_no, row.lambda_multiple, row.tube_no) == ("Z sample", "B9", 0.5, 1)
    assert next(a for a in draft.to_request().assignments if a.name == "Z sample").tube_no == 1


def test_reconfiguring_a_run_keeps_its_original_template_source() -> None:
    draft = draft_with(LIMIT)
    adhoc = ExperimentTemplate("draft.current", LIMIT.name, LIMIT.experiment_type, LIMIT.items)
    draft.configure(adhoc, source_template_id=LIMIT.template_id)
    updated = ExperimentTemplate("draft.edited", LIMIT.name, LIMIT.experiment_type, LIMIT.items)
    draft.configure(updated, source_template_id=draft.source_template_id)
    request = draft.to_request()
    assert request.template_id == "draft.edited"
    assert request.source_template_id == LIMIT.template_id


def test_request_without_a_template_is_an_error() -> None:
    with pytest.raises(ValueError, match="模板"):
        ExperimentDraft().to_request()


def test_row_identity_sequence_and_sample_binding_survive_tube_edits_and_sorting() -> None:
    draft = draft_with(LIMIT)
    first = draft.rows[0]
    first.name = "用户样品"
    first.batch_no = "BATCH-A"
    first.role = TubeRole.SAMPLE
    assert draft.row_has_user_edits(first.row_id)

    removed = draft.remove_tube(8)
    assert removed is not None and removed.sequence_no == 8
    added = draft.add_tube(20, name="新增样品", role=TubeRole.TEST)
    assert added.sequence_no == 9
    assert draft.tubes_match

    draft.sort_rows("tube_no")
    draft.sort_rows("sequence")
    kept = next(row for row in draft.rows if row.row_id == first.row_id)
    assert (kept.tube_no, kept.name, kept.batch_no, kept.role) == (
        1,
        "用户样品",
        "BATCH-A",
        TubeRole.SAMPLE,
    )
    assert draft.auto_select() == list(range(1, 9))
    assert next(row for row in draft.rows if row.row_id == added.row_id).sequence_no == 9
    request = draft.to_request()
    assert request.template_id.startswith("draft.")
    assert request.template_snapshot is not None
    assert request.template_snapshot.items[0].role is TubeRole.SAMPLE
    assert request.template_snapshot.items[0].sample_name == "用户样品"


def test_removed_sequence_is_not_reused_and_assign_tube_rejects_conflicts() -> None:
    draft = draft_with(LIMIT)
    last = draft.rows[-1]
    draft.remove_row(last.row_id)
    new = draft.add_row(name="新样品")
    assert new.sequence_no == 9
    with pytest.raises(ValueError, match="管位已分配"):
        draft.assign_tube(new.row_id, draft.rows[0].tube_no)
    draft.assign_tube(new.row_id, 20)
    assert new.tube_no == 20 and 20 in draft.selected
