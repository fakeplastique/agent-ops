"use client";

import { useId, useRef, useState } from "react";

import { Notice, retryNote } from "@/app/(auth)/AuthFrame";
import { ApiError } from "@/lib/api/client";
import { decideStep, newKey } from "@/lib/api/steps";
import type { PendingStep } from "@/lib/api/types";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

import type { Bundle } from "@/components/Decision/model";

type Verdict = "approve" | "reject";
type Phase =
  | { kind: "idle" }
  | { kind: "sending"; decision: Verdict }
  | { kind: "done"; decision: Verdict; at: number; nextLevel: boolean }
  | { kind: "conflict" }
  | { kind: "stale" }
  | { kind: "offline" }
  | { kind: "failed"; title: string; note: string };

const button =
  "inline-flex h-control items-center justify-center rounded-control border px-4 font-medium transition-colors max-lg:h-touch disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-default aria-disabled:opacity-60";
const APPROVE = `${button} border-text bg-text text-surface hover:opacity-85`;
const REJECT = `${button} border-control bg-surface text-text hover:bg-raised`;

const CONFLICT = new Set(["already_decided", "step_not_pending", "illegal_transition"]);

function failure(e: unknown): Phase {
  if (!(e instanceof ApiError)) return { kind: "failed", title: "Could not send the decision", note: retryNote(e) };
  if (e.status === 0) return { kind: "offline" };
  if (e.status === 409 && e.code === "stale_version") return { kind: "stale" };
  if (e.status === 409 && CONFLICT.has(e.code)) return { kind: "conflict" };
  if (e.status === 403)
    return { kind: "failed", title: "You can't decide this step", note: "Someone else, or a higher role, has to decide it. Nothing changed." };
  if (e.status === 404) return { kind: "failed", title: "This request is no longer available", note: "Nothing changed. Reload the inbox." };
  if (e.status === 429)
    return { kind: "failed", title: "Too many requests", note: `Nothing changed. Try again ${e.retryAfter ? `in ${e.retryAfter} seconds` : "in a moment"}.` };
  return { kind: "failed", title: "Could not send the decision", note: retryNote(e) };
}

/** Approve and Reject bar: comment required to reject, one request per press, closed once the step is decided. */
export function ActionBar({
  bundle,
  step,
  shortcuts,
  onToggleShortcuts,
  onDecided,
  reload,
}: {
  bundle: Bundle;
  step: PendingStep;
  /** Omit both where the keyboard shortcuts are not offered (the detail screen). */
  shortcuts?: boolean;
  onToggleShortcuts?: () => void;
  onDecided: () => void;
  reload: () => void;
}) {
  const id = useId();
  const [comment, setComment] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const now = useNow();
  const inFlight = useRef(false);
  // One key per attempt (same decision, same comment): a retry replays it, a changed decision gets its own.
  const attempt = useRef<{ sig: string; key: string } | null>(null);

  const { decision } = bundle;
  const mine = bundle.steps.items.find((s) => s.id === step.id);
  const stale = decision.version !== step.request_version;
  const closed = !stale && (decision.status !== "pending_approval" || (mine !== undefined && mine.status !== "pending"));

  // Before `closed`: the re-read after our own decision must not replace the confirmation.
  if (phase.kind === "done")
    return (
      <div className="border-t border-border bg-surface px-6 py-3 max-lg:px-4">
        <p className="m-0" role="status">
          You {phase.decision === "approve" ? "approved" : "rejected"} this at {clockTime(phase.at, phase.at)}.
          {phase.decision === "approve" && phase.nextLevel ? " The next level now has to decide it." : ""}
        </p>
      </div>
    );

  if (stale || phase.kind === "stale")
    return (
      <div className="border-t border-border bg-surface px-6 py-3 max-lg:px-4">
        <Notice title="This request changed">
          The agent revised it, so earlier votes and steps no longer apply.{" "}
          <button type="button" onClick={reload} className="text-text underline underline-offset-2">
            Reload
          </button>
        </Notice>
      </div>
    );

  if (closed || phase.kind === "conflict")
    return (
      <div className="border-t border-border bg-surface px-6 py-3 max-lg:px-4">
        <p className="m-0" role="status">
          {phase.kind === "conflict" ? "This step was already decided. " : "This step is no longer waiting. "}
          {mine?.decided_by ? `Decided by ${mine.decided_by}${mine.decided_at ? ` at ${clockTime(mine.decided_at, now)}` : ""}.` : "The progress above shows where it stands."}
        </p>
      </div>
    );

  const sending = phase.kind === "sending";
  const offline = phase.kind === "offline";
  const blank = comment.trim() === "";

  async function send(decisionKind: Verdict) {
    if (inFlight.current || offline) return;
    inFlight.current = true;
    const sig = `${decisionKind}|${comment}`;
    if (attempt.current?.sig !== sig) attempt.current = { sig, key: newKey() };
    setPhase({ kind: "sending", decision: decisionKind });
    try {
      const out = await decideStep(step.id, { request_version: step.request_version, decision: decisionKind, comment }, attempt.current.key);
      setPhase({ kind: "done", decision: decisionKind, at: Date.now(), nextLevel: out.next_step_id !== null });
      onDecided();
    } catch (e) {
      const next = failure(e);
      setPhase(next);
      if (next.kind === "conflict") reload();
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-x-4 gap-y-2 border-t border-border bg-surface px-6 pt-3 pb-3.5 max-lg:sticky max-lg:bottom-0 max-lg:grid-cols-1 max-lg:px-4">
      <label htmlFor={`${id}-c`} className="col-span-full flex flex-wrap justify-between gap-x-2 font-medium max-lg:text-body">
        Comment
        <span className="font-normal text-muted">Required to reject. The agent sees it.</span>
      </label>
      <textarea
        id={`${id}-c`}
        value={comment}
        maxLength={4096}
        rows={2}
        onChange={(e) => setComment(e.target.value)}
        aria-describedby={`${id}-hint`}
        className="min-h-10 w-full resize-y rounded-control border border-control bg-surface px-2.5 py-2 text-text"
      />
      <div className="flex gap-2 max-lg:[&>button]:flex-1">
        <button
          type="button"
          data-action="approve"
          aria-disabled={sending}
          disabled={offline}
          onClick={() => void send("approve")}
          className={APPROVE}
        >
          {sending && phase.decision === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          data-action="reject"
          aria-disabled={sending}
          disabled={offline || blank}
          aria-describedby={blank ? `${id}-need` : undefined}
          onClick={() => void send("reject")}
          className={REJECT}
        >
          {sending && phase.decision === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>
      {blank && (
        <p id={`${id}-need`} className="col-span-full m-0 text-small text-muted">
          Add a reason to reject.
        </p>
      )}
      {offline && (
        <div className="col-span-full">
          <Notice title="No connection">
            Nothing changed. Check your connection, then check again.{" "}
            <button
              type="button"
              onClick={() => {
                setPhase({ kind: "idle" });
                reload();
              }}
              className="text-text underline underline-offset-2"
            >
              Check connection
            </button>
          </Notice>
        </div>
      )}
      {phase.kind === "failed" && (
        <div className="col-span-full">
          <Notice title={phase.title}>{phase.note}</Notice>
        </div>
      )}
      <p id={`${id}-hint`} className="col-span-full m-0 flex flex-wrap items-center gap-x-2 text-small text-muted">
        <span>
          You decide level {step.level} only.
          {onToggleShortcuts && (shortcuts ? " Shortcuts: A approves, R rejects." : " Shortcuts are off.")}
        </span>
        {onToggleShortcuts && (
          <button type="button" onClick={onToggleShortcuts} className="text-text underline underline-offset-2">
            {shortcuts ? "Turn shortcuts off" : "Turn shortcuts on"}
          </button>
        )}
      </p>
    </div>
  );
}
