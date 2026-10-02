from enum import StrEnum

from app.domain.errors import IllegalTransitionError
from app.domain.statuses import Risk, Status


class Event(StrEnum):
    ROUND_APPROVED = "round_approved"
    ROUND_REJECTED = "round_rejected"
    TO_DISCUSSION = "to_discussion"
    TO_APPROVAL = "to_approval"
    DEADLINE = "deadline"  # voting deadline passed with too few votes
    STEP_APPROVED_TOP = "step_approved_top"  # a step at or above the top required level
    STEP_REJECTED = "step_rejected"
    LAST_STEP_TIMED_OUT = "last_step_timed_out"
    REVISE = "revise"
    WITHDRAW = "withdraw"


S, E = Status, Event
_OPEN = (S.PENDING_VOTES, S.PENDING_DISCUSSION, S.PENDING_APPROVAL)

# Spec 4.1. Anything not listed is illegal; terminal statuses have no row at all.
TABLE: dict[tuple[Status, Event], Status] = {
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
    **{(s, E.REVISE): S.PENDING_VOTES for s in _OPEN},
    **{(s, E.WITHDRAW): S.CANCELLED for s in _OPEN},
}


def transition(status: Status, event: Event) -> Status:
    status, event = Status(status), Event(event)  # a raw string must not dodge the table
    try:
        return TABLE[(status, event)]
    except KeyError:
        raise IllegalTransitionError(f"{event} is not allowed from {status}") from None


def top_level(effective_risk: Risk, level_count: int) -> int:
    """Highest escalation level that must approve: all levels for high, the first otherwise."""
    if level_count < 1:
        raise ValueError("a request needing humans must have at least one level")
    return level_count if Risk.of(effective_risk) is Risk.HIGH else 1
