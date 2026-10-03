"""Votes, lifecycle grants on decision requests, and the status-transition guard."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
LIFECYCLE = "status, updated_at, decided_at, receipt, escalation_reason"

# Spec 4.1 as (from, to) pairs; app.domain.transitions.TABLE is the source and a test compares them.
OPEN = ("pending_votes", "pending_discussion", "pending_approval")
PAIRS = sorted(
    {
        ("pending_votes", "approved"), ("pending_votes", "rejected"),
        ("pending_votes", "pending_discussion"), ("pending_votes", "pending_approval"),
        ("pending_discussion", "approved"), ("pending_discussion", "rejected"),
        ("pending_discussion", "pending_approval"),
        ("pending_approval", "approved"), ("pending_approval", "rejected"), ("pending_approval", "expired"),
        *((s, "pending_votes") for s in OPEN if s != "pending_votes"),  # revise (same-status updates pass)
        *((s, "cancelled") for s in OPEN),  # withdraw
    }
)  # fmt: skip


def upgrade() -> None:
    # Composite FK target: a vote must point at a request of its own organization.
    op.create_unique_constraint(
        "decision_requests_org_id_key", "decision_requests", ["organization_id", "id"]
    )
    op.create_table(
        "votes",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("decision_request_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        sa.Column("request_version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # Set only when the round closes. Visibility to others hangs on this fact, not on the request's
        # status: a withdrawn or expired request that never closed its round reveals nothing.
        sa.Column("revealed_at", sa.DateTime(timezone=True)),
        # Spec 3.1 said (request, session, round); a revise returns to round 1, so the version belongs in the key.
        sa.UniqueConstraint("decision_request_id", "request_version", "session_id", "round"),
        sa.ForeignKeyConstraint(
            ["organization_id", "decision_request_id"],
            ["decision_requests.organization_id", "decision_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["agent_sessions.organization_id", "agent_sessions.id"],
        ),
        sa.CheckConstraint("round IN (1, 2)"),
        sa.CheckConstraint("verdict IN ('approve', 'reject', 'abstain')"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1"),
        sa.CheckConstraint("char_length(rationale) <= 4096"),
        sa.CheckConstraint("request_version >= 1"),
    )
    op.create_index(
        "votes_request_idx",
        "votes",
        ["organization_id", "decision_request_id", "request_version", "round"],
    )
    op.create_index("votes_session_idx", "votes", ["organization_id", "session_id"])
    op.execute("ALTER TABLE votes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE votes FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON votes USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # A vote is never edited or deleted; closing the round stamps revealed_at and nothing else.
    op.execute("GRANT SELECT, INSERT ON votes TO app_rt")
    op.execute("GRANT UPDATE (revealed_at) ON votes TO app_rt")

    op.execute(f"GRANT UPDATE ({LIFECYCLE}) ON decision_requests TO app_rt")
    # A receipt exists exactly when the request is approved (I12): no receipt on a pending row, no bare approval.
    op.create_check_constraint(
        "decision_receipt_iff_approved",
        "decision_requests",
        "(status = 'approved') = (receipt IS NOT NULL)",
    )
    values = ", ".join(f"('{a}', '{b}')" for a, b in PAIRS)
    op.execute(
        "CREATE FUNCTION decision_status_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.status <> OLD.status AND NOT EXISTS ("
        f"  SELECT 1 FROM (VALUES {values}) AS t(a, b) WHERE t.a = OLD.status AND t.b = NEW.status)"
        " THEN RAISE EXCEPTION 'illegal status transition % -> %', OLD.status, NEW.status; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER decision_status_guard BEFORE UPDATE OF status ON decision_requests"
        " FOR EACH ROW EXECUTE FUNCTION decision_status_guard()"
    )


def downgrade() -> None:
    op.drop_constraint("decision_receipt_iff_approved", "decision_requests")
    op.execute("DROP TRIGGER decision_status_guard ON decision_requests")
    op.execute("DROP FUNCTION decision_status_guard()")
    op.execute(f"REVOKE UPDATE ({LIFECYCLE}) ON decision_requests FROM app_rt")
    op.drop_table("votes")
    op.drop_constraint("decision_requests_org_id_key", "decision_requests")
