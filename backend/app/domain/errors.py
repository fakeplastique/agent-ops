class DomainError(Exception):
    code = "domain_error"


class NotFoundError(DomainError):
    # Also what a foreign tenant's id gets: existence is never revealed.
    code = "not_found"


class PlanLimitReached(DomainError):
    code = "plan_limit_reached"


class InvalidInput(DomainError, ValueError):
    code = "invalid_input"


class IllegalTransitionError(DomainError):
    code = "illegal_transition"


class PolicyExceedsPlan(DomainError):
    code = "policy_exceeds_plan"


class PolicyUnresolved(DomainError):
    code = "policy_unresolved"


class AlreadyVoted(DomainError):
    code = "already_voted"


class RoundClosed(DomainError):
    code = "round_closed"


class StaleVersion(DomainError):
    code = "stale_version"


class VoteNotAllowed(DomainError):
    code = "vote_not_allowed"


class AlreadyDecided(DomainError):
    code = "already_decided"


class Forbidden(DomainError):
    code = "forbidden"


class StepNotPending(DomainError):
    code = "step_not_pending"


class DiscussionClosed(DomainError):
    code = "discussion_closed"


class DiscussionRequired(DomainError):
    code = "discussion_required"


class Unauthenticated(DomainError):
    """One body for every credential failure (unknown, malformed, revoked, rotated, deactivated): no oracle."""

    code = "unauthenticated"

    def __init__(self) -> None:
        super().__init__("invalid or missing credentials")


class SessionLimit(DomainError):
    code = "session_limit"
