from fastapi import APIRouter

from app.api.v1 import auth, me

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(me.router)

__all__ = ["api_v1_router"]
