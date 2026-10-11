"""Telegram (S31, D19): one-time link codes, the chat a user linked, and the approval messages sent to it.

Codes and button handles are stored as SHA-256 only. Before a tenant is known (a webhook update), a row is
reachable only by naming its own hash, or a user by naming their own Telegram chat."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0018"
down_revision = "0017"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
BY_CODE = "code_hash = nullif(current_setting('app.telegram_code_hash', true), '')"
BY_HANDLE = "handle_hash = nullif(current_setting('app.telegram_handle_hash', true), '')"
BY_CHAT = "telegram_chat_id = nullif(current_setting('app.telegram_chat_id', true), '')::bigint"
SELF = "id = nullif(current_setting('app.self_user_id', true), '')::uuid"
NOW = sa.text("now()")


def _tenant(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table}"
        f" USING (organization_id = {ORG}) WITH CHECK (organization_id = {ORG})"
    )


def upgrade() -> None:
    op.create_table(
        "telegram_link_codes",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # The organization it was issued in: redeeming re-checks that its plan still has `telegram`.
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("code_hash ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index("telegram_link_codes_user_idx", "telegram_link_codes", ["user_id"])
    _tenant("telegram_link_codes")
    op.execute(f"CREATE POLICY by_code_read ON telegram_link_codes FOR SELECT USING ({BY_CODE})")
    op.execute(
        f"CREATE POLICY by_code_use ON telegram_link_codes FOR UPDATE USING ({BY_CODE})"
        f" WITH CHECK ({BY_CODE})"
    )
    op.execute("GRANT SELECT ON telegram_link_codes TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, user_id, code_hash, expires_at) ON telegram_link_codes TO app_rt"
    )
    op.execute("GRANT UPDATE (used_at) ON telegram_link_codes TO app_rt")

    op.create_table(
        "telegram_messages",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("step_id", UUID, sa.ForeignKey("approval_steps.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        # SHA-256 of the random handle in the buttons' callback data; the step id never leaves.
        sa.Column("handle_hash", sa.Text, nullable=False, unique=True),
        sa.Column("message_id", sa.BigInteger),  # set once the Bot API accepted the message
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # The notification consumer's idempotency key: a re-run job for the same step sends nothing new.
        sa.UniqueConstraint("step_id", "user_id"),
        sa.CheckConstraint("handle_hash ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index("telegram_messages_user_idx", "telegram_messages", ["user_id"])
    _tenant("telegram_messages")
    op.execute(f"CREATE POLICY by_handle ON telegram_messages FOR SELECT USING ({BY_HANDLE})")
    op.execute("GRANT SELECT ON telegram_messages TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, step_id, user_id, chat_id, handle_hash)"
        " ON telegram_messages TO app_rt"
    )
    op.execute("GRANT UPDATE (message_id) ON telegram_messages TO app_rt")

    # One Telegram account belongs to at most one user; a press finds its user by naming the chat.
    op.create_index(
        "users_telegram_chat_idx", "users", ["telegram_chat_id"], unique=True,
        postgresql_where=sa.text("telegram_chat_id is not null"),
    )  # fmt: skip
    op.execute(f"CREATE POLICY telegram_lookup ON users FOR SELECT USING ({BY_CHAT})")
    op.execute(
        f"CREATE POLICY telegram_self ON users FOR UPDATE USING ({SELF}) WITH CHECK ({SELF})"
    )
    op.execute("GRANT UPDATE (telegram_chat_id) ON users TO app_rt")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (telegram_chat_id) ON users FROM app_rt")
    op.execute("DROP POLICY telegram_self ON users")
    op.execute("DROP POLICY telegram_lookup ON users")
    op.drop_index("users_telegram_chat_idx", "users")
    op.drop_table("telegram_messages")
    op.drop_table("telegram_link_codes")
