"use client";

import { createElement, useEffect, useState, type CSSProperties, type ReactNode } from "react";

import { clockTime, msToNextMinute, timeLeft, windowUsed } from "@/lib/time";

export type Risk = "low" | "medium" | "high";
export type RailLevel = {
  level: number;
  role: string;
  state: "done" | "current" | "open" | "rejected";
  note?: string;
};
export type RailProps = {
  /** In a blind round only `voted` of `reviewers` is shown: approvals would leak the verdict. */
  quorum: { need: number; approved: number; voted?: number; reviewers?: number; blind?: boolean; revealedAt?: string };
  levels: RailLevel[];
  outcome: "pending" | "approved" | "rejected";
  risk?: Risk;
  receipt?: { hash: string; signedAt?: string };
  /** The one source for time left, window and due time. Omit `now` to follow the real clock. */
  clock?: { startedAt: string; dueAt: string; now?: number };
  headingLevel?: 2 | 3 | 4;
};

type NodeState = "done" | "current" | "open" | "rejected" | "signed" | "unsigned";
type Node = {
  key: string;
  state: NodeState;
  word: string;
  title: string;
  sub?: ReactNode;
  fill: number;
  timer?: string;
};

const SETTLE_MS = 50;

/** A fixed `now` wins (tests, replays). Otherwise tick just after each whole-minute boundary of dueAt, when the floored label changes. */
function useNow(fixed: number | undefined, dueAt: string | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (fixed !== undefined) return;
    let id: ReturnType<typeof setTimeout>;
    const tick = () => {
      const t = Date.now();
      setNow(t);
      id = setTimeout(tick, next(t));
    };
    const next = (t: number) => (dueAt ? (msToNextMinute(dueAt, t) ?? 60_000) : 60_000) + SETTLE_MS;
    const wake = () => document.visibilityState === "visible" && (clearTimeout(id), tick());
    id = setTimeout(tick, next(Date.now()));
    document.addEventListener("visibilitychange", wake);
    return () => {
      clearTimeout(id);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [fixed, dueAt]);
  return fixed ?? now;
}

const DOT: Record<NodeState, string> = {
  done: "rounded-full border-accent bg-accent text-surface",
  current: "rounded-full border-accent bg-surface",
  open: "rounded-full border-control bg-surface",
  rejected: "rounded-full border-negative bg-surface text-negative",
  signed: "rounded-[4px] border-accent bg-accent text-surface",
  unsigned: "rounded-full border-dashed border-control bg-surface",
};

const TONE = { normal: "text-text", warning: "text-warning", overdue: "text-negative" } as const;
const PILL: Record<Risk, string> = {
  low: "bg-pill-neutral-bg text-pill-neutral-ink",
  medium: "bg-pill-warning-bg text-pill-warning-ink",
  high: "bg-pill-negative-bg text-pill-negative-ink",
};
const BARS: Record<Risk, number> = { low: 1, medium: 2, high: 3 };

function RiskPill({ risk }: { risk: Risk }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-pill px-2 text-small ${PILL[risk]}`}>
      <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3" fill="currentColor">
        {[0, 1, 2].map((i) => (
          <rect key={i} x={1 + i * 4} y={9 - i * 3} width="2" height={3 + i * 3} opacity={i < BARS[risk] ? 1 : 0.3} />
        ))}
      </svg>
      {risk[0].toUpperCase() + risk.slice(1)} risk
    </span>
  );
}

function Glyph({ state }: { state: NodeState }) {
  const d = state === "rejected" ? "M5 5l6 6M11 5l-6 6" : state === "done" || state === "signed" ? "M4.5 8.5l2.5 2.5L11.5 5.5" : null;
  if (!d) return null;
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="absolute -inset-0.5 size-4" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path d={d} />
    </svg>
  );
}

/** Flat list of nodes: quorum, levels (cut at the rejection), then the receipt or the end of the rail. */
function buildNodes(p: RailProps, timerLabel: string | undefined, percent: number): Node[] {
  const { quorum: q, levels, outcome, receipt } = p;
  const nodes: Node[] = [];
  const finished = outcome !== "pending";
  if (q.blind) {
    const count =
      q.voted === undefined ? null : q.reviewers === undefined ? `${q.voted} voted` : `${q.voted} of ${q.reviewers} voted`;
    nodes.push({
      key: "quorum",
      state: "open",
      word: "Voting. ",
      title: "Quorum",
      sub: (
        <>
          {count}
          {count && <br />}
          Vote hidden until round closes
        </>
      ),
      fill: 0,
    });
  } else {
    const met = q.approved >= q.need;
    nodes.push({
      key: "quorum",
      state: met ? "done" : finished ? "open" : "current",
      word: met ? "Done. " : finished ? "Stopped. " : "Current. ",
      title: `Quorum ${q.approved} of ${q.need}`,
      sub: q.revealedAt ? `Round revealed ${q.revealedAt}` : undefined,
      fill: met ? 100 : 0,
    });
  }

  const shown = outcome === "rejected" ? levels.filter((l) => l.state !== "open") : levels;
  for (const l of shown) {
    const timed = l.state === "current" && outcome === "pending";
    const stopped = finished && l.state === "current";
    nodes.push({
      key: `level-${l.level}`,
      state: stopped ? "open" : l.state,
      // An open level without a note already reads "Not started" as visible text.
      word: stopped
        ? "Stopped. "
        : { done: "Done. ", current: "Current. ", open: l.note ? "Not started. " : "", rejected: "Rejected. " }[l.state],
      title: `Level ${l.level} · ${l.role}`,
      sub: l.note ?? (l.state === "open" ? "Not started" : undefined),
      fill: l.state === "done" ? 100 : timed ? percent : 0,
      timer: timed ? timerLabel : undefined,
    });
  }

  if (outcome === "rejected") {
    nodes.push({ key: "end", state: "rejected", word: "", title: "Rejected", sub: "No receipt is issued", fill: 0 });
  } else if (outcome === "approved" && receipt) {
    nodes.push({
      key: "end",
      state: "signed",
      word: "Signed. ",
      title: "Receipt",
      sub: (
        <>
          <span className="font-mono">{receipt.hash}</span>
          {receipt.signedAt && <br />}
          {receipt.signedAt && `Signed ${receipt.signedAt}`}
        </>
      ),
      fill: 0,
    });
  } else {
    nodes.push({ key: "end", state: "unsigned", word: "Not signed. ", title: "Receipt", sub: "Signed when approved", fill: 0 });
  }
  return nodes;
}

export function DecisionRail(props: RailProps) {
  const { levels, outcome, risk, clock, headingLevel = 3 } = props;
  const now = useNow(clock?.now, clock?.dueAt);
  const left = clock ? timeLeft(clock.dueAt, now) : null;
  const used = clock ? windowUsed(clock.startedAt, clock.dueAt, now) : null;
  const current = levels.find((l) => l.state === "current");
  const next = current && levels.find((l) => l.level === current.level + 1);
  const timerLabel = current && used ? `Level ${current.level} timeout: ${used.usedMin} of ${used.windowMin} minutes used, ${used.percent} percent` : undefined;
  const nodes = buildNodes(props, timerLabel, used?.percent ?? 0);
  const policy = `Policy: ${levels.length} ${levels.length === 1 ? "level" : "levels"}`;

  return (
    <section aria-label="Decision progress" className="@container">
      {createElement(
        `h${headingLevel}`,
        { className: "m-0 mb-2.5 flex flex-wrap items-baseline justify-between gap-2 text-body font-semibold" },
        "Where it stands",
        <span key="policy" className="flex items-center gap-2 text-small font-normal text-muted">
          {policy}
          {risk && <RiskPill risk={risk} />}
        </span>,
      )}

      {outcome === "pending" && clock && (
        <div className="mb-4 flex flex-wrap items-baseline gap-x-3">
          {left ? (
            <>
              <span data-tone={left.tone} className={`text-kpi font-normal tabular-nums ${TONE[left.tone]}`}>
                {left.label}
              </span>
              {left.note && <span className="text-small font-medium text-warning">{left.note}</span>}
              <p className="m-0 max-w-(--measure) text-body text-muted">
                {current ? `until level ${current.level} times out${next ? ` and the request goes to the ${next.role.toLowerCase()}` : ""}. ` : ""}
                {used ? `${used.usedMin} of ${used.windowMin} min used, ` : ""}
                due {clockTime(clock.dueAt, now)}.
              </p>
            </>
          ) : (
            <p className="m-0 text-body text-muted">Due time unavailable</p>
          )}
        </div>
      )}

      <ol role="list" className="m-0 grid list-none grid-cols-(--n) p-0 @max-2xl:grid-cols-1" style={{ "--n": `repeat(${nodes.length}, minmax(0, 1fr))` } as CSSProperties}>
        {nodes.map((n, i) => (
          <li key={n.key} data-state={n.state} className="relative min-w-0 pt-6 pr-3 @max-2xl:pt-0 @max-2xl:pr-0 @max-2xl:pb-5 @max-2xl:pl-7 @max-2xl:last:pb-0">
            <span className={`absolute top-0 left-0 size-4 border-2 @max-2xl:top-0.5 ${DOT[n.state]}`}>
              <Glyph state={n.state} />
            </span>
            {i < nodes.length - 1 && (
              <span
                {...(n.timer ? { role: "img", "aria-label": n.timer } : { "aria-hidden": true })}
                className="absolute top-1.75 right-1 left-5 h-0.5 overflow-hidden rounded-pill bg-active @max-2xl:top-5 @max-2xl:right-auto @max-2xl:bottom-0 @max-2xl:left-1.75 @max-2xl:h-auto @max-2xl:w-0.5"
              >
                <span
                  style={{ "--fill": `${n.fill}%` } as CSSProperties}
                  className="block h-full w-(--fill) bg-accent transition-[width,height] duration-500 motion-reduce:transition-none @max-2xl:h-(--fill) @max-2xl:w-full"
                />
              </span>
            )}
            <div className="text-body leading-snug font-medium">
              {n.word && <span className="sr-only">{n.word}</span>}
              {n.title}
            </div>
            {n.sub && <div className={`mt-0.5 text-small wrap-anywhere ${n.state === "current" ? "text-text" : "text-muted"}`}>{n.sub}</div>}
          </li>
        ))}
      </ol>
    </section>
  );
}
