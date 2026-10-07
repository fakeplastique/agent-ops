"""Plan gates on REST routes. A gate is a dependency that enforces one flag and registers itself:

    TEAM_GATE = gate("team_policies")
    @router.post(..., dependencies=[Depends(write_limit), Depends(TEAM_GATE)])

List it in the route's `dependencies` so it answers before role checks. tests/integration/test_flag_paths.py
walks every route carrying a gate and expects the 402 contract on Free, so a new gated route is covered
by being marked."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.api.deps import ActorDep, EngineDep
from app.db.session import tenant_session
from app.services import plans


@dataclass(eq=False)
class Gate:
    __name__ = "plan_gate"  # how app/api/errors.document_errors spots it to document the 402
    flag: str
    # Applies only when this holds for the JSON body (None = always); a body that is not an object is gated.
    when: Callable[[dict[str, Any]], bool] | None = None
    probe: dict[str, Any] | None = None  # a body the registry test sends to reach the gate

    async def __call__(self, request: Request, actor: ActorDep, engine: EngineDep) -> None:
        if self.when is not None:
            try:
                body = await request.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and not self.when(body):
                return
        async with tenant_session(engine, actor.organization_id) as s:
            await plans.require_flag(s, actor.organization_id, self.flag)


REGISTRY: list[Gate] = []


def gate(
    flag: str,
    when: Callable[[dict[str, Any]], bool] | None = None,
    probe: dict[str, Any] | None = None,
) -> Gate:
    g = Gate(flag, when, probe)
    REGISTRY.append(g)
    return g
