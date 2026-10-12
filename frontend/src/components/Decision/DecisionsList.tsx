"use client";

import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";

import { EmptyState, ReadView } from "@/components/states/States";
import type { Decision, Page, Risk, Status } from "@/lib/api/types";
import { useAgentNames } from "@/lib/api/useAgents";
import { useRead } from "@/lib/api/useRead";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

import { RiskPill, StatusPill, statusLabel } from "./Pills";

const LIMIT = 20;
const STATUSES: Status[] = ["pending_votes", "pending_discussion", "pending_approval", "approved", "rejected", "expired", "cancelled"];
const RISKS: Risk[] = ["low", "medium", "high"];
const SORTS = [
  ["-created_at", "Newest first"],
  ["created_at", "Oldest first"],
  ["-updated_at", "Recently updated"],
  ["title", "Title A to Z"],
] as const;
const FILTERS = ["status", "risk_level", "agent_id", "q"] as const;

const control = "h-control rounded-control border border-control bg-surface px-2 text-text max-lg:h-touch";

/** Next params with `change` applied: an empty value removes the key, and any change but paging itself returns to page 1. */
function withParams(params: URLSearchParams, change: Record<string, string>) {
  const next = new URLSearchParams(params);
  for (const [k, v] of Object.entries(change)) v ? next.set(k, v) : next.delete(k);
  if (!("skip" in change)) next.delete("skip");
  return next;
}

const asInt = (v: string | null, fallback: number) => {
  const n = Number(v);
  return v !== null && Number.isInteger(n) && n >= 0 ? n : fallback;
};

/** Decisions with filters, sort and paging all in the URL (`params`); `onParams` writes the next URL. */
export function DecisionsList({ params, onParams }: { params: URLSearchParams; onParams: (p: URLSearchParams) => void }) {
  const agents = useAgentNames();
  const now = useNow();
  const skip = asInt(params.get("skip"), 0);
  const sort = params.get("sort") || "-created_at";
  const query = new URLSearchParams({ skip: String(skip), limit: String(LIMIT), sort });
  for (const f of FILTERS) if (params.get(f)) query.set(f, params.get(f)!);
  const read = useRead<Page<Decision>>(`/decision-requests?${query}`);
  const [draft, setDraft] = useState<{ from: string; text: string }>({ from: params.get("q") ?? "", text: params.get("q") ?? "" });
  // Follows the URL when it changes elsewhere (Clear filters, back button) without an effect.
  if (draft.from !== (params.get("q") ?? "")) setDraft({ from: params.get("q") ?? "", text: params.get("q") ?? "" });

  const filtered = FILTERS.some((f) => params.get(f));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onParams(withParams(params, { q: draft.text.trim() }));
  };
  const set = (key: string) => (e: { target: { value: string } }) => onParams(withParams(params, { [key]: e.target.value }));

  const total = read.data?.total ?? 0;
  const shown = read.data?.items.length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h1 className="m-0">Decisions</h1>
        {read.status === "success" && read.isFetching && (
          <span role="status" className="text-small text-muted">
            Updating
          </span>
        )}
      </div>

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3" aria-label="Filter decisions">
        <Field label="Status">
          <select value={params.get("status") ?? ""} onChange={set("status")} className={control}>
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Risk">
          <select value={params.get("risk_level") ?? ""} onChange={set("risk_level")} className={control}>
            <option value="">Any risk</option>
            {RISKS.map((r) => (
              <option key={r} value={r}>
                {r[0].toUpperCase() + r.slice(1)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Agent">
          <select value={params.get("agent_id") ?? ""} onChange={set("agent_id")} className={control}>
            <option value="">Any agent</option>
            {agents.agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Sort">
          <select value={sort} onChange={(e) => onParams(withParams(params, { sort: e.target.value === "-created_at" ? "" : e.target.value }))} className={control}>
            {SORTS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Search titles">
          <input
            type="search"
            value={draft.text}
            maxLength={200}
            onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            className={`${control} w-48`}
          />
        </Field>
        <button type="submit" className={`${control} font-medium hover:bg-raised`}>
          Search
        </button>
      </form>

      <ReadView
        read={read}
        label="Decisions"
        isEmpty={(p) => p.items.length === 0}
        empty={
          filtered ? (
            <EmptyState
              message="No decisions match"
              action={
                <button type="button" onClick={() => onParams(new URLSearchParams())} className={`${control} font-medium hover:bg-raised`}>
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState message="No decisions yet. Requests from your agents appear here as they ask." />
          )
        }
      >
        {(p) => (
          <>
            <table className="w-full border-collapse text-left max-lg:block">
              <thead className="max-lg:sr-only">
                <tr className="border-b border-border text-small text-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">Request</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Agent</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Risk</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                  <th scope="col" className="py-2 text-right font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="max-lg:block">
                {p.items.map((d) => (
                  <tr key={d.id} className="min-h-row border-b border-rule hover:bg-raised max-lg:flex max-lg:min-h-16 max-lg:flex-wrap max-lg:items-center max-lg:gap-x-2 max-lg:gap-y-1 max-lg:py-2">
                    <td className="h-row max-w-0 min-w-0 pr-3 max-lg:h-auto max-lg:w-full max-lg:max-w-none">
                      <Link href={`/decisions/${encodeURIComponent(d.id)}`} title={d.title} className="block truncate font-medium text-text no-underline hover:underline">
                        {d.title}
                      </Link>
                    </td>
                    <td className="pr-3 font-mono text-small max-lg:pr-0">{agents.name(d.requested_by_agent_id)}</td>
                    <td className="pr-3 max-lg:pr-0">
                      <RiskPill risk={d.effective_risk_level} />
                    </td>
                    <td className="pr-3 max-lg:pr-0">
                      <StatusPill status={d.status} />
                    </td>
                    <td className="text-right text-small whitespace-nowrap text-muted tabular-nums max-lg:ml-auto">{clockTime(d.created_at, now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <nav aria-label="Pages" className="mt-3 flex items-center justify-between gap-3">
              <span className="text-muted tabular-nums">
                {skip + 1}-{skip + shown} of {total}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  disabled={skip === 0}
                  aria-label="Previous page"
                  onClick={() => onParams(withParams(params, { skip: skip > LIMIT ? String(skip - LIMIT) : "" }))}
                  className={`${control} font-medium hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50`}
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={skip + shown >= total}
                  aria-label="Next page"
                  onClick={() => onParams(withParams(params, { skip: String(skip + LIMIT) }))}
                  className={`${control} font-medium hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50`}
                >
                  Next
                </button>
              </span>
            </nav>
          </>
        )}
      </ReadView>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  // Label wraps the control: one name, one hit area.
  return (
    <label className="flex flex-col gap-1 text-small font-medium">
      {label}
      {children}
    </label>
  );
}
