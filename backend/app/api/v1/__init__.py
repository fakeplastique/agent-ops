from fastapi import APIRouter

from app.api.v1 import (
    agents,
    audit,
    auth,
    decisions,
    events,
    invitations,
    me,
    members,
    messages,
    organizations,
    plan,
    platform,
    policies,
    receipts,
    stats,
    steps,
    teams,
    tokens,
    votes,
    webhooks,
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
api_v1_router.include_router(audit.router)
api_v1_router.include_router(agents.router)
api_v1_router.include_router(teams.router)
api_v1_router.include_router(policies.router)
api_v1_router.include_router(tokens.router)
api_v1_router.include_router(plan.router)
api_v1_router.include_router(platform.router)
api_v1_router.include_router(webhooks.router)
api_v1_router.include_router(stats.router)
api_v1_router.include_router(events.router)

__all__ = ["api_v1_router"]
