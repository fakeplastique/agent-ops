"""Server-side web sessions, and the login lookup and create for users."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"

BY_HASH = "id_hash = nullif(current_setting('app.session_hash', true), '')"
BY_EMAIL = "email = nullif(current_setting('app.login_email', true), '')"


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "id_hash", sa.Text, nullable=False, unique=True
        ),  # SHA-256 of the cookie value, never the value
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("selected_organization_id", UUID, sa.ForeignKey("organizations.id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),  # the absolute cap (30 days)
        sa.CheckConstraint("id_hash ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("expires_at > created_at"),
    )
    op.create_index("web_sessions_user_idx", "web_sessions", ["user_id"])
    op.create_index("web_sessions_org_idx", "web_sessions", ["selected_organization_id"])
    op.execute("ALTER TABLE web_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_sessions FORCE ROW LEVEL SECURITY")
    # No tenant exists before a session is resolved: a row is reachable only by naming its own hash, which is
    # the SHA-256 of a 256-bit value the cookie holds (the same trick as the credential lookup, S14-D1).
    op.execute(f"CREATE POLICY by_hash ON web_sessions USING ({BY_HASH}) WITH CHECK ({BY_HASH})")
    op.execute("GRANT SELECT, DELETE ON web_sessions TO app_rt")
    op.execute(
        "GRANT INSERT (id_hash, user_id, selected_organization_id, created_at, last_seen_at, expires_at)"
        " ON web_sessions TO app_rt"
    )
    op.execute("GRANT UPDATE (last_seen_at, selected_organization_id) ON web_sessions TO app_rt")
    # Login finds or creates the user by the one email it names; users are otherwise read through memberships.
    # Only email and name can be written: never platform_admin, never an update, never a delete.
    op.execute(f"CREATE POLICY login_lookup ON users FOR SELECT USING ({BY_EMAIL})")
    op.execute(f"CREATE POLICY login_create ON users FOR INSERT WITH CHECK ({BY_EMAIL})")
    op.execute("GRANT INSERT (email, full_name) ON users TO app_rt")


def downgrade() -> None:
    op.execute("REVOKE INSERT (email, full_name) ON users FROM app_rt")
    op.execute("DROP POLICY login_create ON users")
    op.execute("DROP POLICY login_lookup ON users")
    op.drop_table("web_sessions")
