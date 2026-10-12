"""An in-process OIDC issuer and an app client wired to the test database, Redis db 15 and a movable clock."""

import hashlib
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

ISSUER = "https://issuer.test"
CLIENT_ID = "client-1"
ORIGIN = "https://app.test"


class Tick:
    """A clock the test moves."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class FakeIssuer:
    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.other_key = RSAKey.generate_key(
            2048, parameters={"kid": "k1", "use": "sig"}
        )  # same kid, other key
        self.codes: dict[str, dict[str, Any]] = {}
        self.override: dict[str, Any] = {}  # what the NEXT id_token gets wrong
        self.app = Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self._discovery),
                Route("/jwks", self._jwks),
                Route("/token", self._token, methods=["POST"]),
            ]
        )

    def transport(self) -> httpx2.ASGITransport:
        return httpx2.ASGITransport(app=self.app)

    async def _discovery(self, request: Any) -> JSONResponse:
        return JSONResponse(
            {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            }
        )

    async def _jwks(self, request: Any) -> JSONResponse:
        return JSONResponse(KeySet([self.key]).as_dict(private=False))

    def authorize(
        self, login_redirect: str, email: str, name: str = "Test User", **claims: Any
    ) -> str:
        """What the browser does at the issuer: returns the callback URL it would be sent back to."""
        q = {k: v[0] for k, v in parse_qs(urlparse(login_redirect).query).items()}
        assert (
            q["code_challenge_method"] == "S256"
            and q["response_type"] == "code"
            and q["client_id"] == CLIENT_ID
        )
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "email": email,
            "name": name,
            "claims": claims,
        }
        return f"{q['redirect_uri']}?code={code}&state={q['state']}"

    async def _token(self, request: Any) -> JSONResponse:
        form = await request.form()
        entry = self.codes.pop(form.get("code", ""), None)
        if entry is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        verifier = form.get("code_verifier", "")
        challenge = (
            urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        )
        if challenge != entry["challenge"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE"}, status_code=400
            )
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": f"sub-{uuid.uuid5(uuid.NAMESPACE_DNS, entry['email'])}",
            "email": entry["email"], "email_verified": True, "name": entry["name"],
            "iat": now, "exp": now + 600, "nonce": entry["nonce"],
        }  # fmt: skip
        claims |= entry["claims"] | self.override
        self.override = {}
        key = self.other_key if claims.pop("_sign_with_other", False) else self.key
        header = {"alg": claims.pop("_alg", "RS256"), "kid": "k1"}
        id_token = (
            jwt.encode(header, claims, key) if header["alg"] == "RS256" else _unsigned(claims)
        )
        return JSONResponse(
            {"access_token": "at", "token_type": "Bearer", "expires_in": 3600, "id_token": id_token}
        )


def _unsigned(claims: dict[str, Any]) -> str:
    import json
    from base64 import urlsafe_b64encode as b64

    def enc(obj: Any) -> str:
        return b64(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc(claims)}."


class Harness:
    def __init__(
        self, client: httpx.AsyncClient, fake: FakeIssuer, clock: Tick, storage: Any = None
    ) -> None:
        self.client, self.fake, self.clock, self.storage = client, fake, clock, storage

    async def login(self, email: str, name: str = "Test User", **claims: Any) -> httpx.Response:
        """The whole browser dance; returns the callback response."""
        start = await self.client.get("/api/v1/auth/login")
        assert start.status_code == 302, start.text
        callback = self.fake.authorize(start.headers["location"], email, name, **claims)
        return await self.client.get(callback.removeprefix(ORIGIN))

    async def post(self, path: str, origin: str | None = ORIGIN, **kw: Any) -> httpx.Response:
        headers = {"Origin": origin} if origin is not None else {}
        return await self.client.post(path, headers=headers, **kw)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)
