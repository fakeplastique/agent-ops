import type { RailLevel, RailProps } from "@/components/DecisionRail/DecisionRail";
import { clockTime, timeLeft } from "@/lib/time";
import { roleLabel, type Decision, type Step, type StepsPage, type Vote, type VotesPage } from "@/lib/api/types";

export type Bundle = { decision: Decision; votes: VotesPage; steps: StepsPage };

/** Round 1 is blind until the request leaves pending_votes (the API shows a human only counts until then). */
export const isBlind = (d: Decision) => d.status === "pending_votes";

const TERMINAL_NO_RECEIPT = new Set(["expired", "cancelled"]);
export const endsWithoutReceipt = (d: Decision) => TERMINAL_NO_RECEIPT.has(d.status);

/** Votes cast on the current version, in the newest round that has any (older versions are struck through elsewhere). */
export function currentVotes(d: Decision, votes: Vote[]): Vote[] {
  const mine = votes.filter((v) => v.request_version === d.version);
  const round = Math.max(0, ...mine.map((v) => v.round));
  return mine.filter((v) => v.round === round);
}

/** Steps of the current version only; an older version's steps were superseded. */
export const currentSteps = (d: Decision, steps: Step[]) => steps.filter((s) => s.request_version === d.version);

export const shortHash = (h: string) => (h.length > 14 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h);

function levelNote(s: Step, now: number): string | undefined {
  if (s.status === "approved" || s.status === "rejected")
    return `${s.decided_by ?? "Someone"}${s.decided_at ? `, ${clockTime(s.decided_at, now)}` : ""}`;
  if (s.status === "timed_out") return `Timed out ${clockTime(s.due_at, now)}`;
  if (s.status === "pending") return `Due ${clockTime(s.due_at, now)}`;
  return undefined;
}

/** DecisionRail props from what the API returns. Time left, window and due time all come from the one pending step. */
export function railProps(b: Bundle, now: number): RailProps | null {
  const { decision: d, votes, steps } = b;
  if (endsWithoutReceipt(d)) return null;
  const mine = currentSteps(d, steps.items);
  const outcome = d.status === "approved" ? "approved" : d.status === "rejected" ? "rejected" : "pending";
  const blind = isBlind(d);
  const seen = currentVotes(d, votes.items);
  const autoApproved = d.status === "approved" && mine.length === 0;
  const levels: RailLevel[] = autoApproved
    ? []
    : steps.levels.map((lv) => {
        const s = mine.find((x) => x.level === lv.level);
        const state: RailLevel["state"] =
          !s || s.status === "superseded" ? "open" : s.status === "pending" ? "current" : s.status === "rejected" ? "rejected" : "done";
        const note = s ? levelNote(s, now) : undefined;
        return { level: lv.level, role: roleLabel(lv.role), state, note: s?.stands_in_for_level ? `${note ?? ""} (stands in for level ${s.stands_in_for_level})`.trim() : note };
      });
  const pending = mine.find((s) => s.status === "pending");
  const clock = pending
    ? { startedAt: pending.created_at, dueAt: pending.due_at }
    : d.status === "pending_votes" || d.status === "pending_discussion"
      ? { startedAt: d.created_at, dueAt: d.voting_deadline_at }
      : undefined;
  return {
    quorum: {
      need: steps.quorum,
      approved: seen.filter((v) => v.verdict === "approve").length,
      voted: votes.cast,
      reviewers: votes.quorum,
      blind,
      revealedAt: !blind && seen[0]?.revealed_at ? clockTime(seen[0].revealed_at, now) : undefined,
    },
    levels,
    outcome,
    risk: d.effective_risk_level,
    receipt: outcome === "approved" ? { hash: shortHash(d.content_hash), signedAt: d.decided_at ? clockTime(d.decided_at, now) : undefined } : undefined,
    clock,
  };
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The one finding under the title. In a blind round it says nothing about verdicts, only how many have voted. */
export function findingLine(b: Bundle, now: number): string {
  const { decision: d, votes, steps } = b;
  if (d.status === "cancelled") return "The agent withdrew this request. No receipt is issued.";
  if (d.status === "expired") return "No level decided in time, so the request expired. No receipt is issued.";
  if (d.status === "approved") return "Approved. The agent can read the signed receipt.";
  if (d.status === "rejected") return "Rejected. No receipt is issued.";
  if (isBlind(d)) return `${votes.cast} of ${votes.quorum} reviewers have voted. Votes stay hidden until the round closes.`;
  const seen = currentVotes(d, votes.items);
  const rej = seen.filter((v) => v.verdict === "reject").length;
  const total = seen.length;
  const tally = total === 0 ? "" : rej === 0 ? ` All ${plural(total, "reviewer", "reviewers")} approved.` : ` ${rej} of ${total} ${total === 1 ? "reviewer" : "reviewers"} rejected it.`;
  const pending = currentSteps(d, steps.items).find((s) => s.status === "pending");
  if (!pending) return `Waiting for reviewers.${tally}`;
  const left = timeLeft(pending.due_at, now);
  const next = steps.levels.find((l) => l.level === pending.level + 1);
  const when = left && left.ms > 0 ? `in ${left.label.replace(" left", "")}` : "now";
  const after = next ? `goes to the ${roleLabel(next.role).toLowerCase()}` : "expires";
  return `Level ${pending.level} ${after} if it is not decided ${when}.${tally}`;
}
