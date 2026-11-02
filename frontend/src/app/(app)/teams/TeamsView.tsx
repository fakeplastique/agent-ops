"use client";

import { useState, type FormEvent } from "react";

import { AdminTabs } from "@/components/admin/AdminTabs";
import { type Page, LIST, useRead } from "@/components/admin/useRead";
import {
  ActionError, Confirm, Field, MoreNote, Screen, WithSession, inputClass, primaryButton, secondaryButton, useAction,
} from "@/components/admin/ui"; // prettier-ignore
import { EmptyState, ReadView } from "@/components/states/States";
import { api } from "@/lib/api/client";

type Team = { id: string; name: string; parent_team_id: string | null; created_at: string };

const REASONS = {
  team_in_use: "The team still has sub-teams, members, invitations or a policy. Move or remove those first.",
  already_exists: "A team with this name already exists",
};

export function TeamsView() {
  return (
    <WithSession title="Teams" adminOnly>
      {() => <Teams />}
    </WithSession>
  );
}

/** Ids of a team and everything below it: a team cannot move under itself. */
function subtree(teams: Team[], id: string): Set<string> {
  const out = new Set([id]);
  for (let grew = true; grew; ) {
    grew = false;
    for (const t of teams) if (t.parent_team_id && out.has(t.parent_team_id) && !out.has(t.id)) (out.add(t.id), (grew = true));
  }
  return out;
}

function TeamForm({
  teams, team, busy, error, onSubmit, onCancel,
}: {
  teams: Team[];
  team?: Team;
  busy: boolean;
  error: unknown;
  onSubmit: (name: string, parent: string | null) => void;
  onCancel: () => void;
}) {
  const blocked = team ? subtree(teams, team.id) : new Set<string>();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const name = String(f.get("name") ?? "").trim();
    if (name) onSubmit(name, String(f.get("parent") ?? "") || null);
  };
  return (
    <form onSubmit={submit} aria-label={team ? `Edit ${team.name}` : "New team"} className="flex max-w-(--measure) flex-col gap-4 rounded-card bg-raised p-4">
      <Field label="Name" hint="Up to 80 characters.">
        {(p) => <input {...p} name="name" required maxLength={80} autoFocus defaultValue={team?.name} className={inputClass} />}
      </Field>
      <Field label="Parent team">
        {(p) => (
          <select {...p} name="parent" defaultValue={team?.parent_team_id ?? ""} className={inputClass}>
            <option value="">None (top level)</option>
            {teams.filter((t) => !blocked.has(t.id)).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      <ActionError error={error} reasons={REASONS} />
      <div className="flex gap-2">
        <button type="submit" aria-disabled={busy} className={primaryButton}>
          {busy ? "Saving…" : team ? "Save team" : "Create team"}
        </button>
        <button type="button" className={secondaryButton} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Teams() {
  const read = useRead<Page<Team>>(`/teams?${LIST}`);
  const [form, setForm] = useState<"new" | string | null>(null); // "new" or the id being edited
  const act = useAction();

  const close = () => {
    setForm(null);
    act.clear();
  };
  const save = async (name: string, parent: string | null) => {
    const r = await act.run(() =>
      form === "new"
        ? api("/teams", { method: "POST", body: { name, parent_team_id: parent } })
        : api(`/teams/${form}`, { method: "PATCH", body: { name, parent_team_id: parent } }),
    );
    if (r.ok) {
      close();
      read.refetch();
    }
  };
  const remove = async (t: Team) => {
    const r = await act.run(() => api(`/teams/${t.id}`, { method: "DELETE" }));
    if (r.ok) read.refetch();
  };

  const newTeam = (
    <button type="button" className={primaryButton} onClick={() => setForm("new")}>
      New team
    </button>
  );
  const loaded = read.status === "success";
  const empty = loaded && read.data!.items.length === 0;

  const branch = (teams: Team[], parent: string | null, ids: Set<string>) => {
    const kids = teams.filter((t) => (t.parent_team_id && ids.has(t.parent_team_id) ? t.parent_team_id : null) === parent);
    if (!kids.length) return null;
    return (
      <ul className={`m-0 list-none p-0 ${parent ? "ml-6 max-lg:ml-3" : "border-t border-rule"}`}>
        {kids.map((t) => (
          <li key={t.id} className="border-b border-rule last:border-b-0">
            {form === t.id ? (
              <div className="py-3">
                <TeamForm teams={teams} team={t} busy={act.busy} error={act.error} onSubmit={save} onCancel={close} />
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
                <p title={t.name} className="m-0 min-w-0 flex-1 basis-40 truncate font-medium">
                  {t.name}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className={secondaryButton} onClick={() => { act.clear(); setForm(t.id); }}>
                    Edit
                  </button>
                  <Confirm label="Delete" question={`Delete ${t.name}?`} confirmLabel="Delete team" busy={act.busy} onConfirm={() => remove(t)} />
                </div>
              </div>
            )}
            {branch(teams, t.id, ids)}
          </li>
        ))}
      </ul>
    );
  };

  return (
    <Screen
      title="Teams"
      lead="Teams group people for approvals: a team lead decides the steps of their team and the teams above it."
      updating={loaded && read.isFetching}
      actions={loaded && !empty && form === null ? newTeam : undefined}
    >
      <AdminTabs current="teams" />
      {form === "new" && (
        <TeamForm teams={read.data?.items ?? []} busy={act.busy} error={act.error} onSubmit={save} onCancel={close} />
      )}
      {form === null && <ActionError error={act.error} reasons={REASONS} />}
      <ReadView
        read={read}
        label="Teams"
        role="org admin"
        isEmpty={(d) => d.items.length === 0}
        empty={<EmptyState message="No teams yet. Create the first to group people for approvals." action={form === null ? newTeam : undefined} />}
      >
        {(d) => {
          const ids = new Set(d.items.map((t) => t.id));
          return (
            <div className="flex flex-col gap-2">
              {/* A parent missing from a cut-off list would hide its children: they surface at the top instead. */}
              {branch(d.items, null, ids)}
              <MoreNote shown={d.items.length} total={d.total} />
            </div>
          );
        }}
      </ReadView>
    </Screen>
  );
}
