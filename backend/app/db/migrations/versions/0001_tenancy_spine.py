"""Tenancy spine: audit log, outbox, per-org event counters, all under RLS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None

# Unset or empty setting -> NULL -> no row matches (fails closed instead of erroring on '').
ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def _tenant_rls(table: str, grants: str = "SELECT, INSERT, UPDATE, DELETE") -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table}"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    op.execute(f"GRANT {grants} ON {table} TO app_rt")


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO app_rt")
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("actor_type", sa.Text, nullable=False),
        sa.Column("actor_id", UUID),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "org_event_counters",
        sa.Column("organization_id", UUID, primary_key=True),
        sa.Column("seq", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "seq"),
    )
    _tenant_rls("audit_logs", "SELECT, INSERT")
    _tenant_rls("org_event_counters")
    _tenant_rls("outbox_events")
    # Grants stop the app role; the trigger also stops the owner and superusers.
    op.execute(
        "CREATE FUNCTION audit_logs_append_only() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END $$"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs"
        " FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )


def downgrade() -> None:
    for table in ("outbox_events", "org_event_counters", "audit_logs"):
        op.drop_table(table)
    op.execute("DROP FUNCTION audit_logs_append_only()")
