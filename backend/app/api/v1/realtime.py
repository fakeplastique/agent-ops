from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import ActorDep, RedisDep, write_limit
from app.authz.principals import UserPrincipal
from app.domain.errors import Forbidden
from app.realtime.ws import TICKET_TTL, issue_ticket

router = APIRouter(prefix="/realtime", tags=["realtime"])


class TicketOut(BaseModel):
    ticket: str
    expires_in: int


@router.post(
    "/ticket", status_code=201, response_model=TicketOut, dependencies=[Depends(write_limit)]
)
async def create_ticket(actor: ActorDep, redis: RedisDep) -> TicketOut:
    """A single-use ticket for `/ws`, bound to the caller and the organization of the credential.
    Counted as a write, so minting is rate limited per principal and organization."""
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("the event feed is for users")
    ticket = await issue_ticket(redis, actor.user_id, actor.organization_id)
    return TicketOut(ticket=ticket, expires_in=TICKET_TTL)
