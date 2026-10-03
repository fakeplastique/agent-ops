"""Messages (the discussion thread), revise grants and the content freeze of decided requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006"
down_revision = "0005"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
GEN_ID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
CONTENT = "title, description, action_type, payload, risk_level, effective_risk_level, content_hash, version"
REVISE = f"{CONTENT}, policy_snapshot, voting_deadline_at"


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", UUID, primary_key=True, server_default=GEN_ID),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("decision_request_id", UUID, nullable=False),
        sa.Column("request_version", sa.Integer, nullable=False),
        sa.Column("author_type", sa.Text, nullable=False),
        sa.Column("author_id", UUID),  # an agent identity or a user; null for the system
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["organization_id", "decision_request_id"],
            ["decision_requests.organization_id", "decision_requests.id"],
        ),
        sa.CheckConstraint("author_type IN ('agent', 'user', 'system')"),
        sa.CheckConstraint(
            "(author_type = 'system') = (author_id IS NULL)", name="message_author_iff_not_system"
        ),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 4096"),
        sa.CheckConstraint("request_version >= 1"),
    )
    op.create_index(
        "messages_thread_idx", "messages", ["organization_id", "decision_request_id", "created_at"]
    )
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON messages USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )
    # A message is never edited or deleted; INSERT cannot set an id or a timestamp.
    op.execute("GRANT SELECT ON messages TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, decision_request_id, request_version, author_type, author_id, body)"
        " ON messages TO app_rt"
    )

    # Revise rewrites the content and bumps the version; the timer is also reset on entering discussion.
    op.execute(f"GRANT UPDATE ({REVISE}) ON decision_requests TO app_rt")

    # A decided request keeps its content and version forever, so a receipt can never outlive a change (I4, I12);
    # the version only ever moves up by one.
    def row(prefix: str, cols: str) -> str:
        return "(" + ", ".join(f"{prefix}.{c.strip()}" for c in cols.split(",")) + ")"

    bound = "title, action_type, payload, risk_level"  # what content_hash covers
    shown = f"{bound}, description, effective_risk_level"
    op.execute(
        "CREATE FUNCTION decision_content_guard() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN"
        "  IF NEW.version <> OLD.version AND NEW.version <> OLD.version + 1 THEN"
        "   RAISE EXCEPTION 'version moves up by one'; END IF;"
        f"  IF OLD.status IN ('approved', 'rejected', 'expired', 'cancelled') AND {row('NEW', CONTENT)}"
        f"   IS DISTINCT FROM {row('OLD', CONTENT)} THEN"
        "   RAISE EXCEPTION 'a decided request keeps its content and version'; END IF;"
        # An open request changes content only through a revise: version + 1, and a new hash whenever a
        # field the hash covers changed (and a new hash only then), so content and hash cannot drift apart.
        f"  IF ({row('NEW', shown)} IS DISTINCT FROM {row('OLD', shown)} OR NEW.policy_snapshot IS DISTINCT FROM OLD.policy_snapshot)"
        "   AND NEW.version <> OLD.version + 1 THEN"
        "   RAISE EXCEPTION 'content changes only with a version bump'; END IF;"
        f"  IF ({row('NEW', bound)} IS DISTINCT FROM {row('OLD', bound)}) <> (NEW.content_hash <> OLD.content_hash) THEN"
        "   RAISE EXCEPTION 'content_hash must change exactly when hashed content changes'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER decision_content_guard BEFORE UPDATE ON decision_requests"
        " FOR EACH ROW EXECUTE FUNCTION decision_content_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER decision_content_guard ON decision_requests")
    op.execute("DROP FUNCTION decision_content_guard()")
    op.execute(f"REVOKE UPDATE ({REVISE}) ON decision_requests FROM app_rt")
    op.drop_table("messages")
