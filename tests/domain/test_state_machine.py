import itertools

import pytest

from gelinspect.domain.errors import IllegalTransitionError
from gelinspect.domain.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    ExperimentEvent,
    ExperimentState,
    allowed_events,
    next_state,
)

S = ExperimentState
E = ExperimentEvent

EXPECTED_TRANSITIONS = {
    (S.DRAFT, E.SUBMIT_FOR_REAGENT_CHECK): S.REAGENT_CHECK,
    (S.DRAFT, E.STOP): S.STOPPED,
    (S.REAGENT_CHECK, E.REAGENT_CHECK_PASSED): S.READY,
    (S.REAGENT_CHECK, E.REAGENT_CHECK_FAILED): S.REAGENT_LOCKED,
    (S.REAGENT_CHECK, E.EDIT): S.DRAFT,
    (S.REAGENT_CHECK, E.STOP): S.STOPPED,
    (S.REAGENT_LOCKED, E.RETRY_REAGENT_CHECK): S.REAGENT_CHECK,
    (S.REAGENT_LOCKED, E.EDIT): S.DRAFT,
    (S.REAGENT_LOCKED, E.STOP): S.STOPPED,
    (S.READY, E.START): S.INCUBATING,
    (S.READY, E.EDIT): S.DRAFT,
    (S.READY, E.STOP): S.STOPPED,
    (S.INCUBATING, E.ROW_DUE): S.READING,
    (S.INCUBATING, E.STOP): S.STOPPED,
    (S.INCUBATING, E.FAULT): S.FAULTED,
    (S.READING, E.ROW_CAPTURED): S.ANALYZING,
    (S.READING, E.STOP): S.STOPPED,
    (S.READING, E.FAULT): S.FAULTED,
    (S.ANALYZING, E.ROW_ANALYZED): S.INCUBATING,
    (S.ANALYZING, E.ROW_DUE): S.READING,
    (S.ANALYZING, E.ALL_ANALYZED_CLEAN): S.COMPLETED,
    (S.ANALYZING, E.ALL_ANALYZED_NEEDS_REVIEW): S.PENDING_REVIEW,
    (S.ANALYZING, E.STOP): S.STOPPED,
    (S.ANALYZING, E.FAULT): S.FAULTED,
    (S.PENDING_REVIEW, E.REVIEW_COMPLETED): S.COMPLETED,
    (S.COMPLETED, E.REOPEN_REVIEW): S.PENDING_REVIEW,
    (S.COMPLETED, E.SIGN): S.SIGNED,
    (S.FAULTED, E.RESUME_INCUBATION): S.INCUBATING,
    (S.FAULTED, E.RESUME_READING): S.READING,
    (S.FAULTED, E.RESUME_ANALYSIS): S.ANALYZING,
    (S.FAULTED, E.STOP): S.STOPPED,
}


def test_transition_table_matches_specification() -> None:
    assert dict(TRANSITIONS) == EXPECTED_TRANSITIONS


def test_every_state_and_event_combination_is_decided() -> None:
    # 穷举：表内的组合返回目标状态，表外的组合全部抛非法转换异常。
    for state, event in itertools.product(S, E):
        if (state, event) in EXPECTED_TRANSITIONS:
            assert next_state(state, event) is EXPECTED_TRANSITIONS[(state, event)]
        else:
            with pytest.raises(IllegalTransitionError) as exc_info:
                next_state(state, event)
            assert exc_info.value.state == state.value
            assert exc_info.value.event == event.value


def test_every_state_is_reachable_from_draft() -> None:
    reachable = {S.DRAFT}
    frontier = [S.DRAFT]
    while frontier:
        current = frontier.pop()
        for event in allowed_events(current):
            target = next_state(current, event)
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert reachable == set(S)


def test_terminal_states_accept_no_events() -> None:
    assert set(TERMINAL_STATES) == {S.SIGNED, S.STOPPED}
    for state in TERMINAL_STATES:
        assert allowed_events(state) == frozenset()


def test_non_terminal_states_can_progress() -> None:
    for state in set(S) - TERMINAL_STATES:
        assert allowed_events(state)


@pytest.mark.parametrize(
    "state",
    [S.DRAFT, S.REAGENT_CHECK, S.REAGENT_LOCKED, S.INCUBATING, S.READING, S.ANALYZING, S.FAULTED],
)
def test_locked_and_unchecked_experiments_cannot_start(state: ExperimentState) -> None:
    with pytest.raises(IllegalTransitionError):
        next_state(state, E.START)


@pytest.mark.parametrize("state", [state for state in S if state is not S.COMPLETED])
def test_signing_is_only_possible_from_completed(state: ExperimentState) -> None:
    with pytest.raises(IllegalTransitionError):
        next_state(state, E.SIGN)


def test_transition_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        TRANSITIONS[(S.DRAFT, E.SIGN)] = S.SIGNED  # type: ignore[index]
