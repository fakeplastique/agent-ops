import hashlib
import logging

import pytest
from sqlalchemy import text

from app.authz.resolver import resolve_bearer
from app.db.session import tenant_session
from app.domain.errors import Unauthenticated
from app.services.tokens import create_pat, issue_agent_token, revoke_agent, revoke_pat
from tests.integration.helpers import fetch, make_org, make_world, staff

pytestmark = pytest.mark.anyio


async def whole_database_text(owner) -> str:
    """Every row of every table in the public schema, as text."""
    tables = [
        r[0]
        for r in await fetch(owner, "select tablename from pg_tables where schemaname='public'")
    ]
    out = []
    for t in tables:
        out += [r[0] for r in await fetch(owner, f'select row_to_json(x)::text from "{t}" x')]
    return "\n".join(out)


async def test_raw_token_never_in_db_logs_or_error_bodies(app_engine, owner_engine, caplog):
    caplog.set_level(logging.DEBUG)
    w = await make_world(owner_engine)
    user = await staff(owner_engine, w.org, "member")
    raws, errors = [], []
    async with tenant_session(app_engine, w.org) as s:
        first = await issue_agent_token(s, w.org, w.identity)
        pat_id, pat = await create_pat(s, w.org, user, "ci")
    raws += [first, pat]
    await resolve_bearer(app_engine, f"Bearer {first}")  # use
    async with tenant_session(app_engine, w.org) as s:
        second = await issue_agent_token(s, w.org, w.identity)  # rotate
    raws.append(second)
    for bad in (first, "aop_" + "x" * 43, "Bearer", None):  # failures: rotated, unknown, malformed
        try:
            await resolve_bearer(
                app_engine, bad if bad is None or bad == "Bearer" else f"Bearer {bad}"
            )
        except Unauthenticated as e:
            errors.append(f"{e} {e.code}")
    async with tenant_session(app_engine, w.org) as s:
        await revoke_pat(s, w.org, pat_id)
        await revoke_agent(s, w.org, w.identity)
    for bad in (second, pat):
        try:
            await resolve_bearer(app_engine, f"Bearer {bad}")  # revoked
        except Unauthenticated as e:
            errors.append(f"{e} {e.code}")
    haystack = (
        await whole_database_text(owner_engine) + "\n" + caplog.text + "\n" + "\n".join(errors)
    )
    for raw in raws:
        body = raw.split("_", 1)[1]
        # catches: a token in a row, a log line or a 401 body (only the hash and an 8-char prefix may be stored)
        assert raw not in haystack and body not in haystack, raw[:8]
    assert len(errors) == 6


async def test_hash_stored_prefix_lookup_constant_time_compare(
    app_engine, owner_engine, monkeypatch
):
    import hmac

    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        token = await issue_agent_token(s, w.org, w.identity)
    row = (
        await fetch(
            owner_engine,
            "select token_prefix, token_hash from agent_tokens where agent_identity_id=:i",
            i=w.identity,
        )
    )[0]
    body = token[len("aop_") :]
    assert token.startswith("aop_") and len(body) == 43
    assert (
        row[0] == body[:8] and row[1] == hashlib.sha256(token.encode()).hexdigest()
    )  # prefix + SHA-256 of the whole token
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    await resolve_bearer(app_engine, f"Bearer {token}")
    assert calls  # catches: a plain == on the hash (timing oracle)


async def test_unknown_and_revoked_token_same_401_body(app_engine, owner_engine):
    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        live = await issue_agent_token(s, w.org, w.identity)
        await revoke_agent(s, w.org, w.identity)
    org = await make_org(owner_engine)
    user = await staff(owner_engine, org, "member")
    async with tenant_session(app_engine, org) as s:
        _, pat = await create_pat(s, org, user, "ci")
        pat_id = await s.scalar(
            text("select id from personal_access_tokens where user_id=:u"), {"u": user}
        )
        await revoke_pat(s, org, pat_id)
    bodies = set()
    for header in (
        f"Bearer {live}",
        f"Bearer {pat}",
        "Bearer aop_" + "q" * 43,
        "Bearer pat_" + "q" * 43,
        "Bearer nope",
        None,
        "Basic x",
    ):
        with pytest.raises(Unauthenticated) as err:
            await resolve_bearer(app_engine, header)
        bodies.add((str(err.value), err.value.code))
    assert len(bodies) == 1  # catches: a token or account existence oracle
