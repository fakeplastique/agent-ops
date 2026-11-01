"use client";

import { useState, type FormEvent } from "react";

import { type Page, LIST, useRead } from "@/components/admin/useRead";
import {
  ActionError, Confirm, Field, List, MoreNote, Pill, Row, SecretDialog, fmtDate, inputClass, primaryButton, useAction,
} from "@/components/admin/ui"; // prettier-ignore
import { EmptyState, ReadView } from "@/components/states/States";
import { api } from "@/lib/api/client";

type Pat = { id: string; name: string; created_at: string; revoked_at: string | null };

export function TokensBlock() {
  const read = useRead<Page<Pat>>(`/me/tokens?${LIST}`);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const act = useAction();

  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return;
    const r = await act.run(() => api<Pat & { token: string }>("/me/tokens", { method: "POST", body: { name } }));
    if (!r.ok) return;
    form.reset();
    setSecret({ name: r.value.name, value: r.value.token });
    read.refetch();
  };
  const revoke = async (p: Pat) => {
    const r = await act.run(() => api(`/me/tokens/${p.id}`, { method: "DELETE" }));
    if (r.ok) read.refetch();
  };

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={create} noValidate aria-label="New personal access token" className="flex max-w-(--measure) flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <Field label="Token name" hint="Say where it will be used, for example laptop.">
            {(p) => <input {...p} name="name" maxLength={80} autoComplete="off" className={inputClass} />}
          </Field>
        </div>
        <button type="submit" aria-disabled={act.busy} className={`${primaryButton} mb-6`}>
          {act.busy ? "Creating…" : "Create token"}
        </button>
      </form>
      <ActionError error={act.error} />

      <ReadView
        read={read}
        label="Personal access tokens"
        isEmpty={(d) => d.items.length === 0}
        empty={<EmptyState message="No personal access tokens. Create one to call the API or MCP server as yourself." />}
      >
        {(d) => (
          <div className="flex flex-col gap-2">
            <List label="Personal access tokens">
              {d.items.map((p) => {
                const revoked = p.revoked_at !== null;
                return (
                  <Row
                    key={p.id}
                    muted={revoked}
                    actions={
                      revoked ? undefined : (
                        <Confirm label="Revoke" question={`Revoke ${p.name}?`} confirmLabel="Revoke token" busy={act.busy} onConfirm={() => revoke(p)} />
                      )
                    }
                  >
                    <div className="flex items-center gap-2">
                      <p title={p.name} className={`m-0 truncate ${revoked ? "" : "font-medium text-text"}`}>
                        {p.name}
                      </p>
                      {revoked && <Pill>Revoked</Pill>}
                    </div>
                    <p className="m-0 text-small">{revoked ? `Revoked ${fmtDate(p.revoked_at!)}` : `Created ${fmtDate(p.created_at)}`}</p>
                  </Row>
                );
              })}
            </List>
            <MoreNote shown={d.items.length} total={d.total} />
          </div>
        )}
      </ReadView>

      <SecretDialog
        title={`Token "${secret?.name ?? ""}"`}
        intro="Use it as a Bearer token. It is shown once and cannot be shown again; if it is lost, revoke it and create another."
        label="Token"
        secret={secret?.value ?? null}
        onClose={() => setSecret(null)}
      />
    </div>
  );
}
