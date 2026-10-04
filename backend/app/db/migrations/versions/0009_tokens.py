"""Agent tokens and personal access tokens: hashed, prefix-looked-up, tenant-bound credentials."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0009"
down_revision = "0008"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
LOOKUP = "nullif(current_setting('app.lookup_prefix', true), '')"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def _common():
    return (
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        # 8 characters of the random body narrow the lookup; the SHA-256 of the whole token decides.
        sa.Column("token_prefix", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("char_length(token_prefix) = 8"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'"),
    )


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # The resolver does not know the tenant yet: it may read the rows whose prefix it names, nothing else.
    # What a known prefix reveals is that one credential row: tenant, subject id, (PAT) label, hash, revoked_at.
    # Never an identity, user or membership row, and the hash is SHA-256 of 256 random bits (not invertible).
    op.execute(
        f"CREATE POLICY lookup_by_prefix ON {table} FOR SELECT USING (token_prefix = {LOOKUP})"
    )


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        *_common(),
        sa.Column("agent_identity_id", UUID, nullable=False),
        sa.UniqueConstraint(
            "agent_identity_id"
        ),  # one credential per agent identity: rotating replaces it
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_identity_id"],
            ["agent_identities.organization_id", "agent_identities.id"],
        ),
    )
    op.create_index("agent_tokens_prefix_idx", "agent_tokens", ["token_prefix"])
    op.create_index(
        "agent_tokens_org_idx", "agent_tokens", ["organization_id", "agent_identity_id"]
    )
    op.create_table(
        "personal_access_tokens",
        *_common(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 80"),
        # A PAT exists only for a member, and dies with the membership: removing a user kills their tokens for
        # good, and adding them back does not bring an old token to life.
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["memberships.organization_id", "memberships.user_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("pat_prefix_idx", "personal_access_tokens", ["token_prefix"])
    op.create_index("pat_user_idx", "personal_access_tokens", ["user_id"])
    op.create_index("pat_org_idx", "personal_access_tokens", ["organization_id", "user_id"])
    _rls("agent_tokens")
    _rls("personal_access_tokens")
    # Mint and rotate write the token columns; revoke stamps revoked_at. Nothing re-points a credential
    # (identity, user, tenant) and nothing deletes one.
    op.execute("GRANT SELECT ON agent_tokens, personal_access_tokens TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, agent_identity_id, token_prefix, token_hash) ON agent_tokens TO app_rt"
    )
    op.execute("GRANT UPDATE (token_prefix, token_hash, revoked_at) ON agent_tokens TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, user_id, name, token_prefix, token_hash) ON personal_access_tokens TO app_rt"
    )
    op.execute("GRANT UPDATE (revoked_at) ON personal_access_tokens TO app_rt")
    # Revoking an agent identity: its status only, and revocation is final (a new identity is a new agent).
    op.execute("GRANT UPDATE (status, updated_at) ON agent_identities TO app_rt")
    op.execute(
        "CREATE FUNCTION agent_identity_revocation_is_final() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF OLD.status = 'revoked' AND NEW.status <> 'revoked' THEN"
        "  RAISE EXCEPTION 'a revoked agent identity stays revoked'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER agent_identity_revocation_is_final BEFORE UPDATE ON agent_identities"
        " FOR EACH ROW EXECUTE FUNCTION agent_identity_revocation_is_final()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER agent_identity_revocation_is_final ON agent_identities")
    op.execute("DROP FUNCTION agent_identity_revocation_is_final()")
    op.execute("REVOKE UPDATE (status, updated_at) ON agent_identities FROM app_rt")
    op.drop_table("personal_access_tokens")
    op.drop_table("agent_tokens")
