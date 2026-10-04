from fastapi import Request

from app.config import settings
from app.domain.errors import Forbidden

_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_same_origin(request: Request) -> None:
    """A write authenticated by the session cookie must carry the web app's own Origin (FM36): a cross-site
    form or script cannot forge that header, and a missing one is refused too. Exact string match: no case
    folding, no trailing slash, no list. Bearer-token routes do not use this (they carry no ambient cookie)."""
    if request.method in _SAFE:
        return
    if request.headers.get("origin") != settings.web_origin:
        raise Forbidden("cross-site request refused")
