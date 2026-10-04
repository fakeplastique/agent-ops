from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    DomainError,
    Forbidden,
    LoginUnavailable,
    NotFoundError,
    OidcFailed,
    Unauthenticated,
)

_STATUS: dict[type[DomainError], int] = {
    Unauthenticated: 401,
    Forbidden: 403,
    NotFoundError: 404,
    OidcFailed: 400,
    LoginUnavailable: 503,
}


def install(app: FastAPI) -> None:
    """Domain errors as the one error body `{ "error": { "code", "message", "details" } }`.

    Only the errors these routes raise are mapped here; the REST foundation (S16) owns the full table."""

    for exc_type, status in _STATUS.items():

        async def handler(_: Request, exc: DomainError, status: int = status) -> JSONResponse:
            return JSONResponse(
                {"error": {"code": exc.code, "message": str(exc) or exc.code, "details": {}}},
                status_code=status,
            )

        app.add_exception_handler(exc_type, handler)  # type: ignore[arg-type]
