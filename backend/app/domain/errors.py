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
