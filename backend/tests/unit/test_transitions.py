from itertools import product

import pytest

from app.domain.errors import IllegalTransitionError
from app.domain.statuses import TERMINAL, Risk, Status
from app.domain.transitions import Event, top_level, transition

S, E = Status, Event
OPEN = (S.PENDING_VOTES, S.PENDING_DISCUSSION, S.PENDING_APPROVAL)

# Literal copy of spec 4.1 (revise and withdraw expanded over their source statuses).
ALLOWED = {
    (S.PENDING_VOTES, E.ROUND_APPROVED): S.APPROVED,
    (S.PENDING_VOTES, E.ROUND_REJECTED): S.REJECTED,
    (S.PENDING_VOTES, E.TO_DISCUSSION): S.PENDING_DISCUSSION,
    (S.PENDING_VOTES, E.TO_APPROVAL): S.PENDING_APPROVAL,
    (S.PENDING_VOTES, E.DEADLINE): S.PENDING_APPROVAL,
    (S.PENDING_DISCUSSION, E.ROUND_APPROVED): S.APPROVED,
    (S.PENDING_DISCUSSION, E.ROUND_REJECTED): S.REJECTED,
    (S.PENDING_DISCUSSION, E.TO_APPROVAL): S.PENDING_APPROVAL,
    (S.PENDING_DISCUSSION, E.DEADLINE): S.PENDING_APPROVAL,
    (S.PENDING_APPROVAL, E.STEP_APPROVED_TOP): S.APPROVED,
    (S.PENDING_APPROVAL, E.STEP_REJECTED): S.REJECTED,
    (S.PENDING_APPROVAL, E.LAST_STEP_TIMED_OUT): S.EXPIRED,
    **{(s, E.REVISE): S.PENDING_VOTES for s in OPEN},
    **{(s, E.WITHDRAW): S.CANCELLED for s in OPEN},
}


@pytest.mark.parametrize(("status", "event"), list(product(Status, Event)))
def test_every_status_event_pair_outside_the_table_raises(status, event):
    # catches: an undocumented transition (the whole product is walked, not a sample)
    if (status, event) in ALLOWED:
        assert transition(status, event) is ALLOWED[(status, event)]
    else:
        with pytest.raises(IllegalTransitionError):
            transition(status, event)


def test_terminal_states_have_no_exits():
    assert TERMINAL == {S.APPROVED, S.REJECTED, S.EXPIRED, S.CANCELLED}
    for status, event in product(TERMINAL, Event):  # catches: reopening an approved request
        with pytest.raises(IllegalTransitionError):
            transition(status, event)


def test_raw_string_status_and_event_are_coerced():
    assert transition("pending_votes", "withdraw") is S.CANCELLED  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        transition("nope", "withdraw")  # type: ignore[arg-type]


def test_top_level_medium_is_one_high_is_all():
    assert top_level(Risk.MEDIUM, 3) == 1  # catches: medium walking all levels
    assert top_level(Risk.LOW, 3) == 1  # a low that fell to humans
    assert top_level(Risk.HIGH, 3) == 3
    assert top_level(Risk.HIGH, 1) == 1
    with pytest.raises(ValueError):
        top_level(Risk.HIGH, 0)  # no levels: nothing could ever approve
