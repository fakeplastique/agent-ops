"use client";

import { useCallback, useState, type ReactNode } from "react";

import { type Page, LIST, useRead } from "@/components/admin/useRead";
import { confirmDiscard, useUnsavedGuard } from "@/components/admin/useUnsavedGuard";
import { List, MoreNote, Pill, Row, Screen, WithSession, primaryButton, roleLabel, secondaryButton } from "@/components/admin/ui";
import { EmptyState, ReadView } from "@/components/states/States";

import { type Plan, type Policy, type TeamOption, PolicyEditor } from "./PolicyEditor";

export function PoliciesView() {
  return <WithSession title="Policies">{(s) => <Policies canEdit={s.role === "org_admin"} />}</WithSession>;
}

const summary = (p: Policy) =>
  [
    `${p.quorum_size} ${p.quorum_size === 1 ? "reviewer" : "reviewers"}`,
    `${p.rounds} ${p.rounds === 1 ? "round" : "rounds"}`,
    p.levels.map((l) => `${roleLabel(l.role)} ${l.timeout_minutes} min`).join(" then "),
    `${p.voting_timeout_minutes} min to vote`,
  ].join(" · ");

function Policies({ canEdit }: { canEdit: boolean }) {
  const read = useRead<Page<Policy>>(`/escalation-policies?${LIST}`);
  const plan = useRead<Plan>("/organizations/current/plan");
  const teams = useRead<Page<TeamOption>>(`/teams?${LIST}`);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [dirty, setDirty] = useState(false);
  const onDirty = useCallback((d: boolean) => setDirty(d), []);
  useUnsavedGuard(dirty);

  const open = (id: string | "new") => {
    if (editing !== null && dirty && !confirmDiscard()) return;
    setEditing(id);
  };
  const teamName = (id: string | null) => (id === null ? "Organization default" : `Team ${teams.data?.items.find((t) => t.id === id)?.name ?? "—"}`);
  const planData = plan.status === "success" ? plan.data : undefined; // a failed plan read only drops the early check; the API still decides
  const teamScopes = Boolean(planData?.flags.includes("team_policies"));

  const loaded = read.status === "success";
  const empty = loaded && read.data!.items.length === 0;
  const newPolicy = (
    <button type="button" className={primaryButton} onClick={() => open("new")}>
      New policy
    </button>
  );

  const editor = (policy?: Policy): ReactNode => (
    <PolicyEditor
      key={policy ? `${policy.id}-${policy.updated_at}` : "new"}
      policy={policy}
      plan={planData}
      teams={teams.data?.items ?? []}
      teamScopes={teamScopes}
      onDirty={onDirty}
      onClose={() => setEditing(null)}
      onSaved={() => {
        setEditing(null);
        read.refetch();
      }}
    />
  );

  return (
    <Screen
      title="Policies"
      lead="Who must agree, in what order, and how long each step waits before it escalates."
      updating={loaded && read.isFetching}
      actions={canEdit && loaded && !empty && editing === null ? newPolicy : undefined}
    >
      {editing === "new" && editor()}
      <ReadView
        read={read}
        label="Policies"
        isEmpty={(d) => d.items.length === 0}
        empty={editing === "new" ? <span /> : <EmptyState message="No policy yet. Requests cannot be created until the organization has a default policy." action={canEdit ? newPolicy : undefined} />}
      >
        {(d) => (
          <div className="flex flex-col gap-2">
            <List label="Policies">
              {d.items.map((p) =>
                editing === p.id ? (
                  <li key={p.id} className="border-b border-rule py-3">
                    {editor(p)}
                  </li>
                ) : (
                  <Row
                    key={p.id}
                    actions={
                      canEdit ? (
                        <button type="button" aria-label={`Edit ${p.name}`} className={secondaryButton} onClick={() => open(p.id)}>
                          Edit
                        </button>
                      ) : undefined
                    }
                  >
                    <p className="m-0 flex items-center gap-2">
                      <span title={p.name} className="truncate font-medium">
                        {p.name}
                      </span>
                      <Pill>{teamName(p.team_id)}</Pill>
                    </p>
                    <p className="m-0 text-small text-muted wrap-anywhere">{summary(p)}</p>
                    {Object.keys(p.risk_floors).length > 0 && (
                      <p className="m-0 text-small text-muted wrap-anywhere">
                        Floors: {Object.entries(p.risk_floors).map(([a, r]) => `${a} ${r}`).join(", ")}
                      </p>
                    )}
                  </Row>
                ),
              )}
            </List>
            <MoreNote shown={d.items.length} total={d.total} />
          </div>
        )}
      </ReadView>
    </Screen>
  );
}
