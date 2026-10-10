from typing import Any

from app.authz.matrix import Action, Role, can

_STEP = ("id", "decision_request_id", "request_version", "level")
_VOTE = ("decision_request_id", "session_id", "round", "request_version", "cast", "quorum")

# Allow-list per event type: the action a role needs, and the fields that may leave. A type or field not
# listed here never reaches a client (fail closed when an emitter adds one).
_TABLE: dict[str, tuple[Action, tuple[str, ...]]] = {
    "decision.created": (
        Action.READ,
        ("id", "status", "version", "effective_risk_level", "action_type"),
    ),
    "decision.revised": (Action.READ, ("id", "fields", "from_version", "version", "from_status")),
    "decision.status_changed": (Action.READ, ("id", "from", "to", "version")),
    "vote.cast": (Action.READ, _VOTE),
    "round.revealed": (Action.READ, ("decision_request_id", "round", "request_version", "votes")),
    "message.posted": (
        Action.READ,
        ("id", "decision_request_id", "request_version", "author_type"),
    ),
    "step.created": (Action.READ, (*_STEP, "required_role", "stands_in_for_level")),
    "step.decided": (Action.READ, (*_STEP, "decision")),
    "step.timed_out": (Action.READ, _STEP),
    "plan.changed": (Action.READ_PLAN, ("organization_id", "from", "to")),
}
_REVEALED_VOTE = ("session_id", "verdict", "confidence")
TYPES = frozenset(_TABLE)  # a test holds this equal to services.webhooks.EVENT_TYPES


def project(type_: str, payload: Any, role: Role) -> dict[str, Any] | None:
    """What this role may read of one event, or None. Blindness has no role exemption: a round-1 vote
    never carries verdict or confidence here, whoever asks (the rationale never leaves at all)."""
    rule = _TABLE.get(type_)
    # A payload that is no JSON object is skipped, never a 500 that would pin every later poll.
    if rule is None or not isinstance(payload, dict) or not can(role, rule[0]):
        return None
    out = {k: payload[k] for k in rule[1] if k in payload}
    # Only round 2 is open; `type is int` keeps True (== 1) and "2" from passing as a round number.
    if type_ == "vote.cast" and type(payload.get("round")) is int and payload["round"] == 2:
        out |= {k: payload[k] for k in ("verdict", "confidence") if k in payload}
    if type_ == "round.revealed":
        votes = payload.get("votes")
        out["votes"] = [
            {k: v[k] for k in _REVEALED_VOTE if k in v}
            for v in (votes if isinstance(votes, list) else [])
            if isinstance(v, dict)
        ]
    return out
