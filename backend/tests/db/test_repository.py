import uuid
from dataclasses import dataclass

import pytest

from app.db.models import AuditLog
from app.db.repositories.base import TenantRepo
from app.db.session import tenant_session
from app.domain.errors import NotFoundError

pytestmark = pytest.mark.anyio


@dataclass
class P:
    organization_id: uuid.UUID


def _body(org: uuid.UUID) -> dict:
    # What a hostile request body could carry: someone else's organization_id.
    return {
        "organization_id": org,
        "entity_type": "decision",
        "entity_id": uuid.uuid4(),
        "action": "created",
        "actor_type": "agent",
    }


async def test_repo_takes_org_from_principal_only(app_engine, org_a, org_b):
    async with tenant_session(app_engine, org_a) as s:
        row = await TenantRepo(s, P(org_a)).add(AuditLog, **_body(org_b))
        assert row.organization_id == org_a
        assert (await TenantRepo(s, P(org_a)).get(AuditLog, row.id)).id == row.id


async def test_get_of_foreign_id_is_not_found_even_without_rls(
    owner_engine, app_engine, org_a, org_b
):
    async with tenant_session(app_engine, org_a) as s:
        row = await TenantRepo(s, P(org_a)).add(AuditLog, **_body(org_a))
    # The owner session skips RLS, so only the repo's own predicate stands in the way.
    async with tenant_session(owner_engine, org_b) as s:
        with pytest.raises(NotFoundError):
            await TenantRepo(s, P(org_b)).get(AuditLog, row.id)
