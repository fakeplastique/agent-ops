import uuid
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError


class HasOrganization(Protocol):
    @property
    def organization_id(self) -> uuid.UUID: ...


class TenantRepo:
    """The organization comes from the principal, never from input; RLS is the second wall."""

    def __init__(self, session: AsyncSession, principal: HasOrganization) -> None:
        self.session = session
        self.org_id = principal.organization_id

    async def add[T](self, model: type[T], **fields: Any) -> T:
        row = model(**{**fields, "organization_id": self.org_id})
        self.session.add(row)
        await self.session.flush()
        return row

    async def get[T](self, model: type[T], id: uuid.UUID) -> T:
        stmt = select(model).where(model.id == id, model.organization_id == self.org_id)  # pyright: ignore[reportAttributeAccessIssue]
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError
        return row
