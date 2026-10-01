class DomainError(Exception):
    code = "domain_error"


class NotFoundError(DomainError):
    # Also what a foreign tenant's id gets: existence is never revealed.
    code = "not_found"
