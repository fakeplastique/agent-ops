"""Attachments (S32): file metadata per decision request. The bytes live in FileStorage under `<org>/...`."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0019"
down_revision = "0018"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
MAX_BYTES = 10 * 1024 * 1024


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("decision_request_id", UUID, nullable=False),
        # The display name only: sanitized, never a path or a storage key.
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("content_type", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False, unique=True),
        sa.Column("uploaded_by_type", sa.Text, nullable=False),
        sa.Column("uploaded_by_id", UUID, nullable=False),  # an agent identity or a user
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "decision_request_id"],
            ["decision_requests.organization_id", "decision_requests.id"],
        ),
        sa.CheckConstraint("char_length(filename) BETWEEN 1 AND 255"),
        sa.CheckConstraint(f"size_bytes BETWEEN 1 AND {MAX_BYTES}"),
        sa.CheckConstraint("uploaded_by_type IN ('agent', 'user')"),
        # Tenant prefix: a key of another organization cannot be stored here, whatever the code does.
        sa.CheckConstraint(
            "storage_key LIKE organization_id::text || '/%'", name="key_in_org_prefix"
        ),
    )
    op.create_index(
        "attachments_request_idx",
        "attachments",
        ["organization_id", "decision_request_id", "created_at"],
    )
    op.execute("ALTER TABLE attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE attachments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON attachments USING (organization_id = {ORG})"
        f" WITH CHECK (organization_id = {ORG})"
    )
    # Rows are never edited; INSERT cannot set an id or a timestamp.
    op.execute("GRANT SELECT, DELETE ON attachments TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, decision_request_id, filename, content_type, size_bytes,"
        " storage_key, uploaded_by_type, uploaded_by_id) ON attachments TO app_rt"
    )


def downgrade() -> None:
    op.drop_table("attachments")
