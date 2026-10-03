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
    "select v.session_id, v.round, v.verdict, v.confidence, v.rationale, v.request_version,"
    " v.created_at, v.revealed_at, i.status = 'active' as counts"
    " from votes v"
    " join agent_sessions a on a.organization_id = v.organization_id and a.id = v.session_id"
    " join agent_identities i on i.organization_id = a.organization_id and i.id = a.agent_identity_id"
    " where v.organization_id = :o and v.decision_request_id = :r order by v.created_at, v.id"
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
    cast = sum(  # same rule as the tally: only votes of active identities count
        1
        for v in rows
        if v["counts"] and v["request_version"] == req["version"] and v["round"] == current_round
    )
    visible = [
        {k: x for k, x in v.items() if k != "counts"}
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
