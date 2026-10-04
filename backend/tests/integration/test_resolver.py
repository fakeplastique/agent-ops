import pytest
from sqlalchemy import text

from app.authz.matrix import Role
from app.authz.principals import AgentPrincipal, UserPrincipal
from app.authz.resolver import resolve_bearer
from app.db.session import tenant_session
from app.domain.errors import Unauthenticated
from app.services.tokens import create_pat, issue_agent_token, revoke_agent, revoke_pat

from .helpers import fetch, make_org, make_user, make_world, staff

pytestmark = pytest.mark.anyio


async def agent_token(app_engine, w) -> str:
    async with tenant_session(app_engine, w.org) as s:
        return await issue_agent_token(s, w.org, w.identity)


async def resolve(app_engine, token, scheme="Bearer"):
    return await resolve_bearer(app_engine, f"{scheme} {token}")


async def test_an_agent_token_resolves_to_its_identity_and_its_own_org(app_engine, owner_engine):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    ta, tb = await agent_token(app_engine, a), await agent_token(app_engine, b)
    ra, rb = await resolve(app_engine, ta), await resolve(app_engine, tb)
    # catches: the org taken from a header or body: the resolver only ever sees the token
    assert (ra.organization_id, rb.organization_id) == (a.org, b.org)
    assert ra.principal == AgentPrincipal(a.identity, a.org) and rb.principal == AgentPrincipal(
        b.identity, b.org
    )


async def test_a_personal_access_token_resolves_to_its_user_org_and_current_role(
    app_engine, owner_engine
):
    org = await make_org(owner_engine)
    user = await staff(owner_engine, org, "manager")
    async with tenant_session(app_engine, org) as s:
        _, token = await create_pat(s, org, user, "ci")
    r = await resolve(app_engine, token)
    assert (
        r.organization_id == org
        and isinstance(r.principal, UserPrincipal)
        and r.principal.user_id == user
    )
    assert r.principal.role.name == "MANAGER"
    async with (
        owner_engine.begin() as c
    ):  # demoted: the very next request sees it, no cached principal
        await c.execute(
            text("update memberships set role='member' where organization_id=:o and user_id=:u"),
            {"o": org, "u": user},
        )
    assert (await resolve(app_engine, token)).principal == UserPrincipal(user, org, Role.MEMBER)


async def test_a_pat_of_a_user_who_left_the_org_is_denied(app_engine, owner_engine):
    org = await make_org(owner_engine)
    user = await staff(owner_engine, org, "member")
    async with tenant_session(app_engine, org) as s:
        _, token = await create_pat(s, org, user, "ci")
    async with owner_engine.begin() as c:
        await c.execute(
            text("delete from memberships where organization_id=:o and user_id=:u"),
            {"o": org, "u": user},
        )
    with pytest.raises(Unauthenticated):
        await resolve(app_engine, token)


async def test_a_pat_is_bound_to_the_org_it_was_minted_for(app_engine, owner_engine):
    a, b = await make_org(owner_engine), await make_org(owner_engine)
    user = await make_user(owner_engine)  # a member of both
    from .helpers import make_member

    await make_member(owner_engine, a, user, "org_admin")
    await make_member(owner_engine, b, user, "member")
    async with tenant_session(app_engine, a) as s:
        _, token = await create_pat(s, a, user, "for-a")
    r = await resolve(app_engine, token)
    assert r.organization_id == a  # never b, whatever the header says
    assert r.principal == UserPrincipal(user, a, Role.ORG_ADMIN)


async def test_revoked_identity_denied_on_next_request_not_after_ttl(app_engine, owner_engine):
    w = await make_world(owner_engine)
    token = await agent_token(app_engine, w)
    assert (await resolve(app_engine, token)).principal == AgentPrincipal(w.identity, w.org)
    async with tenant_session(app_engine, w.org) as s:
        await revoke_agent(s, w.org, w.identity)
    with pytest.raises(Unauthenticated):  # catches: a cached principal outliving revocation
        await resolve(app_engine, token)


async def test_an_identity_revoked_directly_is_denied_even_with_a_live_token_row(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    token = await agent_token(app_engine, w)
    async with owner_engine.begin() as c:  # status flipped without touching the token row
        await c.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": w.identity}
        )
    with pytest.raises(Unauthenticated):
        await resolve(app_engine, token)


async def test_rotate_invalidates_old_token_immediately(app_engine, owner_engine):
    w = await make_world(owner_engine)
    old = await agent_token(app_engine, w)
    new = await agent_token(app_engine, w)  # issuing again rotates
    assert old != new
    with pytest.raises(Unauthenticated):  # catches: both tokens valid
        await resolve(app_engine, old)
    assert (await resolve(app_engine, new)).principal == AgentPrincipal(w.identity, w.org)


async def test_a_revoked_pat_stops_working_at_once(app_engine, owner_engine):
    org = await make_org(owner_engine)
    user = await staff(owner_engine, org, "member")
    async with tenant_session(app_engine, org) as s:
        pat_id, token = await create_pat(s, org, user, "ci")
    await resolve(app_engine, token)
    async with tenant_session(app_engine, org) as s:
        await revoke_pat(s, org, pat_id)
    with pytest.raises(Unauthenticated):
        await resolve(app_engine, token)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic abc",
        "Bearer aop_short",
        "Bearer xxx_" + "a" * 43,
        "Bearer aop_" + "!" * 43,
        "bearer",
        "Bearer aop_" + "a" * 44,
        "Bearer  aop_" + "a" * 43,
        "Token aop_" + "a" * 43,
    ],
)
async def test_malformed_headers_are_unauthenticated_before_any_lookup(app_engine, header):
    with pytest.raises(Unauthenticated):
        await resolve_bearer(app_engine, header)


async def test_two_tokens_sharing_a_prefix_both_resolve_to_their_own_identity(
    app_engine, owner_engine
):
    # The 8-char prefix only narrows the lookup; the hash decides. A collision must not mix identities up.
    import hashlib

    a, b = await make_world(owner_engine), await make_world(owner_engine)
    shared = "ABCDEFGH"
    tokens = {w.identity: "aop_" + shared + chr(ord("a") + i) * 35 for i, w in enumerate((a, b))}
    async with owner_engine.begin() as c:
        for w in (a, b):
            await c.execute(
                text(
                    "insert into agent_tokens (organization_id, agent_identity_id, token_prefix, token_hash) values (:o, :i, :p, :h)"
                ),
                {
                    "o": w.org,
                    "i": w.identity,
                    "p": shared,
                    "h": hashlib.sha256(tokens[w.identity].encode()).hexdigest(),
                },
            )
    for w in (a, b):
        r = await resolve(app_engine, tokens[w.identity])
        # catches: the first prefix match winning
        assert (r.principal, r.organization_id) == (AgentPrincipal(w.identity, w.org), w.org)
    with pytest.raises(Unauthenticated):  # same prefix, matches neither hash
        await resolve(app_engine, "aop_" + shared + "z" * 35)


async def test_only_the_resolver_reads_the_authorization_header():
    import re
    from pathlib import Path

    root = Path(__file__).parents[2] / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "resolver.py"
        and re.search(
            r"authorization", p.read_text(), re.IGNORECASE
        )  # catches: a handler parsing identity itself
    ]
    assert offenders == []


async def test_other_tenants_credentials_are_invisible_to_a_tenant_session(
    app_engine, owner_engine
):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    await agent_token(app_engine, a)
    await agent_token(app_engine, b)
    async with tenant_session(app_engine, a.org) as s:
        n = await s.scalar(
            text("select count(*) from agent_tokens where organization_id = :b"), {"b": b.org}
        )
    assert n == 0


async def test_no_token_for_a_revoked_identity_and_revoking_stamps_the_token_row(
    app_engine, owner_engine
):
    from app.domain.errors import NotFoundError

    w = await make_world(owner_engine)
    await agent_token(app_engine, w)
    async with tenant_session(app_engine, w.org) as s:
        await revoke_agent(s, w.org, w.identity)
    stamped = await fetch(
        owner_engine,
        "select revoked_at is not null from agent_tokens where agent_identity_id=:i",
        i=w.identity,
    )
    assert stamped == [(True,)]
    with pytest.raises(NotFoundError):  # catches: minting a live token for a revoked agent
        await agent_token(app_engine, w)
    other = await make_world(owner_engine)
    with pytest.raises(NotFoundError):  # and for another tenant's identity
        async with tenant_session(app_engine, other.org) as s:
            await issue_agent_token(s, other.org, w.identity)
