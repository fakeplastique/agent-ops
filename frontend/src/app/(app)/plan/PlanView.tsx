"use client";

import { ActionError, Screen, Section, WithSession, secondaryButton } from "@/components/admin/ui";
import { useDemoSwitch } from "@/components/admin/useDemoSwitch";
import { useRead } from "@/components/admin/useRead";
import { ReadView } from "@/components/states/States";

export type Plan = {
  plan: string;
  limits: { requests_per_month: number | null; max_quorum: number | null; max_rounds: number; max_levels: number };
  usage: { requests: { used: number; limit: number | null; period: string } };
  flags: string[];
};

const PLANS = ["free", "pro", "enterprise"];
const cap = (p: string) => p.charAt(0).toUpperCase() + p.slice(1);
// flag key -> what the user sees; the API lists only the flags that are on.
export const FEATURES: [string, string][] = [
  ["attachments", "Attachments"],
  ["telegram", "Telegram approvals"],
  ["analytics", "Analytics"],
  ["search", "Search"],
  ["team_policies", "Per-team policies"],
  ["audit_export", "Audit export"],
];
const upTo = (n: number | null) => (n === null ? "Any number" : `Up to ${n}`);

export function PlanView() {
  return <WithSession title="Plan and usage">{(s) => <PlanScreen isAdmin={s.role === "org_admin"} />}</WithSession>;
}

function PlanScreen({ isAdmin }: { isAdmin: boolean }) {
  const read = useRead<Plan>("/organizations/current/plan");
  const demo = useDemoSwitch(isAdmin, () => read.refetch());

  return (
    <Screen
      title="Plan and usage"
      lead="What this organization can do, and how much of this month it has used."
      updating={read.status === "success" && read.isFetching}
    >
      <ReadView read={read} label="Plan and usage">
        {(p) => {
          const { used, limit, period } = p.usage.requests;
          const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 0;
          return (
            <div className="flex max-w-2xl flex-col gap-8">
              <Section title={`${cap(p.plan)} plan`}>
                <dl className="m-0 border-t border-rule">
                  <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule py-3">
                    <dt>Requests this month ({period})</dt>
                    <dd className="m-0 font-medium tabular-nums">{limit === null ? `${used} / unlimited` : `${used} / ${limit}`}</dd>
                  </div>
                  {limit !== null && (
                    <div className="border-b border-rule py-3">
                      <div
                        role="meter"
                        aria-label="Requests used this month"
                        aria-valuemin={0}
                        aria-valuemax={limit}
                        aria-valuenow={Math.min(used, limit)}
                        className="h-2 overflow-hidden rounded-pill bg-active"
                      >
                        <div data-bar className="h-full bg-chart-1" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  )}
                  <Limit label="Reviewers per request" value={upTo(p.limits.max_quorum)} />
                  <Limit label="Voting rounds" value={upTo(p.limits.max_rounds)} />
                  <Limit label="Approval levels" value={upTo(p.limits.max_levels)} />
                </dl>
              </Section>

              <Section title="Features">
                <ul className="m-0 list-none border-t border-rule p-0">
                  {FEATURES.map(([flag, label]) => {
                    const on = p.flags.includes(flag);
                    return (
                      <li key={flag} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule py-3">
                        <span className={on ? "" : "text-muted"}>{label}</span>
                        <span className={on ? "font-medium text-positive" : "text-muted"}>{on ? "Included" : "Not on this plan"}</span>
                      </li>
                    );
                  })}
                </ul>
              </Section>

              {demo.switchPlan ? (
                <Section title="Demo mode">
                  <p className="m-0 text-muted">This is a demo: a plan changes at once and nothing is billed.</p>
                  <div className="flex flex-wrap gap-2">
                    {PLANS.filter((x) => x !== p.plan).map((x) => (
                      <button key={x} type="button" aria-disabled={demo.busy} className={secondaryButton} onClick={() => !demo.busy && void demo.switchPlan!(x)}>
                        Switch to {cap(x)}
                      </button>
                    ))}
                  </div>
                  <ActionError error={demo.error} />
                </Section>
              ) : (
                <p className="m-0 text-muted">A platform admin changes the plan.</p>
              )}
            </div>
          );
        }}
      </ReadView>
    </Screen>
  );
}

const Limit = ({ label, value }: { label: string; value: string }) => (
  <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule py-3">
    <dt>{label}</dt>
    <dd className="m-0 font-medium">{value}</dd>
  </div>
);
