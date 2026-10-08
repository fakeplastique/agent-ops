"""Webhook subscriptions (S27, D18): the signing secret is stored only as AES-256-GCM ciphertext."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0016"
down_revision = "0015"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", UUID, primary_key=True),  # set by the app: the ciphertext's AAD names it
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("events", ARRAY(sa.Text), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("secret_ciphertext", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("char_length(url) <= 2048"),
        sa.CheckConstraint("cardinality(events) between 1 and 50"),
        # kid.base64url(nonce || ct): never a whsec_ plaintext, whatever a future code path writes
        sa.CheckConstraint("secret_ciphertext ~ '^[A-Za-z0-9_-]{1,32}\\.[A-Za-z0-9_=-]+$'"),
    )
    op.create_index(
        "webhook_subscriptions_org_idx", "webhook_subscriptions", ["organization_id", "created_at"]
    )
    op.execute("ALTER TABLE webhook_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE webhook_subscriptions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON webhook_subscriptions"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # Column-level: a row never moves to another tenant or changes its id (the AAD).
    op.execute("GRANT SELECT, DELETE ON webhook_subscriptions TO app_rt")
    op.execute(
        "GRANT INSERT (id, organization_id, url, events, active, secret_ciphertext)"
        " ON webhook_subscriptions TO app_rt"
    )
    op.execute(
        "GRANT UPDATE (url, events, active, secret_ciphertext, updated_at)"
        " ON webhook_subscriptions TO app_rt"
    )


def downgrade() -> None:
    op.drop_table("webhook_subscriptions")
