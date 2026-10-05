"""Idempotency keys: one stored answer per (org, principal, key, route)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011"
down_revision = "0010"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("principal_id", UUID, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("route", sa.Text, nullable=False),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column(
            "response_status", sa.Integer
        ),  # null until the request that claimed the key finishes
        sa.Column("response_body", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "principal_id", "key", "route"),
        sa.CheckConstraint("char_length(key) between 1 and 255"),
    )
    op.execute("ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON idempotency_keys"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_keys TO app_rt")


def downgrade() -> None:
    op.drop_table("idempotency_keys")
