import { clockTime } from "@/lib/time";
import type { Decision, Vote, VotesPage } from "@/lib/api/types";

import { isBlind } from "./model";

const HIDDEN = "Vote hidden until round closes";

function Mark({ verdict }: { verdict: Vote["verdict"] }) {
  // Glyph and shape carry the verdict, not colour alone: filled check, ringed cross, dash.
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="mt-px size-5 flex-none">
      {verdict === "approve" ? (
        <>
          <circle cx="10" cy="10" r="10" className="fill-accent" />
          <path d="M5.5 10.3l3 3 6-6.5" className="fill-none stroke-surface" strokeWidth="1.8" />
        </>
      ) : verdict === "reject" ? (
        <>
          <circle cx="10" cy="10" r="9" className="fill-none stroke-negative" strokeWidth="1.8" />
          <path d="M6.8 6.8l6.4 6.4M13.2 6.8l-6.4 6.4" className="fill-none stroke-negative" strokeWidth="1.8" />
        </>
      ) : (
        <>
          <circle cx="10" cy="10" r="9" className="fill-none stroke-control" strokeWidth="1.8" />
          <path d="M6.5 10h7" className="fill-none stroke-muted" strokeWidth="1.8" />
        </>
      )}
    </svg>
  );
}

const VERDICT = { approve: "Approve", reject: "Reject", abstain: "Abstain" } as const;

function Row({ v, struck }: { v: Vote; struck?: boolean }) {
  return (
    <li className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] gap-x-2.5 gap-y-0.5 border-b border-rule py-2.5 last:border-b-0">
      <Mark verdict={v.verdict} />
      <span className={`min-w-0 font-medium ${struck ? "line-through" : ""}`}>
        <span className="font-mono text-small">Session {v.session_id.slice(0, 8)}</span> · {VERDICT[v.verdict]}
      </span>
      <span className={`text-small text-muted tabular-nums ${struck ? "line-through" : ""}`}>confidence {v.confidence.toFixed(2)}</span>
      {/* Agent-authored: rendered as text only. */}
      <span className={`col-span-2 col-start-2 text-muted wrap-anywhere ${struck ? "line-through" : ""}`}>{v.rationale}</span>
    </li>
  );
}

/** One placeholder per reviewer slot of a blind round: how many voted, never what. */
function Blind({ cast, quorum }: { cast: number; quorum: number }) {
  return (
    <ul className="m-0 list-none p-0" aria-label="Reviewer votes, hidden">
      {Array.from({ length: Math.max(quorum, cast) }, (_, i) => (
        <li key={i} className="flex items-center gap-2.5 border-b border-rule py-2.5 text-muted last:border-b-0">
          <span aria-hidden="true" className={`size-5 flex-none rounded-full border-2 ${i < cast ? "border-muted" : "border-dashed border-control"}`} />
          <span>{i < cast ? `Reviewer ${i + 1} has voted. ${HIDDEN}` : `Reviewer ${i + 1} has not voted yet`}</span>
        </li>
      ))}
    </ul>
  );
}

const byRound = (votes: Vote[]) => [...new Set(votes.map((v) => v.round))].sort((a, b) => a - b);

/** Votes of the current version by round, a blind round as placeholders, and older versions struck through. */
export function VotesList({ decision, votes, now }: { decision: Decision; votes: VotesPage; now: number }) {
  if (isBlind(decision)) return <Blind cast={votes.cast} quorum={votes.quorum} />;
  const mine = votes.items.filter((v) => v.request_version === decision.version);
  const older = [...new Set(votes.items.filter((v) => v.request_version < decision.version).map((v) => v.request_version))].sort((a, b) => a - b);
  if (mine.length === 0 && older.length === 0) return <p className="m-0 text-muted">No votes yet.</p>;
  return (
    <div className="flex flex-col gap-4">
      {byRound(mine).map((round) => {
        const rows = mine.filter((v) => v.round === round);
        const at = rows[0]?.revealed_at;
        return (
          <section key={round} aria-label={`Round ${round}`}>
            <h4 className="m-0 mb-1 text-small font-medium text-muted">
              Round {round}
              {at && round === 1 ? ` · revealed ${clockTime(at, now)}` : ""}
            </h4>
            <ul className="m-0 list-none p-0">
              {rows.map((v) => (
                <Row key={`${v.session_id}-${v.round}`} v={v} />
              ))}
            </ul>
          </section>
        );
      })}
      {older.map((ver) => (
        <section key={ver} aria-label={`Version ${ver}`}>
          <h4 className="m-0 mb-1 text-small font-medium text-muted">Version {ver} · not counted, the request changed</h4>
          <ul className="m-0 list-none p-0">
            {votes.items
              .filter((v) => v.request_version === ver)
              .map((v) => (
                <Row key={`${v.session_id}-${v.round}-${ver}`} v={v} struck />
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
