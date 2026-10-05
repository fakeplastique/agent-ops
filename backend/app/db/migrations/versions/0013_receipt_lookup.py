"""Public receipt verification: one approved request is reachable by naming its id, before any tenant."""

from alembic import op

revision = "0013"
down_revision = "0012"

BY_ID = (
    "id = nullif(current_setting('app.receipt_request_id', true), '')::uuid AND status = 'approved'"
)


def upgrade() -> None:
    # SELECT only, approved rows only: the verifier learns the tenant and then reads in that tenant.
    op.execute(f"CREATE POLICY receipt_lookup ON decision_requests FOR SELECT USING ({BY_ID})")


def downgrade() -> None:
    op.execute("DROP POLICY receipt_lookup ON decision_requests")
