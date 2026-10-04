import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import tenant_session
from app.services.tokens import create_pat, issue_agent_token
from tests.integration.helpers import fetch, make_org, make_world, staff

pytestmark = pytest.mark.anyio


async def refused_as(engine, org, sql, **p) -> bool:
    try:
        async with tenant_session(engine, org) as s:
            await s.execute(text(sql), p)
    except DBAPIError:
        return True
    return False


async def test_the_lookup_policy_shows_only_rows_with_the_prefix_asked_for(
    app_engine, owner_engine
):
    a, b = await make_world(owner_engine), await make_world(owner_engine)
    async with tenant_session(app_engine, a.org) as s:
        ta = await issue_agent_token(s, a.org, a.identity)
    async with tenant_session(app_engine, b.org) as s:
        await issue_agent_token(s, b.org, b.identity)
    prefix_a = ta[4:12]
    async with (
        app_engine.begin() as c
    ):  # no tenant at all, only a prefix: that is what the resolver does
        none = (await c.execute(text("select count(*) from agent_tokens"))).scalar_one()
        await c.execute(text("select set_config('app.lookup_prefix', :p, true)"), {"p": prefix_a})
        rows = (
            await c.execute(text("select organization_id, agent_identity_id from agent_tokens"))
        ).all()
    assert none == 0  # catches: a token table readable without a tenant or a prefix
    assert rows == [(a.org, a.identity)]


async def test_the_lookup_never_exposes_the_identity_or_user_rows(app_engine, owner_engine):
    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        t = await issue_agent_token(s, w.org, w.identity)
    async with app_engine.begin() as c:
        await c.execute(text("select set_config('app.lookup_prefix', :p, true)"), {"p": t[4:12]})
        for table in ("agent_identities", "users", "memberships", "personal_access_tokens"):
            assert (await c.execute(text(f"select count(*) from {table}"))).scalar_one() == 0, table


async def test_runtime_role_grants_on_the_credential_tables(app_engine, owner_engine):
    org = await make_org(owner_engine)
    user = await staff(owner_engine, org, "member")
    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        await issue_agent_token(s, w.org, w.identity)
    async with tenant_session(app_engine, org) as s:
        await create_pat(s, org, user, "ci")
    # catches: a credential edited or deleted around the service (hash swapped, identity re-pointed, history erased)
    for sql in (
        "delete from agent_tokens",
        "delete from personal_access_tokens",
        "update agent_tokens set agent_identity_id = gen_random_uuid()",
        "update agent_tokens set organization_id = gen_random_uuid()",
        "update personal_access_tokens set user_id = gen_random_uuid()",
        "update personal_access_tokens set organization_id = gen_random_uuid()",
    ):
        assert await refused_as(app_engine, w.org if "agent" in sql else org, sql), sql
    assert not await refused_as(app_engine, w.org, "update agent_tokens set revoked_at = now()")


async def test_constraints_on_the_credential_tables(owner_engine):
    w = await make_world(owner_engine)
    ins = "insert into agent_tokens (organization_id, agent_identity_id, token_prefix, token_hash) values (:o, :i, :p, :h)"

    async def refused(**kw):
        try:
            async with owner_engine.begin() as c:
                await c.execute(
                    text(ins), {"o": w.org, "i": w.identity, "p": "ABCDEFGH", "h": "a" * 64} | kw
                )
        except DBAPIError:
            return True
        return False

    assert await refused(p="short")  # exactly 8 characters
    assert await refused(p="ABCDEFGH" + "x")
    assert await refused(h="not-hex")
    assert await refused(h="A" * 64)  # lowercase sha-256 hex only
    assert not await refused()
    assert await refused(h="b" * 64)  # one credential per identity
    other = await make_world(owner_engine)
    try:
        async with owner_engine.begin() as c:
            await c.execute(
                text(ins), {"o": other.org, "i": other.identity, "p": "ABCDEFGH", "h": "a" * 64}
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("two credentials share a hash")  # token_hash is unique across tenants
    assert await refused(i=other.identity)  # composite FK: that identity belongs to another org


async def test_app_rt_cannot_repoint_a_credential_to_another_valid_identity_or_user(
    app_engine, owner_engine
):
    from tests.integration.helpers import add_agent, make_member, make_user

    w = await make_world(owner_engine)
    other_identity, _ = await add_agent(owner_engine, w)
    other_user = await make_user(owner_engine)
    await make_member(owner_engine, w.org, other_user, "member")
    me = await make_user(owner_engine)
    await make_member(owner_engine, w.org, me, "member")
    async with tenant_session(app_engine, w.org) as s:
        await issue_agent_token(s, w.org, w.identity)
        await create_pat(s, w.org, me, "ci")
    # Valid targets, so only the column grants can refuse (a random id would be stopped by the foreign key).
    # catches: a credential handed to someone else around the service
    assert await refused_as(
        app_engine, w.org, "update agent_tokens set agent_identity_id=:i", i=other_identity
    )
    assert await refused_as(
        app_engine, w.org, "update personal_access_tokens set user_id=:u", u=other_user
    )


async def test_a_revoked_agent_identity_cannot_be_reactivated_by_the_runtime_role(
    app_engine, owner_engine
):
    w = await make_world(owner_engine)
    async with tenant_session(app_engine, w.org) as s:
        await s.execute(
            text("update agent_identities set status='revoked' where id=:i"), {"i": w.identity}
        )
    # catches: revocation undone around the service (it would also revive the token path)
    assert await refused_as(
        app_engine, w.org, "update agent_identities set status='active' where id=:i", i=w.identity
    )
    assert (
        await fetch(owner_engine, "select status from agent_identities where id=:i", i=w.identity)
    )[0][0] == "revoked"
