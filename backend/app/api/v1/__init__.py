from fastapi import APIRouter

from app.api.v1 import (
    auth,
    decisions,
    invitations,
    me,
    members,
    messages,
    organizations,
    receipts,
    steps,
    votes,
)

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(me.router)
api_v1_router.include_router(organizations.router)
api_v1_router.include_router(invitations.router)
api_v1_router.include_router(members.router)
api_v1_router.include_router(decisions.router)
api_v1_router.include_router(votes.router)
api_v1_router.include_router(messages.router)
api_v1_router.include_router(steps.router)
api_v1_router.include_router(receipts.router)

__all__ = ["api_v1_router"]
