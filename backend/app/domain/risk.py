import re
from collections.abc import Mapping
from typing import Any

from app.domain.errors import InvalidInput
from app.domain.statuses import Risk

# Lowercase ASCII slug: no case, whitespace or homoglyph variant can dodge a floor set on the real name.
ACTION_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


def effective_risk(declared: Any, action_type: str, floors: Mapping[str, Any]) -> Risk:
    """At least the declared risk and at least the policy floor; an unlisted action type floors at medium."""
    if not isinstance(floors, Mapping):
        raise InvalidInput("risk floors must be a mapping")
    if not isinstance(action_type, str) or not ACTION_TYPE.fullmatch(action_type):
        raise InvalidInput(f"invalid action type {action_type!r}")
    return max(Risk.of(declared), Risk.of(floors.get(action_type, Risk.MEDIUM)))
