from app.config import settings
from app.worker.registry import CONCERNS, job_id
from app.worker.settings import WorkerSettings


def test_the_relay_runs_every_second_and_the_timeout_sweep_every_minute():
    jobs = {j.name: j for j in WorkerSettings.cron_jobs}
    assert set(jobs) == {"cron:relay_job", "cron:timeouts_job"}
    assert jobs["cron:relay_job"].second == set(range(60))
    assert jobs["cron:timeouts_job"].second == 0 and jobs["cron:timeouts_job"].minute is None
    assert str(WorkerSettings.redis_settings.host) in settings.redis_url


def test_job_ids_are_stable_per_event_and_concern():
    assert job_id("e1", "webhooks") == "e1:webhooks" == job_id("e1", "webhooks")
    assert len({job_id("e1", c) for c in CONCERNS}) == len(CONCERNS)
