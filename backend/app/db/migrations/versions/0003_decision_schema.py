"""People, teams, agents, escalation policies and decision requests, all under RLS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def _id():
    return sa.Column("id", UUID, primary_key=True, server_default=GEN_ID)


def _org():
    return sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False)


def _stamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )


def _enum(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _rls(table: str, grants: str = "SELECT") -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table}"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # Writes are granted by the slice that owns them: no tenant can self-serve a role or a policy here.
    op.execute(f"GRANT {grants} ON {table} TO app_rt")


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("platform_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("telegram_chat_id", sa.BigInteger),
        *_stamps(),
        sa.CheckConstraint("email = lower(email)", name="users_email_lower"),
    )
    op.create_table(
        "teams",
        _id(),
        _org(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("parent_team_id", UUID),
        *_stamps(),
        sa.UniqueConstraint("organization_id", "name"),
        sa.UniqueConstraint("organization_id", "id"),
        # Composite: a parent must be in the same organization.
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_team_id"], ["teams.organization_id", "teams.id"]
        ),
    )
    op.create_index("teams_parent_idx", "teams", ["organization_id", "parent_team_id"])
    op.create_table(
        "memberships",
        _id(),
        _org(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("team_id", UUID),
        sa.Column("role", sa.Text, nullable=False),
        *_stamps(),
        sa.UniqueConstraint("organization_id", "user_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "team_id"], ["teams.organization_id", "teams.id"]
        ),
        sa.CheckConstraint(_enum("role", ("member", "team_lead", "manager", "org_admin"))),
    )
    op.create_index("memberships_user_idx", "memberships", ["user_id"])
    op.create_index("memberships_team_idx", "memberships", ["organization_id", "team_id"])
    op.create_table(
        "agent_identities",
        _id(),
        _org(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("vendor", sa.Text),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        *_stamps(),
        sa.UniqueConstraint("organization_id", "name"),
        sa.UniqueConstraint("organization_id", "id"),
        sa.CheckConstraint(_enum("status", ("active", "revoked"))),
    )
    op.create_index("agent_identities_owner_idx", "agent_identities", ["owner_user_id"])
    op.create_table(
        "agent_sessions",
        _id(),
        _org(),
        sa.Column("agent_identity_id", UUID, nullable=False),
        sa.Column("session_label", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("organization_id", "agent_identity_id", "session_label"),
        sa.UniqueConstraint("organization_id", "id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_identity_id"],
            ["agent_identities.organization_id", "agent_identities.id"],
        ),
    )
    op.create_table(
        "escalation_policies",
        _id(),
        _org(),
        sa.Column("team_id", UUID),
        sa.Column("name", sa.Text, nullable=False, server_default="default"),
        sa.Column("quorum_size", sa.Integer, nullable=False, server_default="3"),
        sa.Column("require_distinct_agents", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("rounds", sa.Integer, nullable=False, server_default="1"),
        sa.Column("confidence_threshold", sa.Float, nullable=False, server_default="0.8"),
        sa.Column("levels", JSONB, nullable=False),
        sa.Column("voting_timeout_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("risk_floors", JSONB, nullable=False, server_default=sa.text("'{}'")),
        *_stamps(),
        # NULLS NOT DISTINCT: one org default (team_id null) and one policy per team.
        sa.UniqueConstraint("organization_id", "team_id", postgresql_nulls_not_distinct=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "team_id"], ["teams.organization_id", "teams.id"]
        ),
        sa.CheckConstraint("quorum_size >= 1"),
        sa.CheckConstraint("rounds IN (1, 2)"),
        sa.CheckConstraint("confidence_threshold BETWEEN 0 AND 1"),
        sa.CheckConstraint("voting_timeout_minutes >= 1"),
        sa.CheckConstraint("jsonb_typeof(levels) = 'array' AND jsonb_array_length(levels) >= 1"),
        sa.CheckConstraint("jsonb_typeof(risk_floors) = 'object'"),
    )
    op.create_table(
        "decision_requests",
        _id(),
        _org(),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("risk_level", sa.Text, nullable=False),
        sa.Column("effective_risk_level", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending_votes"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("requested_by_session_id", UUID),
        sa.Column("requested_by_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("policy_snapshot", JSONB, nullable=False),
        sa.Column("voting_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalation_reason", sa.Text),
        sa.Column("receipt", JSONB),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["organization_id", "requested_by_session_id"],
            ["agent_sessions.organization_id", "agent_sessions.id"],
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200"),
        sa.CheckConstraint("char_length(description) <= 4096"),
        sa.CheckConstraint("octet_length(payload::text) <= 65536"),
        sa.CheckConstraint(_enum("risk_level", ("low", "medium", "high"))),
        sa.CheckConstraint(_enum("effective_risk_level", ("low", "medium", "high"))),
        sa.CheckConstraint(
            _enum(
                "status",
                (
                    "pending_votes",
                    "pending_discussion",
                    "pending_approval",
                    "approved",
                    "rejected",
                    "expired",
                    "cancelled",
                ),
            )
        ),
        sa.CheckConstraint("version >= 1"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'"),
        # Exactly one requester: an agent session or a user.
        sa.CheckConstraint(
            "(requested_by_session_id IS NOT NULL) <> (requested_by_user_id IS NOT NULL)",
            name="decision_one_requester",
        ),
    )
    op.create_index(
        "decision_requests_list_idx",
        "decision_requests",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "decision_requests_session_idx",
        "decision_requests",
        ["organization_id", "requested_by_session_id"],
    )
    op.create_index("decision_requests_user_idx", "decision_requests", ["requested_by_user_id"])

    for table in ("teams", "memberships", "agent_identities", "escalation_policies"):
        _rls(table)
    _rls("agent_sessions", "SELECT, INSERT, UPDATE")
    _rls("decision_requests", "SELECT, INSERT")
    # users are not tenant-owned: a tenant sees only its own members (S15 adds the login lookup).
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY members_only ON users FOR SELECT USING (EXISTS ("
        " SELECT 1 FROM memberships m WHERE m.user_id = users.id"
        f" AND m.organization_id = {ORG}))"
    )
    op.execute("GRANT SELECT ON users TO app_rt")


def downgrade() -> None:
    for table in (
        "decision_requests",
        "escalation_policies",
        "agent_sessions",
        "agent_identities",
        "memberships",
        "teams",
        "users",
    ):
        op.drop_table(table)
