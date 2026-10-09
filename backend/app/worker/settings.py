from typing import Any

from arq import cron, func
from arq.connections import RedisSettings

from app.adapters.analytics_log import LogAnalytics
from app.adapters.clock import SystemClock
from app.config import settings
from app.db.engine import make_engine
from app.worker.analytics import analytics_job
from app.worker.cron import timeouts_job
from app.worker.relay import relay_once


async def startup(ctx: dict[str, Any]) -> None:
    # Two identities (S12-D1): the scan sees ids of due work only, the work runs as the tenant-scoped runtime role.
    ctx["scan_engine"] = make_engine(settings.worker_database_url)
    ctx["work_engine"] = make_engine(settings.database_url)
    ctx["clock"] = SystemClock()
    ctx["analytics"] = LogAnalytics()


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["scan_engine"].dispose()
    await ctx["work_engine"].dispose()


async def relay_job(ctx: dict[str, Any]) -> dict[str, int]:
    return await relay_once(ctx["scan_engine"], ctx["redis"], ctx["clock"])


class WorkerSettings:
    """`arq app.worker.settings.WorkerSettings`. Each consumer registers its concern name (S28 `webhooks`,
    S31 `notifications`, S34 `analytics`): do not run this worker before all three exist, or the jobs the
    relay enqueues would have no function to run."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    functions: list[Any] = [func(analytics_job, name="analytics")]  # noqa: RUF012
    cron_jobs = [  # noqa: RUF012
        cron(relay_job, second=set(range(60))),  # every second: the relay polls the outbox (D3)
        cron(timeouts_job),  # every minute, at second 0 (S12)
    ]
