"""A signed-in user can list their own memberships and organizations before a tenant is selected (org switcher)."""

from alembic import op

revision = "0019"
down_revision = "0018"

SELF = "nullif(current_setting('app.self_user_id', true), '')::uuid"


def upgrade() -> None:
    # Read-only and keyed on the caller's own user id, which only lookup_as sets (like the invitation lookup).
    op.execute(f"CREATE POLICY self_memberships ON memberships FOR SELECT USING (user_id = {SELF})")
    op.execute(
        "CREATE POLICY self_organizations ON organizations FOR SELECT USING (EXISTS ("
        f" SELECT 1 FROM memberships m WHERE m.organization_id = organizations.id AND m.user_id = {SELF}))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY self_organizations ON organizations")
    op.execute("DROP POLICY self_memberships ON memberships")
