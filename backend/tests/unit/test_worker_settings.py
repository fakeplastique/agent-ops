import pytest

from app.adapters.analytics_log import LogAnalytics
from app.config import settings
from app.worker.registry import CONCERNS, job_id
from app.worker.settings import WorkerSettings, shutdown, startup


def test_the_relay_runs_every_second_and_the_timeout_sweep_every_minute():
    jobs = {j.name: j for j in WorkerSettings.cron_jobs}
    assert set(jobs) == {"cron:relay_job", "cron:timeouts_job", "cron:webhook_retries_job"}
    assert jobs["cron:webhook_retries_job"].second == set(range(0, 60, 10))
    assert jobs["cron:relay_job"].second == set(range(60))
    assert jobs["cron:timeouts_job"].second == 0 and jobs["cron:timeouts_job"].minute is None
    assert str(WorkerSettings.redis_settings.host) in settings.redis_url


def test_every_relay_concern_has_its_consumer():
    # the relay enqueues one job per concern: without a function of that name it would fail unrun
    assert {"webhooks", "notifications", "analytics"} <= set(CONCERNS)
    assert {f.name for f in WorkerSettings.functions} >= {"webhooks", "notifications", "analytics"}


def test_job_ids_are_stable_per_event_and_concern():
    assert job_id("e1", "webhooks") == "e1:webhooks" == job_id("e1", "webhooks")
    assert len({job_id("e1", c) for c in CONCERNS}) == len(CONCERNS)


def test_the_analytics_consumer_is_registered_under_its_concern():
    assert "analytics" in CONCERNS
    assert "analytics" in {f.name for f in WorkerSettings.functions}


@pytest.mark.anyio
async def test_startup_wires_the_analytics_sink():
    ctx: dict = {}
    await startup(ctx)
    try:
        assert isinstance(ctx["analytics"], LogAnalytics)
    finally:
        await shutdown(ctx)
