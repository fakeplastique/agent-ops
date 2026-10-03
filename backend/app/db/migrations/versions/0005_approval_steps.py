"""Approval steps: one pending step at a time, one decision per user and version."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "approval_steps",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("decision_request_id", UUID, nullable=False),
        sa.Column("request_version", sa.Integer, nullable=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("required_role", sa.Text, nullable=False),
        sa.Column("approver_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("comment", sa.Text),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("stands_in_for_level", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["organization_id", "decision_request_id"],
            ["decision_requests.organization_id", "decision_requests.id"],
        ),
        sa.UniqueConstraint("decision_request_id", "request_version", "level"),
        sa.CheckConstraint("level >= 1"),
        sa.CheckConstraint("request_version >= 1"),
        sa.CheckConstraint("required_role IN ('team_lead', 'manager', 'org_admin')"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'timed_out', 'superseded')"
        ),
        sa.CheckConstraint("char_length(comment) <= 4096"),
        sa.CheckConstraint("stands_in_for_level IS NULL OR stands_in_for_level < level"),
        # A decision names its decider and its time; nothing else does.
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected')) = (approver_user_id IS NOT NULL)",
            name="step_decider_iff_decided",
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected')) = (decided_at IS NOT NULL)",
            name="step_decided_at_iff_decided",
        ),
    )
    # One pending step per request version (spec: one pending at a time, in level order).
    op.create_index(
        "approval_steps_one_pending", "approval_steps", ["decision_request_id", "request_version"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )  # fmt: skip
    # D15: a user decides at most one step per request version, backed by the database.
    op.create_index(
        "approval_steps_one_decision_per_user", "approval_steps",
        ["decision_request_id", "request_version", "approver_user_id"],
        unique=True, postgresql_where=sa.text("status IN ('approved', 'rejected')"),
    )  # fmt: skip
    op.create_index(
        "approval_steps_request_idx",
        "approval_steps",
        ["organization_id", "decision_request_id", "request_version"],
    )
    op.create_index("approval_steps_approver_idx", "approval_steps", ["approver_user_id"])
    op.create_index(  # the timeout job (S12) scans overdue pending steps
        "approval_steps_due_idx",
        "approval_steps",
        ["due_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute("ALTER TABLE approval_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_steps FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON approval_steps USING (organization_id = {ORG})"
        f" WITH CHECK (organization_id = {ORG})"
    )
    op.execute("GRANT SELECT ON approval_steps TO app_rt")
    # A step is born pending: INSERT cannot name a status, decider, comment or decision time, so a decided
    # step cannot be forged (the guard trigger below only sees UPDATEs).
    op.execute(
        "GRANT INSERT (organization_id, decision_request_id, request_version, level, required_role, due_at)"
        " ON approval_steps TO app_rt"
    )
    op.execute(
        "GRANT UPDATE (status, approver_user_id, comment, decided_at) ON approval_steps TO app_rt"
    )
    # A step is decided once: pending -> a final status, never edited afterwards (the audit trail relies on it).
    op.execute(
        "CREATE FUNCTION approval_step_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF OLD.status <> 'pending' OR NEW.status = 'pending' THEN"
        "  RAISE EXCEPTION 'approval steps are decided once and then immutable'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER approval_step_guard BEFORE UPDATE ON approval_steps"
        " FOR EACH ROW EXECUTE FUNCTION approval_step_guard()"
    )


def downgrade() -> None:
    op.drop_table("approval_steps")
    op.execute("DROP FUNCTION approval_step_guard()")
