"use client";

import { useState, type FormEvent } from "react";

import { type Page, LIST, useRead } from "@/components/admin/useRead";
import {
  ActionError, Confirm, Field, List, MoreNote, Notice, Pill, Row, Screen, SecretDialog, Trunc, WithSession,
  fmtDateTime, inputClass, primaryButton, secondaryButton, useAction,
} from "@/components/admin/ui"; // prettier-ignore
import { EmptyState, ReadView } from "@/components/states/States";
import { ApiError, api } from "@/lib/api/client";

type Hook = { id: string; url: string; events: string[]; active: boolean; created_at: string };
type Delivery = {
  id: string;
  event_type: string;
  status: "pending" | "delivered" | "dead";
  attempt: number;
  last_status_code: number | null;
  last_error: string | null;
  created_at: string;
};

// Mirrors services/webhooks.EVENT_TYPES: the API refuses anything else.
const EVENTS = [
  "decision.created", "decision.status_changed", "decision.revised", "vote.cast", "round.revealed",
  "step.created", "step.decided", "step.timed_out", "message.posted", "plan.changed",
]; // prettier-ignore

const REASONS = { subscription_paused: "This webhook is paused. Resume it, then redeliver." };

/** A refused URL names the rule (the API never names the address); everything else goes through the shared copy. */
function WriteProblem({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.code === "webhook_url_refused") {
    const rule = typeof error.details.rule === "string" ? error.details.rule : "it is not allowed";
    return <Notice title={`This address is refused: ${rule}`}>Use a public https address that answers within 5 seconds.</Notice>;
  }
  return <ActionError error={error} reasons={REASONS} />;
}

export function WebhooksView() {
  return (
    <WithSession title="Webhooks" adminOnly>
      {() => <Webhooks />}
    </WithSession>
  );
}

function Webhooks() {
  const read = useRead<Page<Hook>>(`/webhook-subscriptions?${LIST}`);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null); // the subscription whose deliveries show
  const [pickError, setPickError] = useState(false);
  const act = useAction();

  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const url = String(f.get("url") ?? "").trim();
    const events = EVENTS.filter((ev) => f.get(`ev:${ev}`));
    setPickError(events.length === 0);
    if (!url || events.length === 0) return;
    const r = await act.run(() => api<Hook & { secret: string }>("/webhook-subscriptions", { method: "POST", body: { url, events } }));
    if (!r.ok) return;
    setCreating(false);
    setSecret({ title: "Signing secret for the new webhook", value: r.value.secret });
    read.refetch();
  };

  const rotate = async (h: Hook) => {
    const r = await act.run(() => api<{ secret: string }>(`/webhook-subscriptions/${h.id}/rotate-secret`, { method: "POST" }));
    if (r.ok) setSecret({ title: "New signing secret", value: r.value.secret });
  };
  const toggle = async (h: Hook) => {
    const r = await act.run(() => api(`/webhook-subscriptions/${h.id}`, { method: "PATCH", body: { active: !h.active } }));
    if (r.ok) read.refetch();
  };
  const remove = async (h: Hook) => {
    const r = await act.run(() => api(`/webhook-subscriptions/${h.id}`, { method: "DELETE" }));
    if (r.ok) read.refetch();
  };

  const newHook = (
    <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
      New webhook
    </button>
  );
  const loaded = read.status === "success";
  const empty = loaded && read.data!.items.length === 0;

  return (
    <Screen
      title="Webhooks"
      lead="Receivers of signed event notifications. Each delivery is signed with the webhook's secret; the secret is shown once."
      updating={loaded && read.isFetching}
      actions={loaded && !empty && !creating ? newHook : undefined}
    >
      {creating && (
        <form onSubmit={create} noValidate aria-label="New webhook" className="flex max-w-(--measure) flex-col gap-4 rounded-card bg-raised p-4">
          <Field label="URL" hint="A public https address. Private and internal addresses are refused.">
            {(p) => <input {...p} name="url" type="url" maxLength={4096} autoFocus autoComplete="off" className={inputClass} />}
          </Field>
          <fieldset className="m-0 flex flex-col gap-1 border-0 p-0" aria-describedby={pickError ? "events-error" : undefined}>
            <legend className="mb-1 font-medium">Events</legend>
            {EVENTS.map((ev) => (
              <label key={ev} className="flex min-h-control items-center gap-2 max-lg:min-h-touch">
                <input type="checkbox" name={`ev:${ev}`} onChange={() => setPickError(false)} className="size-4 accent-accent" />
                {ev}
              </label>
            ))}
            {pickError && (
              <p id="events-error" className="m-0 text-small text-negative">
                Pick at least one event
              </p>
            )}
          </fieldset>
          <WriteProblem error={act.error} />
          <div className="flex gap-2">
            <button type="submit" aria-disabled={act.busy} className={primaryButton}>
              {act.busy ? "Creating…" : "Create webhook"}
            </button>
            <button type="button" className={secondaryButton} onClick={() => { setCreating(false); setPickError(false); act.clear(); }}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {!creating && <WriteProblem error={act.error} />}

      <ReadView
        read={read}
        label="Webhooks"
        role="org admin"
        isEmpty={(d) => d.items.length === 0}
        empty={<EmptyState message="No webhooks yet. Add one to have decisions pushed to your own systems." action={creating ? undefined : newHook} />}
      >
        {(d) => (
          <div className="flex flex-col gap-2">
            <List label="Webhooks">
              {d.items.map((h) => (
                <li key={h.id} className="border-b border-rule py-3">
                  <div className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-2 ${h.active ? "" : "text-muted"}`}>
                    <div className="min-w-0 flex-1 basis-60">
                      <div className="flex items-center gap-2">
                        <p title={h.url} className={`m-0 truncate ${h.active ? "font-medium text-text" : ""}`}>
                          {h.url}
                        </p>
                        <Pill tone={h.active ? "positive" : "warning"}>{h.active ? "Active" : "Paused"}</Pill>
                      </div>
                      <Trunc className="text-small">{h.events.join(", ")}</Trunc>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        aria-expanded={open === h.id}
                        className={secondaryButton}
                        onClick={() => setOpen(open === h.id ? null : h.id)}
                      >
                        Deliveries
                      </button>
                      <button type="button" aria-disabled={act.busy} className={secondaryButton} onClick={() => !act.busy && void toggle(h)}>
                        {h.active ? "Pause" : "Resume"}
                      </button>
                      <Confirm
                        tone="plain"
                        label="Rotate secret"
                        question="The old secret stops working."
                        confirmLabel="Rotate"
                        busy={act.busy}
                        onConfirm={() => rotate(h)}
                      />
                      <Confirm label="Delete" question="Delete this webhook?" confirmLabel="Delete webhook" busy={act.busy} onConfirm={() => remove(h)} />
                    </div>
                  </div>
                  {open === h.id && <Deliveries id={h.id} />}
                </li>
              ))}
            </List>
            <MoreNote shown={d.items.length} total={d.total} />
          </div>
        )}
      </ReadView>

      <SecretDialog
        title={secret?.title ?? ""}
        intro="Use it to check the signature on each delivery. It is not stored in readable form and cannot be shown again; if it is lost, rotate it."
        label="Signing secret"
        secret={secret?.value ?? null}
        onClose={() => setSecret(null)}
      />
    </Screen>
  );
}

const STATUS = {
  delivered: { tone: "positive", text: "Delivered" },
  pending: { tone: "warning", text: "Queued" },
  dead: { tone: "negative", text: "Failed" },
} as const;

function Deliveries({ id }: { id: string }) {
  const read = useRead<Page<Delivery>>(`/webhook-subscriptions/${id}/deliveries?${LIST}`);
  const [queued, setQueued] = useState<Record<string, Delivery>>({}); // rows the user redelivered, as the API answered
  const [failedId, setFailedId] = useState<string | null>(null);
  const act = useAction();

  const redeliver = async (d: Delivery) => {
    setFailedId(null);
    const r = await act.run(() => api<Delivery>(`/webhook-subscriptions/${id}/deliveries/${d.id}/redeliver`, { method: "POST" }));
    if (r.ok) setQueued((q) => ({ ...q, [d.id]: r.value }));
    else setFailedId(d.id);
  };

  return (
    <div className="mt-3 ml-4 flex flex-col gap-2 max-lg:ml-0">
      <ReadView
        read={read}
        label="Deliveries"
        isEmpty={(d) => d.items.length === 0}
        empty={<EmptyState message="No deliveries yet. They appear here when a selected event happens." />}
      >
        {(d) => (
          <>
            <List label="Deliveries">
              {d.items.map((row0) => {
                const x = queued[row0.id] ?? row0;
                const s = STATUS[x.status];
                return (
                  <Row
                    key={x.id}
                    actions={
                      x.status === "dead" ? (
                        <button type="button" aria-disabled={act.busy} className={secondaryButton} onClick={() => !act.busy && void redeliver(x)}>
                          Redeliver
                        </button>
                      ) : undefined
                    }
                  >
                    <div className="flex items-center gap-2">
                      <p className="m-0 truncate font-medium">{x.event_type}</p>
                      <Pill tone={s.tone}>{s.text}</Pill>
                    </div>
                    {/* A failed attempt may have no status code (timeout, refused): a dash, never a blank. */}
                    <p className="m-0 text-small text-muted wrap-anywhere tabular-nums">
                      {fmtDateTime(x.created_at)} · attempt {x.attempt} · HTTP <span>{x.last_status_code ?? "—"}</span>
                      {x.last_error ? ` · ${x.last_error}` : ""}
                    </p>
                    {failedId === x.id && <ActionError error={act.error} reasons={REASONS} />}
                  </Row>
                );
              })}
            </List>
            <MoreNote shown={d.items.length} total={d.total} />
          </>
        )}
      </ReadView>
    </div>
  );
}
