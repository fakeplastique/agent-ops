"""Invitations, membership writes, and the rule that an organization keeps an org_admin."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012"
down_revision = "0011"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
BY_HASH = "token_hash = nullif(current_setting('app.invite_hash', true), '')"
SELF = "id = nullif(current_setting('app.self_user_id', true), '')::uuid"
ROLES = "'member', 'team_lead', 'manager', 'org_admin'"


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("team_id", UUID),
        sa.Column(
            "token_hash", sa.Text, nullable=False, unique=True
        ),  # SHA-256 of the token, never the token
        sa.Column("invited_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["organization_id", "team_id"], ["teams.organization_id", "teams.id"]
        ),
        sa.CheckConstraint("email = lower(email)"),
        sa.CheckConstraint(f"role IN ({ROLES})"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index("invitations_org_idx", "invitations", ["organization_id", "created_at"])
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON invitations"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # The accepting user belongs to no tenant yet: a row is reachable by naming its own token hash.
    op.execute(f"CREATE POLICY by_hash_read ON invitations FOR SELECT USING ({BY_HASH})")
    op.execute(
        f"CREATE POLICY by_hash_accept ON invitations FOR UPDATE USING ({BY_HASH}) WITH CHECK ({BY_HASH})"
    )
    op.execute("GRANT SELECT ON invitations TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, email, role, team_id, token_hash, invited_by, created_at,"
        " expires_at) ON invitations TO app_rt"
    )
    op.execute("GRANT UPDATE (accepted_at, accepted_by, revoked_at) ON invitations TO app_rt")
    # Accept compares the signed-in user's email with the invited one.
    op.execute(f"CREATE POLICY self_read ON users FOR SELECT USING ({SELF})")
    # Membership writes: the service layer decides who may; RLS pins the organization.
    op.execute("GRANT INSERT (organization_id, user_id, team_id, role) ON memberships TO app_rt")
    op.execute("GRANT UPDATE (role, updated_at) ON memberships TO app_rt")
    op.execute("GRANT DELETE ON memberships TO app_rt")
    # Serialise on the organization row, then count: two admins demoting each other cannot both pass.
    op.execute(
        "CREATE FUNCTION keep_one_org_admin() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN"
        "  IF OLD.role = 'org_admin' AND (TG_OP = 'DELETE' OR NEW.role <> 'org_admin') THEN"
        "   PERFORM 1 FROM organizations WHERE id = OLD.organization_id FOR UPDATE;"
        "   IF NOT EXISTS (SELECT 1 FROM memberships WHERE organization_id = OLD.organization_id"
        "     AND role = 'org_admin' AND user_id <> OLD.user_id) THEN"
        "    RAISE EXCEPTION 'an organization keeps at least one org_admin' USING ERRCODE = '23514';"
        "   END IF;"
        "  END IF;"
        "  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;"
        " END $$"
    )
    op.execute(
        "CREATE TRIGGER keep_one_org_admin BEFORE UPDATE OR DELETE ON memberships"
        " FOR EACH ROW EXECUTE FUNCTION keep_one_org_admin()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER keep_one_org_admin ON memberships")
    op.execute("DROP FUNCTION keep_one_org_admin()")
    op.execute("REVOKE DELETE ON memberships FROM app_rt")
    op.execute("REVOKE UPDATE (role, updated_at) ON memberships FROM app_rt")
    op.execute("REVOKE INSERT (organization_id, user_id, team_id, role) ON memberships FROM app_rt")
    op.execute("DROP POLICY self_read ON users")
    op.drop_table("invitations")
