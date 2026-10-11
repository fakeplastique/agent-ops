from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from app.api.deps import COOKIE, ActorDep, RedisDep, write_limit
from app.authz.principals import UserPrincipal
from app.domain.errors import Forbidden
from app.realtime.access import credential_of
from app.realtime.ws import TICKET_TTL, issue_ticket

router = APIRouter(prefix="/realtime", tags=["realtime"])


class TicketOut(BaseModel):
    ticket: str
    expires_in: int


@router.post(
    "/ticket", status_code=201, response_model=TicketOut, dependencies=[Depends(write_limit)]
)
async def create_ticket(
    request: Request,
    actor: ActorDep,
    redis: RedisDep,
    authorization: Annotated[str | None, Header()] = None,
) -> TicketOut:
    """A single-use ticket for `/ws`, bound to the caller, the credential's organization and the credential
    itself (by hash), which the socket re-verifies. Counted as a write, so minting is rate limited."""
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("the event feed is for users")
    cred = credential_of(authorization, request.cookies.get(COOKIE))
    ticket = await issue_ticket(redis, actor.user_id, actor.organization_id, cred)
    return TicketOut(ticket=ticket, expires_in=TICKET_TTL)
