"""A least-privilege scan for the timeout worker, and stand-in steps."""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    # The worker finds due work across tenants by id only (S12; the S13 relay will get its own grant). It cannot
    # read what a request says, who decided a step, or write anything: the work itself runs through the normal
    # tenant sessions as app_rt. FORCE RLS applies to it too, so the only rows it sees are the scan policies' rows.
    op.execute("GRANT USAGE ON SCHEMA public TO app_worker")
    op.execute(
        "GRANT SELECT (id, organization_id, decision_request_id, request_version, level, status, due_at)"
        " ON approval_steps TO app_worker"
    )
    op.execute(
        "CREATE POLICY worker_scan ON approval_steps FOR SELECT TO app_worker USING (status = 'pending')"
    )
    op.execute(
        "GRANT SELECT (id, organization_id, status, version, voting_deadline_at)"
        " ON decision_requests TO app_worker"
    )
    op.execute(
        "CREATE POLICY worker_scan ON decision_requests FOR SELECT TO app_worker"
        " USING (status IN ('pending_votes', 'pending_discussion'))"
    )
    # A stand-in step names the level it replaces.
    op.execute("GRANT INSERT (stands_in_for_level) ON approval_steps TO app_rt")


def downgrade() -> None:
    op.execute("REVOKE INSERT (stands_in_for_level) ON approval_steps FROM app_rt")
    op.execute("DROP POLICY worker_scan ON decision_requests")
    op.execute(
        "REVOKE SELECT (id, organization_id, status, version, voting_deadline_at) ON decision_requests FROM app_worker"
    )
    op.execute("DROP POLICY worker_scan ON approval_steps")
    op.execute(
        "REVOKE SELECT (id, organization_id, decision_request_id, request_version, level, status, due_at)"
        " ON approval_steps FROM app_worker"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_worker")
