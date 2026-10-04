"""Outbox: append-only for the runtime role, a scan-and-stamp grant for the relay."""

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    # S2 left app_rt with full CRUD on the outbox and its counter (decided here, as S2's R3-3 asked): a tenant
    # session appends events and nothing more, and the per-org counter can only step up by one.
    op.execute("REVOKE UPDATE, DELETE ON outbox_events FROM app_rt")
    op.execute("REVOKE DELETE ON org_event_counters FROM app_rt")
    op.execute(
        "CREATE FUNCTION org_event_counter_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.seq <> OLD.seq + 1 THEN RAISE EXCEPTION 'the event counter moves up by one'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER org_event_counter_guard BEFORE UPDATE ON org_event_counters"
        " FOR EACH ROW EXECUTE FUNCTION org_event_counter_guard()"
    )
    # Events are born unpublished with the counter's seq, and the counter starts at 1 and never moves without an
    # event: an INSERT cannot pre-stamp an event (the relay would never send it) or detach a seq (a hole, or a
    # later unique-key clash that blocks every emit). The last check runs at COMMIT: the service bumps the
    # counter first and inserts the event after it.
    op.execute(
        "CREATE FUNCTION outbox_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.published_at IS NOT NULL OR NEW.seq IS DISTINCT FROM"
        "  (SELECT seq FROM org_event_counters WHERE organization_id = NEW.organization_id) THEN"
        "  RAISE EXCEPTION 'an outbox event is born unpublished with the counter seq'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER outbox_insert_guard BEFORE INSERT ON outbox_events"
        " FOR EACH ROW EXECUTE FUNCTION outbox_insert_guard()"
    )
    op.execute(
        "CREATE FUNCTION org_event_counter_start() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.seq <> 1 THEN RAISE EXCEPTION 'the event counter starts at 1'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER org_event_counter_start BEFORE INSERT ON org_event_counters"
        " FOR EACH ROW EXECUTE FUNCTION org_event_counter_start()"
    )
    op.execute(
        "CREATE FUNCTION org_event_counter_has_event() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NOT EXISTS (SELECT 1 FROM outbox_events WHERE organization_id = NEW.organization_id"
        "  AND seq = NEW.seq) THEN RAISE EXCEPTION 'the event counter moved without an event'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER org_event_counter_has_event AFTER INSERT OR UPDATE ON org_event_counters"
        " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION org_event_counter_has_event()"
    )
    # An event is stamped published exactly once and never edited, for every writer including the owner.
    op.execute(
        "CREATE FUNCTION outbox_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF OLD.published_at IS NOT NULL OR NEW.published_at IS NULL OR"
        "  (NEW.id, NEW.organization_id, NEW.seq, NEW.type, NEW.payload, NEW.created_at)"
        "  IS DISTINCT FROM (OLD.id, OLD.organization_id, OLD.seq, OLD.type, OLD.payload, OLD.created_at) THEN"
        "  RAISE EXCEPTION 'an outbox event is published once and never edited'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER outbox_event_guard BEFORE UPDATE ON outbox_events"
        " FOR EACH ROW EXECUTE FUNCTION outbox_event_guard()"
    )
    # The relay (app_worker) sees ids of unpublished events across tenants, never a payload, and can only
    # stamp published_at (NULL -> a time). The jobs it enqueues carry ids; consumers read under the tenant.
    op.execute(
        "GRANT SELECT (id, organization_id, seq, type, published_at) ON outbox_events TO app_worker"
    )
    op.execute("GRANT UPDATE (published_at) ON outbox_events TO app_worker")
    # Postgres also checks an UPDATE's new row against the SELECT policies, so a scan policy of
    # `published_at IS NULL` would forbid stamping it. The column grant is what keeps the payload out of reach.
    op.execute("CREATE POLICY worker_scan ON outbox_events FOR SELECT TO app_worker USING (true)")
    op.execute(
        "CREATE POLICY worker_publish ON outbox_events FOR UPDATE TO app_worker"
        " USING (published_at IS NULL) WITH CHECK (published_at IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER org_event_counter_has_event ON org_event_counters")
    op.execute("DROP FUNCTION org_event_counter_has_event()")
    op.execute("DROP TRIGGER org_event_counter_start ON org_event_counters")
    op.execute("DROP FUNCTION org_event_counter_start()")
    op.execute("DROP TRIGGER outbox_insert_guard ON outbox_events")
    op.execute("DROP FUNCTION outbox_insert_guard()")
    op.execute("DROP POLICY worker_publish ON outbox_events")
    op.execute("DROP POLICY worker_scan ON outbox_events")
    op.execute("REVOKE UPDATE (published_at) ON outbox_events FROM app_worker")
    op.execute(
        "REVOKE SELECT (id, organization_id, seq, type, published_at) ON outbox_events FROM app_worker"
    )
    op.execute("DROP TRIGGER outbox_event_guard ON outbox_events")
    op.execute("DROP FUNCTION outbox_event_guard()")
    op.execute("DROP TRIGGER org_event_counter_guard ON org_event_counters")
    op.execute("DROP FUNCTION org_event_counter_guard()")
    op.execute("GRANT DELETE ON org_event_counters TO app_rt")
    op.execute("GRANT UPDATE, DELETE ON outbox_events TO app_rt")
