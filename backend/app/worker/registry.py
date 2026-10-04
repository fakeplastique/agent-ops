# One arq job per concern per event, so a slow consumer never holds up the others (spec D3). Each concern is the
# arq function name the consumer slice registers (S28 webhooks, S31 notifications, S34 analytics); every event
# type goes to every concern and the consumer decides what it cares about. Jobs carry (organization_id, event_id)
# only: the consumer reads the event inside that tenant's own session.
#
# CONTRACT for every consumer (webhooks S28, notifications S31, analytics S34): be idempotent on
# (event_id, concern). The stable job id stops duplicates only while arq still holds the job or its result
# (keep_result, 1 hour by default): a relay that dies after enqueueing re-sends the batch, and a re-send that
# arrives after the result expired runs the job again. Record "event X done for concern Y" in the same
# transaction as the effect (a delivery row keyed by event id), and skip when it exists.
CONCERNS: tuple[str, ...] = ("notifications", "webhooks", "analytics")


def job_id(event_id: object, concern: str) -> str:
    """Stable per (event, concern): arq refuses a second job with the same id, so a relay that died after
    enqueuing and re-runs the batch cannot create duplicates."""
    return f"{event_id}:{concern}"
