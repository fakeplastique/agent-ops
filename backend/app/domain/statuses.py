from enum import IntEnum, StrEnum
from typing import Any

from app.domain.errors import InvalidInput


class Status(StrEnum):
    PENDING_VOTES = "pending_votes"
    PENDING_DISCUSSION = "pending_discussion"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL = frozenset({Status.APPROVED, Status.REJECTED, Status.EXPIRED, Status.CANCELLED})


class Verdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class Risk(IntEnum):
    # Order is the severity order, so max() is "at least as risky as both".
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def of(cls, value: Any) -> "Risk":
        """Coerce a Risk, its name ('low') or its int; anything else fails closed."""
        try:
            return cls[value.upper()] if isinstance(value, str) else cls(value)
        except (KeyError, ValueError):
            raise InvalidInput(f"unknown risk level {value!r}") from None
