import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_flat_params
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain import errors as de
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

# One row per domain error; a test fails when a new DomainError subclass has no row.
DOMAIN_STATUS_CODES: dict[type[DomainError], int] = {
    de.Unauthenticated: 401,
    de.Forbidden: 403,
    de.VoteNotAllowed: 403,
    de.NotFoundError: 404,
    de.PlanLimitReached: 402,
    de.PolicyExceedsPlan: 402,
    de.InvalidInput: 422,
    de.OidcFailed: 400,
    de.LoginUnavailable: 503,
    de.IllegalTransitionError: 409,
    de.PolicyUnresolved: 409,
    de.AlreadyVoted: 409,
    de.RoundClosed: 409,
    de.StaleVersion: 409,
    de.AlreadyDecided: 409,
    de.StepNotPending: 409,
    de.DiscussionClosed: 409,
    de.DiscussionRequired: 409,
    de.SessionLimit: 409,
    de.InvitationUnusable: 404,
    de.InvitationEmailMismatch: 403,
    de.AlreadyMember: 409,
    de.LastOrgAdmin: 409,
    de.SlugTaken: 409,
    de.AlreadyExists: 409,
    de.TeamInUse: 409,
    de.PlanFeatureRequired: 402,
    de.PolicyAbovePlan: 422,
    de.IdempotencyKeyReuse: 422,
    de.RateLimited: 429,
    de.RateLimiterUnavailable: 503,
}


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    402: "plan_limit_reached",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "invalid_input",
    429: "rate_limited",
}


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any


class ErrorBody(BaseModel):
    error: ErrorDetail


_DOC = {
    400: "Bad request",
    401: "Missing, invalid or revoked credential",
    402: "The organization's plan does not allow this",
    403: "Role, principal kind or origin not allowed",
    404: "Not found (also another organization's id)",
    409: "Conflicts with the current state or version",
    422: "Invalid input",
    429: "Rate limited; see Retry-After",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` for a route: each code documented with the error envelope."""
    return {c: {"model": ErrorBody, "description": _DOC[c]} for c in codes}


_AUTH = {"get_actor", "get_current_principal", "web_session"}
_ROLE = {"get_actor", "session_user", "require_same_origin", "require_org_admin", "require_user"}
# Bearer resolution throttles failed attempts per IP, so any bearer route can answer 429 too.
_LIMIT = {"get_actor", "get_current_principal", "write_limit", "user_write_limit", "public_limit"}


def _calls(dependant: Dependant) -> set[str]:
    out = set()
    for d in dependant.dependencies:
        out.add(getattr(d.call, "__name__", ""))
        out |= _calls(d)
    return out


def _routes(router: Any):
    for r in router.routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):  # FastAPI wraps an included router
            yield from _routes(r.original_router)


def document_errors(app: FastAPI) -> None:
    """Add the 401/403/404/422/429 a route's dependencies can raise; 400/402/409 the route declares itself."""
    for r in _routes(app):
        calls = _calls(r.dependant)
        codes = [
            *([401] if calls & _AUTH else []),
            *([403] if calls & _ROLE else []),
            *([404] if r.param_convertors else []),
            *([422] if get_flat_params(r.dependant) or r.body_field else []),  # FastAPI's own rule
            *([429] if calls & _LIMIT else []),
        ]
        r.responses = error_responses(*codes) | r.responses


def error_envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    safe_details = jsonable_encoder(details) if details is not None else {}
    return {
        "error": {
            "code": code,
            "message": message,
            "details": safe_details,
        }
    }


def get_status_code_for_domain_error(exc: DomainError) -> int:
    return DOMAIN_STATUS_CODES.get(
        type(exc), 500
    )  # unmapped = our bug: 500, never a misleading 4xx


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        status_code = get_status_code_for_domain_error(exc)
        code = getattr(exc, "code", "domain_error")
        message = str(exc) or code
        details = getattr(exc, "details", {})
        retry_after = getattr(exc, "retry_after", None)
        return JSONResponse(
            status_code=status_code,
            content=error_envelope(code=code, message=message, details=details),
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                code="invalid_input",
                message="Validation error",
                details=exc.errors(),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        message = str(exc.detail) if exc.detail else "HTTP error"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code=code, message=message, details={}),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        logger.exception("Unhandled server error request_id=%s: %s", request_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                code="internal_error",
                message="Internal server error",
                details={"request_id": request_id},
            ),
            headers={"X-Request-ID": request_id},
        )
