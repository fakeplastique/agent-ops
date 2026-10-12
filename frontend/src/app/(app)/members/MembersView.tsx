"use client";

import { useState, type FormEvent } from "react";

import { AdminTabs } from "@/components/admin/AdminTabs";
import { type Page, LIST, useRead } from "@/components/admin/useRead";
import {
  ActionError, Confirm, Field, List, MoreNote, Pill, ROLES, Row, Screen, SecretDialog, Section, Trunc, WithSession,
  fmtDate, inputClass, primaryButton, roleLabel, useAction,
} from "@/components/admin/ui"; // prettier-ignore
import { EmptyState, ReadView } from "@/components/states/States";
import { api } from "@/lib/api/client";

type Member = { user_id: string; email: string; full_name: string; role: string; team_id: string | null };
type Invitation = { id: string; email: string; role: string; team_id: string | null; expires_at: string };

const LAST_ADMIN = "The last org admin cannot be removed or demoted. Make someone else an org admin first.";

export function MembersView() {
  return (
    <WithSession title="Members" adminOnly>
      {(s) => <Members me={s.user_id} />}
    </WithSession>
  );
}

function Members({ me }: { me: string }) {
  const members = useRead<Page<Member>>(`/members?${LIST}`);
  const invites = useRead<Page<Invitation>>(`/invitations?${LIST}`);
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const invite = useAction();
  const act = useAction();

  // Known only when the list is complete; a cut-off list leaves it to the API (409 last_org_admin).
  const items = members.data?.items ?? [];
  const complete = members.status === "success" && items.length === members.data!.total;
  const soleAdmin = complete && items.filter((m) => m.role === "org_admin").length === 1 ? items.find((m) => m.role === "org_admin")?.user_id : null;

  const mutate = async (m: Member, call: () => Promise<unknown>) => {
    setFailedId(null);
    setBusyId(m.user_id);
    const r = await act.run(call);
    setBusyId(null);
    if (r.ok) members.refetch();
    else setFailedId(m.user_id); // the error itself renders from act.error, in this row
  };

  const send = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const email = String(f.get("email") ?? "").trim();
    if (!email) return;
    const r = await invite.run(() => api<Invitation & { token: string }>("/invitations", { method: "POST", body: { email, role: f.get("role") } }));
    if (!r.ok) return;
    form.reset();
    setLink({ email: r.value.email, url: `${window.location.origin}/invite/${r.value.token}` });
    invites.refetch();
  };

  const revoke = async (i: Invitation) => {
    const r = await act.run(() => api(`/invitations/${i.id}`, { method: "DELETE" }));
    if (r.ok) invites.refetch();
  };

  return (
    <Screen
      title="Members"
      lead="People in this organization, their roles, and the invitations still open."
      updating={(members.status === "success" && members.isFetching) || (invites.status === "success" && invites.isFetching)}
    >
      <AdminTabs current="members" />
      {failedId === null && act.error ? <ActionError error={act.error} /> : null}

      <Section title="Members">
        <ReadView read={members} label="Members" role="org admin" isEmpty={(d) => d.items.length === 0} empty={<EmptyState message="No members to show." />}>
          {(d) => (
            <div className="flex flex-col gap-2">
              <List label="Members">
                {d.items.map((m) => {
                  const last = m.user_id === soleAdmin;
                  const reasonId = `last-${m.user_id}`;
                  const rowError = failedId === m.user_id && act.error;
                  return (
                    <Row
                      key={m.user_id}
                      actions={
                        <>
                          <label className="sr-only" htmlFor={`role-${m.user_id}`}>
                            Role for {m.full_name}
                          </label>
                          {/* aria-disabled, not disabled: the reason below stays reachable and focus is not dropped. */}
                          <select
                            id={`role-${m.user_id}`}
                            value={m.role}
                            aria-disabled={last || busyId === m.user_id}
                            aria-describedby={last ? reasonId : undefined}
                            onChange={(e) => !last && busyId === null && void mutate(m, () => api(`/members/${m.user_id}`, { method: "PATCH", body: { role: e.target.value } }))}
                            className={`${inputClass} w-auto aria-disabled:opacity-60`}
                          >
                            {ROLES.map((r) => (
                              <option key={r} value={r}>
                                {roleLabel(r)}
                              </option>
                            ))}
                          </select>
                          {last ? (
                            <button type="button" aria-disabled="true" aria-describedby={reasonId} className="inline-flex h-control items-center rounded-control border border-control px-4 font-medium text-muted opacity-60 max-lg:h-touch">
                              Remove
                            </button>
                          ) : (
                            <Confirm
                              label="Remove"
                              question={`Remove ${m.full_name}?`}
                              confirmLabel="Remove member"
                              busy={busyId === m.user_id}
                              onConfirm={() => mutate(m, () => api(`/members/${m.user_id}`, { method: "DELETE" }))}
                            />
                          )}
                        </>
                      }
                    >
                      <p className="m-0 flex items-center gap-2">
                        <span title={m.full_name} className="truncate font-medium">
                          {m.full_name}
                        </span>
                        {m.user_id === me && <Pill>You</Pill>}
                      </p>
                      <Trunc className="text-small text-muted">{m.email}</Trunc>
                      {last && (
                        <p id={reasonId} className="m-0 text-small text-muted">
                          {LAST_ADMIN}
                        </p>
                      )}
                      {rowError ? <ActionError error={rowError} reasons={{ last_org_admin: LAST_ADMIN }} /> : null}
                    </Row>
                  );
                })}
              </List>
              <MoreNote shown={d.items.length} total={d.total} />
            </div>
          )}
        </ReadView>
      </Section>

      <Section title="Invitations">
        <form onSubmit={send} noValidate aria-label="Invite someone" className="flex max-w-(--measure) flex-col gap-4 rounded-card bg-raised p-4">
          <Field label="Email" hint="They sign in with Google using this address.">
            {(p) => <input {...p} name="email" type="email" required maxLength={300} autoComplete="off" className={inputClass} />}
          </Field>
          <Field label="Role">
            {(p) => (
              <select {...p} name="role" defaultValue="member" className={inputClass}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <ActionError error={invite.error} />
          <div>
            <button type="submit" aria-disabled={invite.busy} className={primaryButton}>
              {invite.busy ? "Sending…" : "Send invitation"}
            </button>
          </div>
        </form>

        <ReadView
          read={invites}
          label="Invitations"
          role="org admin"
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState message="No open invitations." />}
        >
          {(d) => (
            <div className="flex flex-col gap-2">
              <List label="Open invitations">
                {d.items.map((i) => (
                  <Row
                    key={i.id}
                    actions={
                      <Confirm
                        label="Revoke"
                        question={`Revoke the invitation for ${i.email}?`}
                        confirmLabel="Revoke invitation"
                        busy={act.busy}
                        onConfirm={() => revoke(i)}
                      />
                    }
                  >
                    <Trunc className="font-medium">{i.email}</Trunc>
                    <p className="m-0 text-small text-muted">
                      {roleLabel(i.role)} · expires {fmtDate(i.expires_at)}
                    </p>
                  </Row>
                ))}
              </List>
              <MoreNote shown={d.items.length} total={d.total} />
            </div>
          )}
        </ReadView>
      </Section>

      <SecretDialog
        title={`Invitation for ${link?.email ?? ""}`}
        intro="Send this link to them. It is shown once and works for one person: if it is lost, revoke the invitation and send a new one."
        label="Invitation link"
        secret={link?.url ?? null}
        onClose={() => setLink(null)}
      />
    </Screen>
  );
}
