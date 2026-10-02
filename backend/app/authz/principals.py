from dataclasses import dataclass
from uuid import UUID

from app.authz.matrix import Role


@dataclass(frozen=True)
class AgentPrincipal:
    agent_id: UUID
    organization_id: UUID


@dataclass(frozen=True)
class UserPrincipal:
    user_id: UUID
    organization_id: UUID
    role: Role


Principal = AgentPrincipal | UserPrincipal
