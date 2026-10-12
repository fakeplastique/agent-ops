import { currentSteps, type Bundle } from "@/components/Decision/model";
import { roleLabel, type Step } from "@/lib/api/types";
import { clockTime, timeLeft } from "@/lib/time";

function describe(s: Step | undefined, now: number): string {
  if (!s || s.status === "superseded") return "Not started";
  if (s.status === "approved") return `Approved by ${s.decided_by ?? "someone"}${s.decided_at ? ` at ${clockTime(s.decided_at, now)}` : ""}`;
  if (s.status === "rejected") return `Rejected by ${s.decided_by ?? "someone"}${s.decided_at ? ` at ${clockTime(s.decided_at, now)}` : ""}`;
  if (s.status === "timed_out") return `Timed out at ${clockTime(s.due_at, now)}`;
  const left = timeLeft(s.due_at, now);
  return `Waiting, due ${clockTime(s.due_at, now)}${left ? ` (${left.label.toLowerCase()})` : ""}`;
}

/** The escalation ladder: each policy level with who decided it or when it is due. Read-only; actions live in the decision bar. */
export function Timeline({ bundle, now }: { bundle: Bundle; now: number }) {
  const { decision: d, steps } = bundle;
  const mine = currentSteps(d, steps.items);
  const earlier = steps.items.filter((s) => s.request_version < d.version);
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <section aria-label="Voting window">
        <h3 className="m-0 mb-1 text-body font-semibold">Voting</h3>
        <p className="m-0 text-muted tabular-nums">
          Quorum of {steps.quorum} reviewers, voting window ends {clockTime(d.voting_deadline_at, now)}.
        </p>
      </section>
      {mine.length === 0 && d.status === "approved" ? (
        <p className="m-0 text-muted">The reviewers agreed, so no human level was needed.</p>
      ) : (
        <section aria-label="Approval levels">
          <h3 className="m-0 mb-1 text-body font-semibold">Approval levels</h3>
          <ol className="m-0 list-none p-0">
            {steps.levels.map((lv) => {
              const s = mine.find((x) => x.level === lv.level);
              return (
                <li key={lv.level} className="grid gap-x-4 border-b border-rule py-2.5 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
                  <span className="font-medium">
                    Level {lv.level} · {roleLabel(lv.role)}
                    <span className="block text-small font-normal text-muted">{lv.timeout_minutes} min timeout</span>
                  </span>
                  <span className="text-muted tabular-nums">
                    {describe(s, now)}
                    {s?.stands_in_for_level ? <span className="block text-small">Stands in for level {s.stands_in_for_level}</span> : null}
                  </span>
                </li>
              );
            })}
          </ol>
        </section>
      )}
      {earlier.length > 0 && (
        <section aria-label="Earlier versions">
          <h3 className="m-0 mb-1 text-body font-semibold">Earlier versions</h3>
          <ul className="m-0 list-none p-0 text-muted">
            {earlier.map((s) => (
              <li key={s.id} className="py-1 line-through">
                Version {s.request_version}, level {s.level} · {roleLabel(s.required_role)}: {describe(s, Date.parse(d.updated_at))}
              </li>
            ))}
          </ul>
          <p className="m-0 mt-1 text-small text-muted">The request changed, so these steps no longer count.</p>
        </section>
      )}
    </div>
  );
}
