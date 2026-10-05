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


class OidcFailed(DomainError):
    """Every login failure (state, PKCE, token, signature, claims, unverified email) looks the same outside."""

    code = "login_failed"

    def __init__(self) -> None:
        super().__init__("login failed")


class LoginUnavailable(DomainError):
    code = "login_unavailable"

    def __init__(self) -> None:
        super().__init__("login is not configured")


class IdempotencyKeyReuse(DomainError):
    code = "idempotency_key_reuse"

    def __init__(self) -> None:
        super().__init__("this Idempotency-Key was already used with a different request")


class RateLimited(DomainError):
    code = "rate_limited"

    def __init__(self, retry_after: int = 1) -> None:
        super().__init__("too many requests")
        self.retry_after = max(1, retry_after)


class RateLimiterUnavailable(DomainError):
    """Writes fail closed when the limiter store is down: an outage must not lift the limit."""

    code = "rate_limiter_unavailable"

    def __init__(self) -> None:
        super().__init__("rate limiter unavailable")


class InvitationUnusable(DomainError):
    """Unknown, expired, used and revoked look the same: the link holder learns nothing about its state."""

    code = "invitation_unusable"

    def __init__(self) -> None:
        super().__init__("This invitation can't be used")


class InvitationEmailMismatch(DomainError):
    code = "invitation_email_mismatch"

    def __init__(self, invited_email: str = "") -> None:
        super().__init__("this invitation is for a different account")
        self.details = {"invited_email": invited_email}


class AlreadyMember(DomainError):
    code = "already_member"


class LastOrgAdmin(DomainError):
    code = "last_org_admin"

    def __init__(self) -> None:
        super().__init__("an organization keeps at least one org_admin")


class SlugTaken(DomainError):
    code = "slug_taken"
