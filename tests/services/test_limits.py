"""MVD 和内毒素限值：向导可选录入，固化到试验记录；已签署的不可改。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from gelinspect.domain.enums import AuditAction, ExperimentType
from gelinspect.domain.templates import BUILTIN_TEMPLATES
from gelinspect.interfaces.storage import ExperimentQuery, PageRequest, StateConflictError
from gelinspect.services.auth import PermissionDeniedError
from gelinspect.services.errors import ServiceError, ServiceErrorCode
from gelinspect.services.experiment_draft import (
    ExperimentDraft,
    NewExperimentRequest,
    parse_limit,
)
from tests.services.reporting.conftest import make_completed, make_service

from .conftest import GOOD_CODES, PASSWORDS, Stack

SENSITIVITY = BUILTIN_TEMPLATES[ExperimentType.SENSITIVITY_CONFIRMATION]


def request(draft_changes: dict[str, str] | None = None) -> NewExperimentRequest:
    draft = ExperimentDraft()
    draft.choose_template(SENSITIVITY)
    draft.auto_select()
    for key, value in (draft_changes or {}).items():
        setattr(draft, key, value)
    base = draft.to_request()
    return NewExperimentRequest(
        base.template_id,
        base.assignments,
        GOOD_CODES,
        mvd=base.mvd,
        endotoxin_limit=base.endotoxin_limit,
    )


@pytest.mark.parametrize(
    ("text", "value"),
    [("1280", 1280.0), (" 0.25 ", 0.25), ("1e3", 1000.0), ("", None), ("abc", None),
     ("0", None), ("-5", None), ("nan", None), ("inf", None)],
)  # fmt: skip
def test_parse_limit_accepts_only_positive_finite_numbers(text: str, value: float | None) -> None:
    assert parse_limit(text) == value


def test_draft_reports_bad_limit_text_and_counts_it_as_an_edit() -> None:
    draft = ExperimentDraft()
    assert draft.limit_problems() == [] and not draft.is_modified()
    draft.mvd_text, draft.limit_text = "abc", "0.25"
    assert draft.limit_problems() == ["mvd"] and draft.is_modified()
    assert draft.endotoxin_limit == 0.25 and draft.mvd is None
    draft.mvd_text, draft.limit_text = "-1", "x"
    assert draft.limit_problems() == ["mvd", "endotoxin_limit"]
    draft.mvd_text = draft.limit_text = "  "
    assert draft.limit_problems() == []  # 全空白等于没填


def test_limits_are_optional_and_stored_with_the_experiment(stack: Stack) -> None:
    experimenter = stack.login("experimenter")
    plain = stack.experiments.create_experiment(experimenter, request())
    stored = stack.database.experiments.get(plain)
    assert stored is not None and stored.mvd is None and stored.endotoxin_limit is None

    given = stack.experiments.create_experiment(
        experimenter, request({"mvd_text": "1280", "limit_text": "0.25"})
    )
    stored = stack.database.experiments.get(given)
    assert stored is not None and stored.mvd == 1280.0 and stored.endotoxin_limit == 0.25
    created = [d for d in stack.audit(AuditAction.EXPERIMENT_CREATED) if "MVD 1280" in d]
    assert created and "内毒素限值 0.25" in created[0]


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_the_service_rejects_bad_limits_even_if_the_ui_is_bypassed(
    stack: Stack, bad: float
) -> None:
    base = request()
    for field in ("mvd", "endotoxin_limit"):
        crafted = NewExperimentRequest(
            base.template_id,
            base.assignments,
            GOOD_CODES,
            **{field: bad},
        )
        with pytest.raises(ServiceError) as caught:
            stack.experiments.create_experiment(stack.login("experimenter"), crafted)
        assert caught.value.code is ServiceErrorCode.LIMITS_INVALID
        assert caught.value.params["field"] == field
    assert stack.database.experiments.query(ExperimentQuery(), PageRequest()).total == 0


def test_limits_can_be_edited_until_the_experiment_is_signed(stack: Stack) -> None:
    make_completed(stack, experiment_id="EXP-L-1")
    experimenter = stack.login("experimenter")
    stack.experiments.update_limits(experimenter, "EXP-L-1", mvd=640.0, endotoxin_limit=0.5)
    stored = stack.database.experiments.get("EXP-L-1")
    assert stored is not None and (stored.mvd, stored.endotoxin_limit) == (640.0, 0.5)
    stack.experiments.update_limits(experimenter, "EXP-L-1", mvd=None, endotoxin_limit=0.5)
    stored = stack.database.experiments.get("EXP-L-1")
    assert stored is not None and stored.mvd is None
    changes = stack.audit(AuditAction.LIMITS_CHANGED)
    assert changes == [
        "修改 MVD 和内毒素限值：无 → MVD 640，内毒素限值 0.5",
        "修改 MVD 和内毒素限值：MVD 640，内毒素限值 0.5 → 内毒素限值 0.5",
    ]
    with pytest.raises(PermissionDeniedError):
        stack.experiments.update_limits(
            stack.login("analyst"), "EXP-L-1", mvd=1.0, endotoxin_limit=1.0
        )
    with pytest.raises(ServiceError) as bad:
        stack.experiments.update_limits(experimenter, "EXP-L-1", mvd=-3.0, endotoxin_limit=None)
    assert bad.value.code is ServiceErrorCode.LIMITS_INVALID
    with pytest.raises(ServiceError) as missing:
        stack.experiments.update_limits(experimenter, "EXP-NOPE", mvd=1.0, endotoxin_limit=None)
    assert missing.value.code is ServiceErrorCode.EXPERIMENT_NOT_FOUND


def test_a_signed_experiment_cannot_have_its_limits_changed(stack: Stack, tmp_path: Path) -> None:
    make_completed(stack, experiment_id="EXP-L-2")
    stack.experiments.update_limits(
        stack.login("experimenter"), "EXP-L-2", mvd=100.0, endotoxin_limit=2.0
    )
    service, _ = make_service(stack, tmp_path)
    service.sign(stack.login("analyst"), "EXP-L-2", PASSWORDS["analyst"])
    with pytest.raises(ServiceError) as caught:
        stack.experiments.update_limits(
            stack.login("experimenter"), "EXP-L-2", mvd=999.0, endotoxin_limit=9.0
        )
    assert caught.value.code is ServiceErrorCode.EXPERIMENT_SIGNED
    stored = stack.database.experiments.get("EXP-L-2")
    assert stored is not None and (stored.mvd, stored.endotoxin_limit) == (100.0, 2.0)
    # 数据层也有同样的保护，不依赖服务层
    with pytest.raises(StateConflictError):
        stack.database.experiments.update_limits("EXP-L-2", mvd=1.0, endotoxin_limit=1.0)
