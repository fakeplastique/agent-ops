"""Webhook deliveries (S28, D18): one row per (subscription, event), its attempts, status and last error.

The row id is the `webhook-id` a receiver de-duplicates on, so it stays the same across retries and redelivers.
No request or response body is ever stored."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0017"
down_revision = "0016"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "subscription_id",
            UUID,
            sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", UUID, sa.ForeignKey("outbox_events.id"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_status_code", sa.Integer),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # The consumer's idempotency key on (event, concern): a re-run job inserts nothing new.
        sa.UniqueConstraint("subscription_id", "event_id"),
        sa.CheckConstraint("status in ('pending', 'delivered', 'dead')"),
        sa.CheckConstraint("attempt between 0 and 5"),
        sa.CheckConstraint("char_length(last_error) <= 200"),  # a reason, never a body
        sa.CheckConstraint("(status = 'pending') = (next_attempt_at is not null)"),
    )
    op.create_index(
        "webhook_deliveries_sub_idx",
        "webhook_deliveries",
        ["organization_id", "subscription_id", "created_at"],
    )
    op.create_index("webhook_deliveries_event_idx", "webhook_deliveries", ["event_id"])
    op.create_index(
        "webhook_deliveries_due_idx",
        "webhook_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute("ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON webhook_deliveries"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # Column-level: a row never changes tenant, subscription, event or id (the webhook-id).
    op.execute("GRANT SELECT ON webhook_deliveries TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, subscription_id, event_id, event_type, next_attempt_at)"
        " ON webhook_deliveries TO app_rt"
    )
    op.execute(
        "GRANT UPDATE (status, attempt, next_attempt_at, last_status_code, last_error, updated_at)"
        " ON webhook_deliveries TO app_rt"
    )
    # The retry sweep (app_worker) sees ids of due rows across tenants, never a URL, secret or error.
    op.execute(
        "GRANT SELECT (id, organization_id, status, next_attempt_at) ON webhook_deliveries TO app_worker"
    )
    op.execute(
        "CREATE POLICY worker_scan ON webhook_deliveries FOR SELECT TO app_worker"
        " USING (status = 'pending')"
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
