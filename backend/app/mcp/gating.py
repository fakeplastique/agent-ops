"""Plan gates on MCP tools: `@gated("search")` above a tool function checks the flag first and registers
the tool, so tests/integration/test_flag_paths.py probes it on Free (same text as the REST 402)."""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from app.api import deps
from app.db.session import tenant_session
from app.domain.errors import PlanFeatureRequired
from app.mcp.auth import current_principal
from app.services import plans


@dataclass
class ToolGate:
    flag: str
    # Arguments that pass validation and reach the gate (the registry test sends them).
    probe: dict[str, Any] = field(default_factory=dict)


REGISTRY: dict[str, ToolGate] = {}


def gated[**P, R](
    flag: str, probe: dict[str, Any] | None = None
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def wrap(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)  # the SDK reads the wrapped signature for the tool's schema
        async def tool(*args: P.args, **kwargs: P.kwargs) -> R:
            p = current_principal()
            try:
                async with tenant_session(deps.get_engine(), p.organization_id) as s:
                    await plans.require_flag(s, p.organization_id, flag)
            except PlanFeatureRequired as e:
                raise ToolError(f"plan feature required: {e}; an org admin can upgrade") from e
            return await fn(*args, **kwargs)

        tool.__gate__ = flag  # type: ignore[attr-defined]
        REGISTRY[fn.__name__] = ToolGate(flag, probe or {})
        return tool

    return wrap
