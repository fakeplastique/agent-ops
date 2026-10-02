"""Plans, flags, organizations (tenant root) and monthly usage counters."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"
NOW = sa.text("now()")
PRO = ("attachments", "telegram", "analytics")
ENTERPRISE = (*PRO, "search", "team_policies", "audit_export")


def _rls(table: str, column: str, grants: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table}"
        f" USING ({column} = {ORG}) WITH CHECK ({column} = {ORG})"
    )
    op.execute(f"GRANT {grants} ON {table} TO app_rt")


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("requests_per_month", sa.Integer),  # NULL = unlimited
        sa.Column("max_quorum", sa.Integer),  # NULL = any
        sa.Column("max_rounds", sa.Integer, nullable=False),
        sa.Column("max_levels", sa.Integer, nullable=False),
    )
    flags = op.create_table("feature_flags", sa.Column("key", sa.Text, primary_key=True))
    plan_features = op.create_table(
        "plan_features",
        sa.Column("plan_key", sa.Text, sa.ForeignKey("plans.key"), primary_key=True),
        sa.Column("flag_key", sa.Text, sa.ForeignKey("feature_flags.key"), primary_key=True),
    )
    op.bulk_insert(
        plans,
        [
            {
                "key": "free",
                "requests_per_month": 50,
                "max_quorum": 3,
                "max_rounds": 1,
                "max_levels": 1,
            },
            {
                "key": "pro",
                "requests_per_month": 1000,
                "max_quorum": 7,
                "max_rounds": 2,
                "max_levels": 3,
            },
            {
                "key": "enterprise",
                "requests_per_month": None,
                "max_quorum": None,
                "max_rounds": 2,
                "max_levels": 3,
            },
        ],
    )
    op.bulk_insert(flags, [{"key": f} for f in ENTERPRISE])
    op.bulk_insert(
        plan_features,
        [{"plan_key": "pro", "flag_key": f} for f in PRO]
        + [{"plan_key": "enterprise", "flag_key": f} for f in ENTERPRISE],
    )
    for table in ("plans", "feature_flags", "plan_features"):
        op.execute(f"GRANT SELECT ON {table} TO app_rt")  # reference data: read-only at runtime

    op.create_table(
        "organizations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("plan_key", sa.Text, sa.ForeignKey("plans.key"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "organization_feature_overrides",
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("flag_key", sa.Text, sa.ForeignKey("feature_flags.key"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
    )
    op.create_table(
        "usage_counters",
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("metric", sa.Text, primary_key=True),
        sa.Column("period", sa.Text, primary_key=True),  # 'YYYY-MM', UTC
        sa.Column("value", sa.Integer, nullable=False),
    )
    # The tenant root is its own tenant: a new org is inserted under its own id as the setting.
    # Grants are the second wall for what a tenant may never do to itself (S5-D2):
    # no plan_key change (S25 adds a guarded path), no override writes, counters only go up.
    _rls("organizations", "id", "SELECT, INSERT")
    op.execute("GRANT UPDATE (name, slug, updated_at) ON organizations TO app_rt")
    _rls("organization_feature_overrides", "organization_id", "SELECT")
    _rls("usage_counters", "organization_id", "SELECT, INSERT")
    op.execute("GRANT UPDATE (value) ON usage_counters TO app_rt")
    op.create_check_constraint("usage_value_nonneg", "usage_counters", "value >= 0")
    op.execute(
        "CREATE FUNCTION usage_only_up() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.value < OLD.value THEN RAISE EXCEPTION 'usage counters only go up'; END IF;"
        " RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER usage_only_up BEFORE UPDATE ON usage_counters"
        " FOR EACH ROW EXECUTE FUNCTION usage_only_up()"
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.execute("DROP FUNCTION usage_only_up()")
    for table in (
        "organization_feature_overrides",
        "organizations",
        "plan_features",
        "feature_flags",
        "plans",
    ):
        op.drop_table(table)
