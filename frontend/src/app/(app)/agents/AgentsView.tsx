"use client";

import { useState, type FormEvent } from "react";

import { type Page, LIST, useRead } from "@/components/admin/useRead";
import {
  ActionError, Confirm, Field, List, MoreNote, Pill, Row, Screen, SecretDialog, Trunc, WithSession,
  fmtDate, inputClass, primaryButton, secondaryButton, useAction,
} from "@/components/admin/ui"; // prettier-ignore
import { EmptyState, ReadView } from "@/components/states/States";
import { api } from "@/lib/api/client";

type Agent = {
  id: string;
  name: string;
  description: string;
  vendor: string | null;
  owner_user_id: string;
  status: string;
  created_at: string;
  updated_at: string;
};
type Member = { user_id: string; full_name: string };

export function AgentsView() {
  return <WithSession title="Agents">{(s) => <Agents canEdit={s.role === "org_admin"} />}</WithSession>;
}

function Agents({ canEdit }: { canEdit: boolean }) {
  const read = useRead<Page<Agent>>(`/agents?${LIST}`);
  const members = useRead<Page<Member>>(`/members?${LIST}`);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const act = useAction();

  // Owner names are a nicety: a failed members read shows a dash, never blocks the list.
  const owners = new Map(members.status === "success" ? members.data?.items.map((m) => [m.user_id, m.full_name]) : []);

  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const name = String(f.get("name") ?? "").trim();
    if (!name) return;
    const body = { name, vendor: String(f.get("vendor") ?? "").trim() || null, description: String(f.get("description") ?? "").trim() };
    const r = await act.run(() => api<Agent & { token: string }>("/agents", { method: "POST", body }));
    if (!r.ok) return;
    setCreating(false);
    setSecret({ title: `Token for ${r.value.name}`, value: r.value.token });
    read.refetch();
  };

  const rotate = async (a: Agent) => {
    const r = await act.run(() => api<{ token: string }>(`/agents/${a.id}/rotate`, { method: "POST" }));
    if (r.ok) setSecret({ title: `Token for ${a.name}`, value: r.value.token });
  };

  const revoke = async (a: Agent) => {
    const r = await act.run(() => api(`/agents/${a.id}`, { method: "DELETE" }));
    if (r.ok) read.refetch();
  };

  const newAgent = (
    <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
      New agent
    </button>
  );
  const loaded = read.status === "success";
  const empty = loaded && read.data!.items.length === 0;

  return (
    <Screen
      title="Agents"
      lead="The identities software uses to ask for sign-off. A token is shown once, when it is created or replaced."
      updating={loaded && read.isFetching}
      actions={canEdit && loaded && !empty && !creating ? newAgent : undefined}
    >
      {creating && (
        <form onSubmit={create} className="flex max-w-(--measure) flex-col gap-4 rounded-card bg-raised p-4" aria-label="New agent">
          <Field label="Name" hint="Up to 80 characters. Must be unique in the organization.">
            {(p) => <input {...p} name="name" required maxLength={80} autoFocus className={inputClass} />}
          </Field>
          <Field label="Vendor" hint="Optional: who built it.">
            {(p) => <input {...p} name="vendor" maxLength={80} className={inputClass} />}
          </Field>
          <Field label="Description" hint="Optional: what it does.">
            {(p) => <input {...p} name="description" maxLength={1000} className={inputClass} />}
          </Field>
          <ActionError error={act.error} reasons={{ already_exists: "An agent with this name already exists" }} />
          <div className="flex gap-2">
            <button type="submit" aria-disabled={act.busy} className={primaryButton}>
              {act.busy ? "Creating…" : "Create agent"}
            </button>
            <button type="button" className={secondaryButton} onClick={() => { setCreating(false); act.clear(); }}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {!creating && <ActionError error={act.error} />}

      <ReadView
        read={read}
        label="Agents"
        role="org admin"
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="No agents yet. An agent is the identity software uses to ask for sign-off."
            action={canEdit && !creating ? newAgent : undefined}
          />
        }
      >
        {(d) => (
          <div className="flex flex-col gap-2">
            <List label="Agents">
              {d.items.map((a) => {
                const revoked = a.status === "revoked";
                return (
                  <Row
                    key={a.id}
                    muted={revoked}
                    actions={
                      canEdit && !revoked ? (
                        <>
                          <Confirm
                            tone="plain"
                            label="Rotate token"
                            question="The old token stops working."
                            confirmLabel="Rotate"
                            busy={act.busy}
                            onConfirm={() => rotate(a)}
                          />
                          <Confirm
                            label="Revoke"
                            question={`Revoke ${a.name} for good?`}
                            confirmLabel="Revoke agent"
                            busy={act.busy}
                            onConfirm={() => revoke(a)}
                          />
                        </>
                      ) : undefined
                    }
                  >
                    <div className="flex items-center gap-2">
                      <p title={a.name} className={`m-0 truncate ${revoked ? "" : "font-medium text-text"}`}>
                        {a.name}
                      </p>
                      <Pill tone={revoked ? "neutral" : "positive"}>{revoked ? "Revoked" : "Active"}</Pill>
                    </div>
                    <Trunc className="text-small">{[a.vendor, a.description].filter(Boolean).join(" · ") || "No description"}</Trunc>
                    <p className="m-0 text-small">
                      Owner {owners.get(a.owner_user_id) ?? "—"} ·{" "}
                      {revoked ? `Revoked ${fmtDate(a.updated_at)}` : `Created ${fmtDate(a.created_at)}`}
                    </p>
                  </Row>
                );
              })}
            </List>
            <MoreNote shown={d.items.length} total={d.total} />
          </div>
        )}
      </ReadView>

      <SecretDialog
        title={secret?.title ?? ""}
        intro="Give this token to the agent now. It is not stored and cannot be shown again; if it is lost, rotate it."
        label="Token"
        secret={secret?.value ?? null}
        onClose={() => setSecret(null)}
      />
    </Screen>
  );
}
