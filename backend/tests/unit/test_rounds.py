import math

import pytest

from app.domain.errors import InvalidInput
from app.domain.risk import effective_risk
from app.domain.rounds import Event, Vote, round_outcome
from app.domain.statuses import Risk, Verdict

A, R, X = Verdict.APPROVE, Verdict.REJECT, Verdict.ABSTAIN


def votes(*verdicts: Verdict, conf: float = 0.9, round: int = 1) -> list[Vote]:
    return [Vote(v, conf, round) for v in verdicts]


def outcome(vs, risk=Risk.LOW, quorum=3, threshold=0.8, rounds=1, round_no=1):
    return round_outcome(
        vs, risk=risk, quorum_size=quorum, threshold=threshold, rounds=rounds, round_no=round_no
    )


def test_auto_approve_only_low_all_approve_conf_at_or_above_threshold_full_quorum():
    assert outcome(votes(A, A, A)) is Event.ROUND_APPROVED
    # Each condition dropped on its own must stop the auto-approval.
    assert outcome(votes(A, A, A), risk=Risk.MEDIUM) is Event.TO_APPROVAL  # risk ignored
    assert outcome(votes(A, A, A), risk=Risk.HIGH) is Event.TO_APPROVAL
    assert outcome(votes(A, A, R)) is not Event.ROUND_APPROVED  # unanimity ignored
    low = votes(A, A) + [Vote(A, 0.79, 1)]
    assert outcome(low) is Event.TO_APPROVAL  # one confidence ignored
    assert outcome(votes(A, A)) is None  # quorum ignored: round still open


def test_abstain_is_not_approve():
    assert outcome(votes(A, A, X)) is Event.TO_APPROVAL  # catches: abstain counted toward unanimity


def test_confidence_equal_to_threshold_approves_below_does_not():
    assert outcome([Vote(A, 0.8, 1)] * 3) is Event.ROUND_APPROVED  # catches: > instead of >=
    assert outcome([Vote(A, 0.7999, 1)] * 3) is Event.TO_APPROVAL


@pytest.mark.parametrize("bad", [math.nan, math.inf, -0.1, 1.0000001])
def test_nan_or_out_of_range_confidence_rejected(bad):
    with pytest.raises(InvalidInput):  # catches: NaN compares false and slips past the threshold
        Vote(A, bad, 1)
    with pytest.raises(InvalidInput):
        outcome(votes(A, A, A), threshold=bad)


def test_round_two_uses_only_round_two_votes():
    stale = votes(A, A, A, round=1)
    r2 = votes(R, R, R, round=2)
    assert (
        outcome(stale + r2, rounds=2, round_no=2) is Event.ROUND_REJECTED
    )  # catches: stale round-1 mixed in
    assert (
        outcome(stale + votes(A, round=2), round_no=2) is None
    )  # one round-2 vote: round 2 not complete


def test_all_reject_is_rejected_whatever_the_risk():
    for risk in Risk:
        assert outcome(votes(R, R, R), risk=risk) is Event.ROUND_REJECTED


def test_split_with_rounds_one_goes_to_approval_and_with_two_to_discussion():
    assert outcome(votes(A, A, R), rounds=1) is Event.TO_APPROVAL
    assert outcome(votes(A, A, R), rounds=2) is Event.TO_DISCUSSION
    # Round 2 never discusses again.
    assert outcome(votes(A, A, R, round=2), rounds=2, round_no=2) is Event.TO_APPROVAL


def test_votes_beyond_quorum_are_an_error_not_a_pass():
    with pytest.raises(InvalidInput):
        outcome(votes(A, A, A, A))


def test_raw_string_verdict_is_coerced_or_rejected():
    assert Vote("approve", 0.9, 1).verdict is Verdict.APPROVE  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Vote("maybe", 0.9, 1)  # type: ignore[arg-type]


def test_unknown_action_type_floors_at_medium_and_never_lowers_declared():
    floors = {"read_file": "low", "wire_money": "high"}
    assert effective_risk(Risk.LOW, "brand_new", floors) is Risk.MEDIUM  # I7: unknown -> medium
    assert effective_risk(Risk.LOW, "read_file", floors) is Risk.LOW  # whitelisted low stays low
    assert (
        effective_risk(Risk.HIGH, "read_file", floors) is Risk.HIGH
    )  # catches: floor lowering a declared high
    assert effective_risk(Risk.LOW, "wire_money", floors) is Risk.HIGH
    assert effective_risk("high", "read_file", floors) is Risk.HIGH  # type: ignore[arg-type]


def test_garbage_floor_value_fails_closed():
    with pytest.raises(InvalidInput):
        effective_risk(Risk.LOW, "x", {"x": "lowest"})


@pytest.mark.parametrize("bad", [True, False, 1.0, 0, 4, None, "hıgh", "HIGH", " low", [], {}])
def test_risk_must_be_an_exact_level(bad):
    # catches: bool/float quietly becoming LOW (auto-approvable), non-ASCII look-alikes mapping to a level
    with pytest.raises(InvalidInput):
        Risk.of(bad)
    assert Risk.of("high") is Risk.HIGH
    assert Risk.of(2) is Risk.MEDIUM
    assert Risk.of(Risk.LOW) is Risk.LOW


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quorum": True},
        {"quorum": 0},
        {"rounds": 3},
        {"rounds": True},
        {"round_no": True},
        {"round_no": "1"},
    ],
)
def test_round_parameters_are_validated(kwargs):
    with pytest.raises(InvalidInput):  # catches: quorum_size=True auto-approving on one vote
        outcome(votes(A), **kwargs)


def test_vote_round_must_be_a_positive_int():
    for bad in (True, "1", 0, -1, 1.0):
        with pytest.raises(InvalidInput):
            Vote(A, 0.9, bad)  # type: ignore[arg-type]
