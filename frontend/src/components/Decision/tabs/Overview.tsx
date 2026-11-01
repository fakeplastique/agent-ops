import type { Decision } from "@/lib/api/types";
import { clockTime } from "@/lib/time";

import { PayloadBlock } from "../PayloadBlock";

/** What the agent asked for: description, action type, payload. All of it is agent-authored text, shown as text. */
export function Overview({ decision: d, agent, now }: { decision: Decision; agent: string; now: number }) {
  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <section aria-label="Description">
        <h3 className="m-0 mb-1 text-body font-semibold">Description</h3>
        {d.description ? <p className="m-0 max-w-(--measure) whitespace-pre-wrap wrap-anywhere">{d.description}</p> : <p className="m-0 text-muted">The agent gave no description.</p>}
      </section>
      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1.5 text-body">
        <dt className="text-muted">Action type</dt>
        <dd className="m-0 font-mono text-small wrap-anywhere">{d.action_type}</dd>
        <dt className="text-muted">Requested by</dt>
        <dd className="m-0 wrap-anywhere">{agent}</dd>
        <dt className="text-muted">Declared risk</dt>
        <dd className="m-0">{d.risk_level === d.effective_risk_level ? d.risk_level : `${d.risk_level}, raised to ${d.effective_risk_level} by policy`}</dd>
        <dt className="text-muted">Created</dt>
        <dd className="m-0 tabular-nums">{clockTime(d.created_at, now)}</dd>
        <dt className="text-muted">Content hash</dt>
        <dd className="m-0 font-mono text-small break-all">{d.content_hash}</dd>
      </dl>
      <section aria-label="Payload section">
        <h3 className="m-0 mb-2 text-body font-semibold">
          Payload <span className="font-normal text-small text-muted">from {agent}, treated as data</span>
        </h3>
        <PayloadBlock payload={d.payload} />
      </section>
    </div>
  );
}
