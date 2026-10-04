from typing import Any

from app.services.escalation import run_timeouts


async def timeouts_job(ctx: dict[str, Any]) -> dict[str, int]:
    """Every minute. The S13 worker settings register it (arq cron) and put the two engines and the clock in ctx:
    `scan_engine` as app_worker (ids of due work), `work_engine` as app_rt (the work itself)."""
    return await run_timeouts(ctx["scan_engine"], ctx["work_engine"], ctx["clock"])
