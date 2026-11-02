"use client";

import { useState } from "react";

import { Field, Screen, Section, WithSession, inputClass } from "@/components/admin/ui";
import { useDemoSwitch } from "@/components/admin/useDemoSwitch";
import { useRead } from "@/components/admin/useRead";
import { BarList, Columns, dayLabel } from "@/components/charts/Charts";
import { PlanGated, ReadView } from "@/components/states/States";

import type { Plan } from "../plan/PlanView";

type Summary = {
  first_day: string;
  last_day: string;
  days: number;
  requests: number;
  by_status: Record<string, number>;
  by_risk: Record<string, number>;
  auto_approval_rate: number | null;
  median_seconds_to_decision: number | null;
  votes_per_agent: { agent_id: string; name: string; votes: number }[];
};
type Series = { points: { date: string; requests: number }[] };

const FEATURE = "Analytics";
const DESCRIPTION = "Requests, how many were approved without a human, how long decisions take, and which agents vote most.";
const STATUS: [string, string][] = [
  ["pending_votes", "Waiting for votes"],
  ["pending_discussion", "In discussion"],
  ["pending_approval", "Waiting for approval"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["expired", "Expired"],
  ["cancelled", "Cancelled"],
];
const RISK: [string, string][] = [
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
];
const PERIODS = [7, 30, 90];

/** A fixed list of known keys (a missing one is a real zero) followed by any key the API sent that this screen does not know. */
const rows = (counts: Record<string, number>, known: [string, string][]) => [
  ...known.map(([k, label]) => ({ label, value: counts[k] ?? 0 })),
  ...Object.keys(counts)
    .filter((k) => !known.some(([x]) => x === k))
    .map((k) => ({ label: k, value: counts[k] })),
];

export function fmtDuration(s: number): string {
  const sec = Math.round(s);
  if (sec < 60) return `${sec} s`;
  if (sec < 3600) return sec % 60 ? `${Math.floor(sec / 60)} min ${sec % 60} s` : `${sec / 60} min`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)} h ${String(Math.floor((sec % 3600) / 60)).padStart(2, "0")} min`;
  return `${Math.floor(sec / 86_400)} d ${Math.floor((sec % 86_400) / 3600)} h`;
}

export function AnalyticsView() {
  return <WithSession title="Analytics">{(s) => <Analytics isAdmin={s.role === "org_admin"} />}</WithSession>;
}

function Analytics({ isAdmin }: { isAdmin: boolean }) {
  const plan = useRead<Plan>("/organizations/current/plan");
  const demo = useDemoSwitch(isAdmin, () => plan.refetch());
  return (
    <Screen title="Analytics" lead="How decisions are going in this organization.">
      {/* The flag decides before any stats call: Free makes none. */}
      <ReadView read={plan} label="Analytics">
        {(p) => (p.flags.includes("analytics") ? <Stats /> : <PlanGated feature={FEATURE} description={DESCRIPTION} plan="pro" onSwitchPlan={demo.switchPlan} />)}
      </ReadView>
    </Screen>
  );
}

function Stats() {
  const [days, setDays] = useState(30);
  return (
    <div className="flex flex-col gap-8">
      <div className="w-44">
        <Field label="Period">
          {(p) => (
            <select {...p} value={days} onChange={(e) => setDays(Number(e.target.value))} className={inputClass}>
              {PERIODS.map((d) => (
                <option key={d} value={d}>{`Last ${d} days`}</option>
              ))}
            </select>
          )}
        </Field>
      </div>
      {/* Keyed by the period: a new period starts from loading, never from the old period's numbers. */}
      <Results key={days} days={days} />
    </div>
  );
}

function Results({ days }: { days: number }) {
  const summary = useRead<Summary>(`/stats/summary?days=${days}`);
  const series = useRead<Series>(`/stats/timeseries?days=${days}`);

  return (
    <div className="flex flex-col gap-8">
      {summary.status === "success" && (
        <p role="status" className="m-0 text-small text-muted tabular-nums">
          {dayLabel(summary.data!.first_day)} to {dayLabel(summary.data!.last_day)}
          {summary.isFetching ? " · Updating" : ""}
        </p>
      )}
      <ReadView read={summary} label="Analytics" gate={{ feature: FEATURE, description: DESCRIPTION }}>
        {(s) => (
          <div className="flex flex-col gap-8">
            <dl className="m-0 grid grid-cols-1 border-y border-rule sm:grid-cols-3 sm:divide-x sm:divide-rule">
              <Kpi label="Requests" value={String(s.requests)} />
              <Kpi label="Auto-approval rate" value={s.auto_approval_rate === null ? "—" : `${Math.round(100 * s.auto_approval_rate)}%`} note="Approved without a human step" />
              <Kpi label="Median time to decision" value={s.median_seconds_to_decision === null ? "—" : fmtDuration(s.median_seconds_to_decision)} note="From request to final outcome" />
            </dl>

            <div className="grid gap-8 lg:grid-cols-2">
              <Section title="By status">
                <BarList label="Requests by status" rows={rows(s.by_status, STATUS)} />
              </Section>
              <Section title="By risk">
                <BarList label="Requests by risk" rows={rows(s.by_risk, RISK)} />
              </Section>
            </div>

            <Section title="Requests per day">
              {s.requests === 0 ? (
                <p className="m-0 text-muted">No requests in the last {s.days} days. The chart appears with the first one.</p>
              ) : (
                <ReadView read={series} label="Requests per day" isEmpty={(d) => d.points.length === 0} empty={<p className="m-0 text-muted">No days to show.</p>}>
                  {(d) => <Columns noun="Requests" points={d.points.map((p) => ({ date: p.date, value: p.requests }))} />}
                </ReadView>
              )}
            </Section>

            <Section title="Votes per agent">
              {s.votes_per_agent.length === 0 ? (
                <p className="m-0 text-muted">No votes yet in this period.</p>
              ) : (
                <BarList label="Votes per agent" rows={s.votes_per_agent.map((a) => ({ label: a.name, value: a.votes }))} />
              )}
            </Section>
          </div>
        )}
      </ReadView>
    </div>
  );
}

function Kpi({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex flex-col gap-1 px-0 py-4 sm:px-6 sm:first:pl-0">
      <dt className="text-muted">{label}</dt>
      <dd className="m-0 text-kpi font-normal tabular-nums">{value}</dd>
      {note && <p className="m-0 text-small text-muted">{note}</p>}
    </div>
  );
}
