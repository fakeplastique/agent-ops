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
        """Coerce a Risk, its exact lowercase name or its int. Bool, float and look-alikes fail closed."""
        if isinstance(value, cls):
            return value
        if isinstance(value, int) and type(value) is not int:  # bool, or another enum's value 1
            raise InvalidInput(f"unknown risk level {value!r}")
        if isinstance(value, str):
            level = {r.name.lower(): r for r in cls}.get(value)
        else:
            level = (
                None
                if isinstance(value, bool) or not isinstance(value, int)
                else cls._value2member_map_.get(value)
            )
        if level is None:
            raise InvalidInput(f"unknown risk level {value!r}")
        return level  # type: ignore[return-value]
