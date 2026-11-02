"use client";

import Link from "next/link";
import { useState } from "react";

import { Notice } from "@/app/(auth)/AuthFrame";
import { DecisionRail } from "@/components/DecisionRail/DecisionRail";
import { ActionBar, type Decided } from "@/components/Inbox/ActionBar";
import { ReadView } from "@/components/states/States";
import { useApp } from "@/components/shell/AppShell";
import type { Page, PendingStep } from "@/lib/api/types";
import { useAgentNames } from "@/lib/api/useAgents";
import { useRead } from "@/lib/api/useRead";
import { touchesRequest, useUpdatedNotice } from "@/lib/realtime/react";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

import { findingLine, railProps } from "./model";
import { RiskPill, StatusPill } from "./Pills";
import { Tabs, type TabDef } from "./Tabs";
import { Attachments } from "./tabs/Attachments";
import { Audit } from "./tabs/Audit";
import { Discussion } from "./tabs/Discussion";
import { Overview } from "./tabs/Overview";
import { Receipt } from "./tabs/Receipt";
import { Timeline } from "./tabs/Timeline";
import { Votes } from "./tabs/Votes";
import { useDecisionBundle } from "./useDecisionBundle";

const TABS: TabDef[] = [
  { id: "overview", label: "Overview" },
  { id: "votes", label: "Votes" },
  { id: "timeline", label: "Timeline" },
  { id: "discussion", label: "Discussion" },
  { id: "attachments", label: "Attachments" },
  { id: "audit", label: "Audit" },
  { id: "receipt", label: "Receipt" },
];

/** One request: header and finding, the rail, then tabs. The decision bar shows only for a step the viewer may decide now. */
export function DecisionDetail({ id, tab, onTab }: { id: string; tab: string; onTab: (tab: string) => void }) {
  const read = useDecisionBundle(id);
  const mySteps = useRead<Page<PendingStep>>("/me/pending-approvals?limit=100");
  const { updated } = useUpdatedNotice(touchesRequest(id), () => {
    void read.refetch();
    void mySteps.refetch();
  });
  const agents = useAgentNames();
  const { refreshInbox } = useApp();
  const now = useNow();
  // Kept here: the bar unmounts once the step leaves the pending list, and the confirmation must not go with it.
  const [done, setDone] = useState<Decided | null>(null);

  return (
    <ReadView read={read} label="Decision" role="member">
      {(b) => {
        const d = b.decision;
        const rail = railProps(b, now);
        const agent = agents.name(d.requested_by_agent_id);
        const open = d.status === "pending_approval";
        const mine = open ? mySteps.data?.items.find((s) => s.decision_request_id === d.id) : undefined;
        return (
          <div className="flex flex-col gap-5">
            <div>
              <Link href="/decisions" className="text-small text-muted no-underline hover:text-text">
                ← Decisions
              </Link>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                <h1 className="m-0 text-balance wrap-anywhere">{d.title}</h1>
                <StatusPill status={d.status} />
                <RiskPill risk={d.effective_risk_level} />
              </div>
              <p className="m-0 mt-1.5 flex flex-wrap gap-x-3 text-muted">
                <span className="font-mono text-small">{agent}</span>
                <span className="tabular-nums">
                  Version {d.version} · {clockTime(d.created_at, now)}
                </span>
                {read.isFetching && <span role="status">Updating</span>}
                {updated && <span role="status">Updated</span>}
              </p>
              <p className="m-0 mt-2 max-w-(--measure)">{findingLine(b, now)}</p>
            </div>
            {rail && <DecisionRail {...rail} headingLevel={2} />}
            <Tabs tabs={TABS} selected={tab} onSelect={onTab}>
              {tab === "votes" ? (
                <Votes bundle={b} now={now} />
              ) : tab === "timeline" ? (
                <Timeline bundle={b} now={now} />
              ) : tab === "discussion" ? (
                <Discussion decision={d} />
              ) : tab === "attachments" ? (
                <Attachments decision={d} />
              ) : tab === "audit" ? (
                <Audit decision={d} />
              ) : tab === "receipt" ? (
                <Receipt decision={d} />
              ) : (
                <Overview decision={d} agent={agent} now={now} />
              )}
            </Tabs>
            {open && mySteps.status === "error" && (
              <Notice title="Your approval steps did not load">
                <button type="button" onClick={() => void mySteps.refetch()} className="text-text underline underline-offset-2">
                  Retry
                </button>
              </Notice>
            )}
            {(mine || done) && (
              <div className="sticky bottom-0 -mx-4 -mb-4 sm:-mx-6 sm:-mb-6">
                {mine && !done ? (
                  <ActionBar
                    key={mine.id}
                    bundle={b}
                    step={mine}
                    onDecided={(d) => {
                      setDone(d);
                      refreshInbox();
                      void mySteps.refetch();
                      void read.refetch();
                    }}
                    reload={() => {
                      void read.refetch();
                      void mySteps.refetch();
                    }}
                  />
                ) : (
                  done && (
                    <div className="border-t border-border bg-surface px-4 py-3 sm:px-6">
                      <p className="m-0" role="status">
                        You {done.decision === "approve" ? "approved" : "rejected"} this at {clockTime(done.at, done.at)}.
                        {done.decision === "approve" && done.nextLevel ? " The next level now has to decide it." : ""}
                      </p>
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        );
      }}
    </ReadView>
  );
}
