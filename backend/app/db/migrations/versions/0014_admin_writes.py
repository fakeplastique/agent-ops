"""Admin writes over REST: teams, agent identities and escalation policies (grants only, additive)."""

from alembic import op

revision = "0014"
down_revision = "0013"

POLICY_FIELDS = (
    "name, quorum_size, require_distinct_agents, rounds, confidence_threshold, levels,"
    " voting_timeout_minutes, risk_floors"
)


def upgrade() -> None:
    # Column-level: nothing re-points a row to another tenant; an identity is born active (status default).
    op.execute("GRANT INSERT (organization_id, name, parent_team_id) ON teams TO app_rt")
    op.execute("GRANT UPDATE (name, parent_team_id, updated_at) ON teams TO app_rt")
    op.execute("GRANT DELETE ON teams TO app_rt")
    op.execute(
        "GRANT INSERT (organization_id, name, description, vendor, owner_user_id)"
        " ON agent_identities TO app_rt"
    )
    # A policy's team is fixed at create: moving it would dodge the team_policies flag check.
    op.execute(
        f"GRANT INSERT (organization_id, team_id, {POLICY_FIELDS}) ON escalation_policies TO app_rt"
    )
    op.execute(f"GRANT UPDATE ({POLICY_FIELDS}, updated_at) ON escalation_policies TO app_rt")
    op.execute("GRANT DELETE ON escalation_policies TO app_rt")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON escalation_policies FROM app_rt")
    op.execute(f"REVOKE UPDATE ({POLICY_FIELDS}, updated_at) ON escalation_policies FROM app_rt")
    op.execute(
        f"REVOKE INSERT (organization_id, team_id, {POLICY_FIELDS}) ON escalation_policies FROM app_rt"
    )
    op.execute(
        "REVOKE INSERT (organization_id, name, description, vendor, owner_user_id)"
        " ON agent_identities FROM app_rt"
    )
    op.execute("REVOKE DELETE ON teams FROM app_rt")
    op.execute("REVOKE UPDATE (name, parent_team_id, updated_at) ON teams FROM app_rt")
    op.execute("REVOKE INSERT (organization_id, name, parent_team_id) ON teams FROM app_rt")
