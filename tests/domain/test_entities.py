from datetime import UTC, date, datetime

import pytest

from gelinspect.domain.entities import (
    AuditEvent,
    Experiment,
    ReagentBatch,
    Sample,
    TubeAssignment,
    TubeResult,
    check_reagents,
)
from gelinspect.domain.enums import (
    AuditAction,
    ExperimentType,
    GelState,
    Interpretation,
    ReagentCheckStatus,
    ReagentKind,
    ReviewStatus,
    TubeAnalysisStatus,
    TubeRole,
)
from gelinspect.domain.errors import IllegalTransitionError, TransitionGuardError
from gelinspect.domain.interpretation import GelStateMapping
from gelinspect.domain.state_machine import ExperimentEvent as E
from gelinspect.domain.state_machine import ExperimentState as S

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 4)
MAPPING = GelStateMapping()


def batch(
    kind: ReagentKind = ReagentKind.LAL_REAGENT,
    expiry: date | None = TODAY,
    registered: bool = True,
) -> ReagentBatch:
    return ReagentBatch(kind=kind, batch_no="b", expiry_date=expiry, registered=registered)


def all_batches() -> list[ReagentBatch]:
    return [batch(kind) for kind in ReagentKind]


def make_experiment(tubes: tuple[int, ...] = (1, 2)) -> Experiment:
    sample = Sample(sample_id="s", name="样品")
    return Experiment(
        experiment_id="e",
        experiment_type=ExperimentType.LIMIT,
        template_id="t",
        instrument_no="i",
        operator="op",
        created_at=NOW,
        assignments=tuple(
            TubeAssignment(tube_no=no, sample=sample, role=TubeRole.TEST) for no in tubes
        ),
    )


def result(tube_no: int, state: GelState | None) -> TubeResult:
    status = TubeAnalysisStatus.OK if state else TubeAnalysisStatus.FAILED
    return TubeResult.from_analysis(tube_no=tube_no, status=status, state=state, mapping=MAPPING)


def analyzing_experiment() -> Experiment:
    experiment = make_experiment()
    for event in (
        E.SUBMIT_FOR_REAGENT_CHECK,
        E.REAGENT_CHECK_PASSED,
        E.START,
        E.ROW_DUE,
        E.ROW_CAPTURED,
    ):
        experiment.apply(event)
    return experiment


def completed_experiment() -> Experiment:
    experiment = analyzing_experiment()
    experiment.record_result(result(1, GelState.FULLY_GELLED))
    experiment.record_result(result(2, GelState.NOT_GELLED))
    experiment.apply(E.ALL_ANALYZED_CLEAN)
    return experiment


def pending_review_experiment() -> Experiment:
    experiment = analyzing_experiment()
    experiment.record_result(result(1, GelState.FULLY_GELLED))
    experiment.record_result(result(2, GelState.PARTIALLY_GELLED))
    experiment.apply(E.ALL_ANALYZED_NEEDS_REVIEW)
    return experiment


@pytest.mark.parametrize(
    ("reagent", "expected"),
    [
        (batch(expiry=TODAY), ReagentCheckStatus.VALID),
        (batch(expiry=date(2026, 11, 3)), ReagentCheckStatus.EXPIRED),
        (batch(expiry=None), ReagentCheckStatus.EXPIRY_UNKNOWN),
        (batch(registered=False), ReagentCheckStatus.NOT_REGISTERED),
        (batch(expiry=date(2020, 1, 1), registered=False), ReagentCheckStatus.NOT_REGISTERED),
    ],
)
def test_reagent_batch_check(reagent: ReagentBatch, expected: ReagentCheckStatus) -> None:
    assert reagent.check(TODAY) is expected


def test_check_reagents_passes_with_all_valid() -> None:
    report = check_reagents(all_batches(), TODAY)
    assert report.passed
    assert report.event is E.REAGENT_CHECK_PASSED


def test_check_reagents_locks_on_missing_kind() -> None:
    report = check_reagents([batch(ReagentKind.LAL_REAGENT)], TODAY)
    assert not report.passed
    assert report.event is E.REAGENT_CHECK_FAILED
    assert report.statuses[ReagentKind.WATER] is ReagentCheckStatus.MISSING


def test_check_reagents_any_bad_batch_of_a_kind_fails_regardless_of_order() -> None:
    good = batch(ReagentKind.WATER)
    bad = batch(ReagentKind.WATER, registered=False)
    others = [batch(ReagentKind.LAL_REAGENT), batch(ReagentKind.ENDOTOXIN)]
    assert not check_reagents([*others, good, bad], TODAY).passed
    assert not check_reagents([*others, bad, good], TODAY).passed


def test_locked_experiment_cannot_start_until_rechecked() -> None:
    experiment = make_experiment()
    experiment.apply(E.SUBMIT_FOR_REAGENT_CHECK)
    experiment.apply(check_reagents([batch(expiry=date(2020, 1, 1))], TODAY).event)
    assert experiment.state is S.REAGENT_LOCKED
    with pytest.raises(IllegalTransitionError):
        experiment.apply(E.START)
    experiment.apply(E.RETRY_REAGENT_CHECK)
    experiment.apply(check_reagents(all_batches(), TODAY).event)
    assert experiment.apply(E.START) is S.INCUBATING


def test_start_requires_assignments() -> None:
    experiment = make_experiment(tubes=())
    experiment.apply(E.SUBMIT_FOR_REAGENT_CHECK)
    experiment.apply(E.REAGENT_CHECK_PASSED)
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.START)
    assert experiment.state is S.READY


def test_failed_tube_goes_to_pending_review_not_negative() -> None:
    failed = result(1, None)
    assert failed.auto_interpretation is Interpretation.PENDING_REVIEW
    assert failed.review_status is ReviewStatus.PENDING
    assert failed.needs_review


def test_clear_result_needs_no_review() -> None:
    positive = result(1, GelState.FULLY_GELLED)
    assert positive.final_interpretation is Interpretation.POSITIVE
    assert positive.review_status is ReviewStatus.NOT_REQUIRED
    assert not positive.needs_review


def test_review_overrides_auto_and_can_be_repeated() -> None:
    reviewed = result(1, GelState.PARTIALLY_GELLED).with_review(
        interpretation=Interpretation.NEGATIVE, reviewed_by="a", reviewed_at=NOW
    )
    assert reviewed.final_interpretation is Interpretation.NEGATIVE
    assert reviewed.review_status is ReviewStatus.REVIEWED
    again = reviewed.with_review(
        interpretation=Interpretation.POSITIVE, reviewed_by="b", reviewed_at=NOW, note="改判"
    )
    assert again.final_interpretation is Interpretation.POSITIVE
    assert again.auto_interpretation is Interpretation.PENDING_REVIEW


def test_review_rejects_pending_conclusion_and_naive_time() -> None:
    tube = result(1, GelState.PARTIALLY_GELLED)
    with pytest.raises(ValueError):
        tube.with_review(
            interpretation=Interpretation.PENDING_REVIEW, reviewed_by="a", reviewed_at=NOW
        )
    with pytest.raises(ValueError):
        tube.with_review(
            interpretation=Interpretation.NEGATIVE,
            reviewed_by="a",
            reviewed_at=datetime(2026, 11, 4),
        )


def test_results_can_only_be_recorded_while_analyzing() -> None:
    with pytest.raises(TransitionGuardError):
        make_experiment().record_result(result(1, GelState.FULLY_GELLED))


def test_results_must_belong_to_experiment() -> None:
    with pytest.raises(TransitionGuardError):
        analyzing_experiment().record_result(result(9, GelState.FULLY_GELLED))


def test_all_analyzed_requires_every_result() -> None:
    experiment = analyzing_experiment()
    experiment.record_result(result(1, GelState.FULLY_GELLED))
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.ALL_ANALYZED_CLEAN)
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.ALL_ANALYZED_NEEDS_REVIEW)
    assert experiment.state is S.ANALYZING


def test_clean_event_is_rejected_when_review_is_pending() -> None:
    experiment = analyzing_experiment()
    experiment.record_result(result(1, GelState.FULLY_GELLED))
    experiment.record_result(result(2, None))
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.ALL_ANALYZED_CLEAN)


def test_needs_review_event_is_rejected_when_nothing_is_pending() -> None:
    experiment = analyzing_experiment()
    experiment.record_result(result(1, GelState.FULLY_GELLED))
    experiment.record_result(result(2, GelState.NOT_GELLED))
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.ALL_ANALYZED_NEEDS_REVIEW)


def test_review_flow_blocks_completion_until_all_reviewed() -> None:
    experiment = pending_review_experiment()
    assert list(experiment.pending_review_tubes) == [2]
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.REVIEW_COMPLETED)
    experiment.review_tube(
        2, interpretation=Interpretation.POSITIVE, reviewed_by="a", reviewed_at=NOW
    )
    assert list(experiment.pending_review_tubes) == []
    assert experiment.apply(E.REVIEW_COMPLETED) is S.COMPLETED


def test_cannot_sign_while_review_is_pending() -> None:
    experiment = pending_review_experiment()
    with pytest.raises(IllegalTransitionError):
        experiment.sign("a", NOW)
    assert experiment.signed_by is None


def test_review_is_only_allowed_in_pending_review_state() -> None:
    experiment = completed_experiment()
    with pytest.raises(TransitionGuardError):
        experiment.review_tube(
            1, interpretation=Interpretation.NEGATIVE, reviewed_by="a", reviewed_at=NOW
        )


def test_review_of_unknown_tube_is_rejected() -> None:
    with pytest.raises(TransitionGuardError):
        pending_review_experiment().review_tube(
            9, interpretation=Interpretation.NEGATIVE, reviewed_by="a", reviewed_at=NOW
        )


def test_reopen_review_before_signing() -> None:
    experiment = completed_experiment()
    assert experiment.apply(E.REOPEN_REVIEW) is S.PENDING_REVIEW


def test_sign_records_signer_and_is_final() -> None:
    experiment = completed_experiment()
    assert experiment.sign("a", NOW) is S.SIGNED
    assert (experiment.signed_by, experiment.signed_at) == ("a", NOW)
    with pytest.raises(IllegalTransitionError):
        experiment.apply(E.REOPEN_REVIEW)
    with pytest.raises(IllegalTransitionError):
        experiment.sign("b", NOW)
    assert experiment.signed_by == "a"


def test_sign_event_must_go_through_sign_method() -> None:
    experiment = completed_experiment()
    with pytest.raises(TransitionGuardError):
        experiment.apply(E.SIGN)
    assert experiment.state is S.COMPLETED


def test_sign_rejects_naive_time_without_side_effects() -> None:
    experiment = completed_experiment()
    with pytest.raises(ValueError):
        experiment.sign("a", datetime(2026, 11, 4))
    assert experiment.signed_by is None
    assert experiment.state is S.COMPLETED


def test_experiment_rejects_duplicate_tubes_and_naive_time() -> None:
    with pytest.raises(ValueError):
        make_experiment(tubes=(1, 1))
    with pytest.raises(ValueError):
        Experiment(
            experiment_id="e",
            experiment_type=ExperimentType.LIMIT,
            template_id="t",
            instrument_no="i",
            operator="op",
            created_at=datetime(2026, 11, 4),
            assignments=(),
        )


def test_audit_event_requires_aware_time() -> None:
    with pytest.raises(ValueError):
        AuditEvent(
            occurred_at=datetime(2026, 11, 4),
            actor="a",
            actor_role=None,
            action=AuditAction.LOGIN,
            detail="d",
        )
