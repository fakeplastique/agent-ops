import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError

_REQUEST = text(
    "select status, version, (policy_snapshot->>'quorum_size')::int as quorum"
    " from decision_requests where organization_id = :o and id = :r"
)
_VOTES = text(
    "select session_id, round, verdict, confidence, rationale, request_version, created_at, revealed_at"
    " from votes where organization_id = :o and decision_request_id = :r order by created_at, id"
)


async def visible_votes(
    session: AsyncSession,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    viewer_session_id: uuid.UUID | None,
) -> dict[str, Any]:
    """What this viewer may see. Round 1 stays blind until its round closes (`revealed_at`): until then
    the viewer gets only their own vote plus the count ("2 of 3 cast"). The caller must derive
    `viewer_session_id` from the authenticated principal, never from request input."""
    req = (
        (await session.execute(_REQUEST, {"o": organization_id, "r": request_id}))
        .mappings()
        .first()
    )
    if req is None:
        raise NotFoundError
    rows = (await session.execute(_VOTES, {"o": organization_id, "r": request_id})).mappings().all()
    current_round = 2 if req["status"] == "pending_discussion" else 1
    cast = sum(
        1 for v in rows if v["request_version"] == req["version"] and v["round"] == current_round
    )
    visible = [
        dict(v)
        for v in rows
        if v["session_id"] == viewer_session_id or v["round"] == 2 or v["revealed_at"] is not None
    ]
    return {
        "status": req["status"],
        "version": req["version"],
        "cast": cast,
        "quorum": req["quorum"],
        "votes": visible,
    }
