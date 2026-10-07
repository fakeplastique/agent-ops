"""The one guarded write path to organizations.plan_key (S25): a SECURITY DEFINER function, no column grant."""

from alembic import op

revision = "0015"
down_revision = "0014"

ORG = "nullif(current_setting('app.organization_id', true), '')::uuid"


def upgrade() -> None:
    # app_rt still cannot UPDATE plan_key (test_grants); this function changes it for the current tenant only,
    # and the service decides who may call it (demo org admin, platform admin). Returns the old plan, NULL if
    # the organization does not exist. The tenant check trusts the GUC exactly as RLS does (only
    # app/db/session.py sets it). pg_temp goes last and every table is schema-qualified, so a caller's temp
    # table cannot shadow one and run its triggers as the owner. Hosted environments should own this
    # function by a non-superuser role.
    op.execute(
        "CREATE OR REPLACE FUNCTION set_organization_plan(org uuid, new_plan text) RETURNS text"
        " LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$"
        " DECLARE old text;"
        " BEGIN"
        f"  IF org IS DISTINCT FROM {ORG} THEN"
        "    RAISE EXCEPTION 'plan change outside the current tenant';"
        "  END IF;"
        "  SELECT plan_key INTO old FROM public.organizations WHERE id = org FOR UPDATE;"
        "  IF NOT FOUND THEN RETURN NULL; END IF;"
        "  UPDATE public.organizations SET plan_key = new_plan, updated_at = now() WHERE id = org;"
        "  RETURN old;"
        " END $$"
    )
    op.execute("REVOKE ALL ON FUNCTION set_organization_plan(uuid, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION set_organization_plan(uuid, text) TO app_rt")


def downgrade() -> None:
    op.execute("DROP FUNCTION set_organization_plan(uuid, text)")
