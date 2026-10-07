import uuid

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from app.api import deps
from app.authz.matrix import Role
from app.authz.principals import AgentPrincipal, Principal, UserPrincipal
from app.authz.resolver import resolve_bearer
from app.domain.errors import Unauthenticated


class BearerVerifier:
    """The SDK's TokenVerifier: the same resolver as REST, re-run on every HTTP request (no cache)."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            got = await resolve_bearer(deps.get_engine(), f"Bearer {token}")
        except Unauthenticated:
            return None  # the SDK answers one 401 body for every failure: no existence hint
        p = got.principal
        if isinstance(p, AgentPrincipal):
            claims = {"kind": "agent", "id": str(p.agent_id)}
        else:
            claims = {"kind": "user", "id": str(p.user_id), "role": p.role.name}
        claims["org"] = str(got.organization_id)
        return AccessToken(token=token, client_id=claims["id"], scopes=[], claims=claims)


def current_principal() -> Principal:
    """The caller the verifier resolved for this request; tools never look at headers."""
    token = get_access_token()
    # Unreachable behind the SDK's RequireAuthMiddleware; fail closed anyway.
    if token is None or token.claims is None:
        raise Unauthenticated
    c = token.claims
    org, subject = uuid.UUID(c["org"]), uuid.UUID(c["id"])
    if c["kind"] == "agent":
        return AgentPrincipal(subject, org)
    return UserPrincipal(subject, org, Role[c["role"]])
