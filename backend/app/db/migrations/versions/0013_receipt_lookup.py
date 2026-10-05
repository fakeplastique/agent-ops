"""Public receipt verification and the audit read: a lookup policy for one approved request, an audit index."""

from alembic import op

revision = "0013"
down_revision = "0012"

BY_ID = (
    "id = nullif(current_setting('app.receipt_request_id', true), '')::uuid AND status = 'approved'"
)


def upgrade() -> None:
    # SELECT only, approved rows only: the verifier learns the tenant and then reads in that tenant.
    op.execute(f"CREATE POLICY receipt_lookup ON decision_requests FOR SELECT USING ({BY_ID})")
    # GET /audit-logs pages one organization's rows in (created_at, id) order.
    # ponytail: the per-request filter scans the org's rows; add an index on data->>'decision_request_id' if slow.
    op.create_index(
        "audit_logs_org_created_idx", "audit_logs", ["organization_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("audit_logs_org_created_idx", "audit_logs")
    op.execute("DROP POLICY receipt_lookup ON decision_requests")
