"""Full-text search (S33): generated tsvector columns on requests and messages, with GIN indexes.

Generated and stored, so a revise or a new message updates the index in the same statement and nothing can
forget to. Votes get no vector: round-1 verdicts and rationales are never searchable. Additive: old code
ignores the columns. ADD COLUMN ... STORED rewrites the table once (a brief lock; fine at this size)."""

from alembic import op

revision = "0020"
down_revision = "0019"


def upgrade() -> None:
    # 'simple': no stemming or stop words, because agents write in any language.
    op.execute(
        "ALTER TABLE decision_requests ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ("
        "to_tsvector('simple'::regconfig, title || ' ' || description)) STORED"
    )
    op.execute(
        "CREATE INDEX decision_requests_search_idx ON decision_requests USING GIN (search_vector)"
    )
    op.execute(
        "ALTER TABLE messages ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ("
        "to_tsvector('simple'::regconfig, body)) STORED"
    )
    op.execute("CREATE INDEX messages_search_idx ON messages USING GIN (search_vector)")


def downgrade() -> None:
    op.execute("DROP INDEX messages_search_idx")
    op.execute("ALTER TABLE messages DROP COLUMN search_vector")
    op.execute("DROP INDEX decision_requests_search_idx")
    op.execute("ALTER TABLE decision_requests DROP COLUMN search_vector")
