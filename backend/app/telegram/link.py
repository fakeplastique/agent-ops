from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.api.deps import ActorDep, ClockDep, EngineDep, SessionUserDep, write_limit
from app.api.gating import gate
from app.authz.origin import require_same_origin
from app.authz.principals import UserPrincipal
from app.config import settings
from app.db.session import tenant_session
from app.domain.errors import Forbidden
from app.services import telegram

router = APIRouter(prefix="/me/telegram", tags=["me"])
TELEGRAM_GATE = gate(telegram.FLAG)


def require_user(actor: ActorDep) -> UserPrincipal:
    if not isinstance(actor, UserPrincipal):
        raise Forbidden("Telegram links belong to people, not agents")
    return actor


UserDep = Annotated[UserPrincipal, Depends(require_user)]


class LinkCodeOut(BaseModel):
    code: str  # shown once
    link: str | None  # the t.me deep link, when the bot's username is configured
    expires_at: datetime


class TelegramLinkOut(BaseModel):
    linked: bool


@router.post(
    "/link-code",
    status_code=201,
    response_model=LinkCodeOut,
    dependencies=[Depends(TELEGRAM_GATE), Depends(require_same_origin), Depends(write_limit)],
)
async def link_code(user: SessionUserDep, engine: EngineDep, clock: ClockDep) -> LinkCodeOut:
    """Web session only: a leaked token must not be able to bind its own Telegram account to the user."""
    async with tenant_session(engine, user.organization_id) as s:
        code, expires = await telegram.issue_code(s, user.organization_id, user.user_id, clock)
    bot = settings.telegram_bot_username
    return LinkCodeOut(
        code=code, link=f"https://t.me/{bot}?start={code}" if bot else None, expires_at=expires
    )


@router.get("", response_model=TelegramLinkOut)
async def link_state(user: UserDep, engine: EngineDep) -> TelegramLinkOut:
    async with tenant_session(engine, user.organization_id) as s:
        return TelegramLinkOut(linked=await telegram.is_linked(s, user.user_id))


@router.delete("", status_code=204, dependencies=[Depends(write_limit)])
async def unlink(user: UserDep, engine: EngineDep) -> Response:
    """Not plan-gated: a downgraded organization's members can still disconnect."""
    async with tenant_session(engine, user.organization_id) as s:
        await telegram.unlink(s, user.user_id)
    return Response(status_code=204)
