import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet
from redis.asyncio import Redis

from app.domain.errors import LoginUnavailable, OidcFailed

log = logging.getLogger(__name__)

FLOW_TTL = 600  # seconds a login may take between the redirect and the callback
TIMEOUT = 10  # every outbound OIDC call (spec)
_STATE = re.compile(r"[A-Za-z0-9_-]{20,64}")


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    transport: httpx2.AsyncBaseTransport | None = None  # tests inject an in-process issuer


@dataclass(frozen=True)
class Identity:
    email: str
    name: str
    next: str | None = (
        None  # where the user was going before sign-in (already checked by safe_next)
    )


def safe_next(raw: str | None) -> str | None:
    """A same-origin relative path or None; the same rule as the web's safeNext (no `//`, backslash or control chars)."""
    if not raw or len(raw) > 2000 or not raw.startswith("/") or raw.startswith("//"):
        return None
    return None if any(c == "\\" or ord(c) < 32 or ord(c) == 127 for c in raw) else raw


def _client(cfg: OidcConfig) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        redirect_uri=cfg.redirect_uri,
        scope="openid email profile",
        code_challenge_method="S256",
        timeout=TIMEOUT,
        transport=cfg.transport,
    )


async def _discover(cfg: OidcConfig) -> dict[str, str]:
    async with httpx2.AsyncClient(transport=cfg.transport, timeout=TIMEOUT) as http:
        resp = await http.get(f"{cfg.issuer}/.well-known/openid-configuration")
        resp.raise_for_status()
        doc = resp.json()
    if doc.get("issuer") != cfg.issuer:  # a discovery document must name the issuer we asked
        raise ValueError("issuer mismatch in the discovery document")
    return doc


async def begin_login(
    cfg: OidcConfig, redis: Redis, next_path: str | None = None
) -> tuple[str, str]:
    """(issuer URL, state): state, nonce and a PKCE S256 challenge, all single-use. The caller binds `state` to
    the browser with a cookie: without that, a callback URL planted in another browser would log it in as us."""
    try:
        doc = await _discover(cfg)
    except Exception as e:  # noqa: BLE001 - unreachable, slow or wrong issuer: login is unavailable, not a 500
        log.warning("oidc discovery failed: %s", type(e).__name__)
        raise LoginUnavailable from None
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    url, _ = _client(cfg).create_authorization_url(
        doc["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier
    )
    try:
        await redis.set(
            f"oidc:{state}",
            json.dumps({"v": verifier, "n": nonce, "next": safe_next(next_path)}),
            ex=FLOW_TTL,
        )
    except Exception as e:  # noqa: BLE001 - no flow store, no login: unavailable, not a 500 and not a half-flow
        log.warning("oidc flow store failed: %s", type(e).__name__)
        raise LoginUnavailable from None
    return url, state


async def complete_login(
    cfg: OidcConfig, redis: Redis, code: str, state: str, browser_state: str | None
) -> Identity:
    """Callback: redeem the code with the PKCE verifier, verify the id token, require a verified email.

    Every failure is the same OidcFailed to the caller; the reason is logged without any token or code."""
    try:
        return await _complete(cfg, redis, code, state, browser_state)
    except OidcFailed:
        raise
    except Exception as e:  # noqa: BLE001 - network, protocol, signature, claims: all the same outside
        log.warning("oidc login failed: %s", type(e).__name__)
        raise OidcFailed from None


async def _complete(
    cfg: OidcConfig, redis: Redis, code: str, state: str, browser_state: str | None
) -> Identity:
    if not _STATE.fullmatch(state or "") or not code:
        raise OidcFailed
    # The state must also be the one THIS browser was given at /login (login CSRF). Checked before Redis, so a
    # planted callback in the wrong browser cannot burn the real flow.
    if not browser_state or not hmac.compare_digest(browser_state.encode(), state.encode()):
        raise OidcFailed
    raw = await redis.getdel(f"oidc:{state}")  # single use: a replayed state finds nothing
    if raw is None:
        raise OidcFailed
    flow = json.loads(raw)
    doc = await _discover(cfg)
    token = await _client(cfg).fetch_token(
        doc["token_endpoint"], code=code, code_verifier=flow["v"]
    )
    async with httpx2.AsyncClient(transport=cfg.transport, timeout=TIMEOUT) as http:
        jwks_response = await http.get(doc["jwks_uri"])
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
    decoded = jwt.decode(token["id_token"], KeySet.import_key_set(jwks), algorithms=["RS256"])
    registry = jwt.JWTClaimsRegistry(
        iss={"essential": True, "value": cfg.issuer},
        aud={"essential": True, "value": cfg.client_id},
        exp={"essential": True},
        nonce={"essential": True, "value": flow["n"]},
    )
    registry.validate(decoded.claims)
    claims = decoded.claims
    audience = claims.get("aud")
    if claims.get("azp", cfg.client_id) != cfg.client_id or (
        isinstance(audience, list) and len(audience) > 1 and "azp" not in claims
    ):
        raise OidcFailed  # a token issued to another party, or several audiences without saying whose it is
    email = claims.get("email")
    if claims.get("email_verified") is not True:  # only the boolean true: not "true", not 1
        raise OidcFailed
    if (
        not isinstance(email, str)
        or "@" not in email
        or len(email) > 320
        or any(c.isspace() or ord(c) < 32 for c in email)
    ):
        raise OidcFailed  # also keeps a NUL (which Postgres cannot store) out of the database layer
    name = claims.get("name")
    return Identity(
        email.strip().lower(),
        (name if isinstance(name, str) and name.strip() else email.split("@")[0])[:200],
        safe_next(flow.get("next")),  # re-checked on the way out, whatever the store held
    )
