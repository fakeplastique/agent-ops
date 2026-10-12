"use client";

import { DecisionRail } from "@/components/DecisionRail/DecisionRail";
import { PayloadBlock } from "@/components/Decision/PayloadBlock";
import { RiskPill, StatusPill } from "@/components/Decision/Pills";
import { VotesList } from "@/components/Decision/VotesList";
import { findingLine, railProps } from "@/components/Decision/model";
import { useDecisionBundle } from "@/components/Decision/useDecisionBundle";
import { ReadView } from "@/components/states/States";
import type { PendingStep } from "@/lib/api/types";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

import { ActionBar } from "./ActionBar";

/** The open step: title, where it stands (rail), the finding, votes, payload, and the always-visible decision bar. */
export function Pane({
  step,
  agentName,
  shortcuts,
  onToggleShortcuts,
  onDecided,
}: {
  step: PendingStep;
  agentName: string;
  shortcuts: boolean;
  onToggleShortcuts: () => void;
  onDecided: () => void;
}) {
  const read = useDecisionBundle(step.decision_request_id);
  const now = useNow();
  return (
    <ReadView read={read} label="Request" role="manager">
      {(b) => {
        const d = b.decision;
        const rail = railProps(b, now);
        return (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-1 flex-col gap-5.5 pb-2">
              <div>
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                  <h2 className="m-0 text-title text-balance wrap-anywhere">{d.title}</h2>
                  <RiskPill risk={d.effective_risk_level} />
                  <StatusPill status={d.status} />
                </div>
                <p className="m-0 mt-1.5 flex flex-wrap gap-x-3 text-muted">
                  <span>
                    <span className="font-mono text-small">{agentName}</span>
                  </span>
                  <span className="font-mono text-small">{d.action_type}</span>
                  <span className="tabular-nums">
                    Version {d.version} · {clockTime(d.created_at, now)}
                  </span>
                  {read.isFetching && <span role="status">Updating</span>}
                </p>
              </div>
              {rail && <DecisionRail {...rail} />}
              <p className="m-0 rounded-card bg-raised px-3.5 py-3">{findingLine(b, now)}</p>
              <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-6 max-xl:grid-cols-1">
                <section aria-label="Reviewer votes">
                  <h3 className="m-0 mb-2 text-body font-semibold">Reviewer votes</h3>
                  <VotesList decision={d} votes={b.votes} now={now} />
                </section>
                <section aria-label="Payload section">
                  <h3 className="m-0 mb-2 text-body font-semibold">
                    Payload <span className="font-normal text-small text-muted">from {agentName}, treated as data</span>
                  </h3>
                  <PayloadBlock payload={d.payload} />
                </section>
              </div>
            </div>
            <ActionBar
              key={step.id}
              bundle={b}
              step={step}
              shortcuts={shortcuts}
              onToggleShortcuts={onToggleShortcuts}
              onDecided={onDecided}
              reload={() => void read.refetch()}
            />
          </div>
        );
      }}
    </ReadView>
  );
}
