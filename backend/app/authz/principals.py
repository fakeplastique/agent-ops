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

    def __post_init__(self) -> None:
        # A raw int from a DB column must become a Role (or raise), never skip an `is` check.
        object.__setattr__(self, "role", Role(self.role))


Principal = AgentPrincipal | UserPrincipal
