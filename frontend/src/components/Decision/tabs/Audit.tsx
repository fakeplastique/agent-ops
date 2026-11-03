"use client";

import { useState } from "react";

import { ReadView } from "@/components/states/States";
import { isBlind } from "@/components/Decision/model";
import { roleLabel, type Decision, type Page } from "@/lib/api/types";
import { useAgentNames } from "@/lib/api/useAgents";
import { useRead } from "@/lib/api/useRead";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

type Entry = { id: string; entity_type: string; action: string; actor_type: string; actor_id: string | null; data: Record<string, unknown>; created_at: string };

const LIMIT = 50;
const VERDICTS = new Set(["approve", "reject", "abstain"]);
const DECISIONS = { approve: "approved", reject: "rejected" } as const;
const num = (v: unknown) => (typeof v === "number" ? v : null);

/** One plain sentence per entry, built from known fields only. A blind round's vote never names its verdict. */
function sentence(e: Entry, blind: boolean): string {
  const d = e.data;
  const key = `${e.entity_type}.${e.action}`;
  if (key === "vote.cast") return !blind && typeof d.verdict === "string" && VERDICTS.has(d.verdict) ? `Vote cast: ${d.verdict}` : "Vote cast";
  if (key === "decision_request.created") return "Request created";
  if (key === "decision_request.revised") return "Request revised";
  if (key === "decision_request.escalated") return "Escalated to the human levels";
  if (key === "decision_request.status_changed") return "Status changed";
  if (key === "message.posted") return "Message posted";
  const level = num(d.level);
  if (key === "approval_step.created") return `Level ${level ?? "?"} step opened${typeof d.required_role === "string" ? ` for ${roleLabel(d.required_role).toLowerCase()}` : ""}`;
  if (key === "approval_step.decided") return `Level ${level ?? "?"} step ${typeof d.decision === "string" && d.decision in DECISIONS ? DECISIONS[d.decision as keyof typeof DECISIONS] : "decided"}`;
  if (key === "approval_step.timed_out") return `Level ${level ?? "?"} step timed out`;
  return `${e.entity_type.replaceAll("_", " ")} ${e.action.replaceAll("_", " ")}`;
}

/** The request's audit trail, oldest first, paged. */
export function Audit({ decision }: { decision: Decision }) {
  const [skip, setSkip] = useState(0);
  const read = useRead<Page<Entry>>(`/audit-logs?decision_request_id=${encodeURIComponent(decision.id)}&limit=${LIMIT}&skip=${skip}`);
  const agents = useAgentNames();
  const now = useNow();
  const blind = isBlind(decision);

  const actor = (e: Entry) => (e.actor_type === "system" ? "System" : e.actor_type === "agent" ? agents.name(e.actor_id) : `Person ${(e.actor_id ?? "").slice(0, 8)}`);

  return (
    <div className="max-w-3xl">
      <ReadView read={read} label="Audit" isEmpty={(p) => p.items.length === 0} empty={<p className="m-0 py-2">Nothing has happened to this request yet.</p>}>
        {(p) => (
          <>
            <ol aria-label="Audit trail" className="m-0 list-none p-0">
              {p.items.map((e) => (
                <li key={e.id} className="grid gap-x-4 border-b border-rule py-2.5 last:border-b-0 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
                  <span className="font-medium">{sentence(e, blind)}</span>
                  <span className="text-muted">{actor(e)}</span>
                  <span className="text-small text-muted tabular-nums">{clockTime(e.created_at, now)}</span>
                </li>
              ))}
            </ol>
            <nav aria-label="Audit pages" className="mt-3 flex items-center justify-between gap-3">
              <span className="text-muted tabular-nums">
                {p.skip + 1}-{p.skip + p.items.length} of {p.total}
              </span>
              <span className="flex gap-2">
                <button type="button" disabled={skip === 0} onClick={() => setSkip(Math.max(0, skip - LIMIT))} className="h-control rounded-control border border-field bg-surface px-3 font-medium hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50 max-lg:h-touch coarse:h-touch">
                  Previous
                </button>
                <button type="button" disabled={skip + p.items.length >= p.total} onClick={() => setSkip(skip + LIMIT)} className="h-control rounded-control border border-field bg-surface px-3 font-medium hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50 max-lg:h-touch coarse:h-touch">
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
