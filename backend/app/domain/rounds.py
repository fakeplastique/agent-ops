from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.errors import InvalidInput
from app.domain.statuses import Risk, Verdict
from app.domain.transitions import Event


def _check_confidence(value: Any) -> float:
    # `not 0 <= x <= 1` is also true for NaN, which would otherwise compare false everywhere.
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
        raise InvalidInput(f"confidence must be a number in [0, 1], got {value!r}")
    return float(value)


def _check_int(name: str, value: Any, allowed: tuple[int, ...] | None = None) -> int:
    # bool is an int subclass: True would pass as quorum 1 or round 1.
    ok = isinstance(value, int) and not isinstance(value, bool)
    if not ok or (value not in allowed if allowed else value < 1):
        raise InvalidInput(f"{name} is invalid: {value!r}")
    return value


@dataclass(frozen=True)
class Vote:
    verdict: Verdict
    confidence: float
    round: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdict", Verdict(self.verdict))
        object.__setattr__(self, "confidence", _check_confidence(self.confidence))
        _check_int("round", self.round)


def round_outcome(
    votes: Sequence[Vote],
    *,
    risk: Risk,
    quorum_size: int,
    threshold: float,
    rounds: int,
    round_no: int,
) -> Event | None:
    """Event for a complete round (None while open). Callers pass valid votes of this version only."""
    threshold = _check_confidence(threshold)
    _check_int("quorum_size", quorum_size)
    _check_int("rounds", rounds, (1, 2))
    _check_int("round_no", round_no, (1, 2))
    if round_no > rounds:
        raise InvalidInput("round_no exceeds the policy's rounds")
    current = [v for v in votes if v.round == round_no]  # round 2 never sees round-1 votes
    if len(current) < quorum_size:
        return None
    if len(current) > quorum_size:
        raise InvalidInput("more votes than the quorum: the round should have closed")
    verdicts = {v.verdict for v in current}
    if verdicts == {Verdict.REJECT}:
        return Event.ROUND_REJECTED
    # I6: the only auto-approval. Agreement is not proof, so only low risk skips humans.
    if (
        verdicts == {Verdict.APPROVE}
        and Risk.of(risk) is Risk.LOW
        and all(v.confidence >= threshold for v in current)
    ):
        return Event.ROUND_APPROVED
    if rounds == 2 and round_no == 1 and len(verdicts) > 1:
        return Event.TO_DISCUSSION
    return Event.TO_APPROVAL
