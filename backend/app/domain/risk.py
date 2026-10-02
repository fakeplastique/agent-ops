from collections.abc import Mapping
from typing import Any

from app.domain.statuses import Risk


def effective_risk(declared: Any, action_type: str, floors: Mapping[str, Any]) -> Risk:
    """At least the declared risk and at least the policy floor; an unlisted action type floors at medium."""
    return max(Risk.of(declared), Risk.of(floors.get(action_type, Risk.MEDIUM)))
