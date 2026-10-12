import type { Risk, Status } from "@/lib/api/types";

const base = "inline-flex h-5 flex-none items-center gap-1 rounded-pill px-2 text-small font-medium whitespace-nowrap";
const RISK: Record<Risk, string> = {
  low: "bg-pill-neutral-bg text-pill-neutral-ink",
  medium: "bg-pill-warning-bg text-pill-warning-ink",
  high: "bg-pill-negative-bg text-pill-negative-ink",
};
const BARS: Record<Risk, number> = { low: 1, medium: 2, high: 3 };

/** Risk with text and a bar glyph, never colour alone. */
export function RiskPill({ risk }: { risk: Risk }) {
  return (
    <span className={`${base} ${RISK[risk]}`}>
      <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3" fill="currentColor">
        {[0, 1, 2].map((i) => (
          <rect key={i} x={1 + i * 4} y={9 - i * 3} width="2" height={3 + i * 3} opacity={i < BARS[risk] ? 1 : 0.3} />
        ))}
      </svg>
      {risk[0].toUpperCase() + risk.slice(1)} risk
    </span>
  );
}

const STATUS: Record<Status, { label: string; tone: string; glyph: string }> = {
  pending_votes: { label: "Voting", tone: "bg-pill-neutral-bg text-pill-neutral-ink", glyph: "M8 4v4l2.5 1.5" },
  pending_discussion: { label: "Discussion", tone: "bg-pill-neutral-bg text-pill-neutral-ink", glyph: "M3.5 4.5h9v5h-5l-2.5 2v-2h-1.5z" },
  pending_approval: { label: "Waiting for approval", tone: "bg-pill-warning-bg text-pill-warning-ink", glyph: "M8 4v4l2.5 1.5" },
  approved: { label: "Approved", tone: "bg-pill-positive-bg text-pill-positive-ink", glyph: "M4.5 8.5l2.5 2.5 4.5-5" },
  rejected: { label: "Rejected", tone: "bg-pill-negative-bg text-pill-negative-ink", glyph: "M5 5l6 6M11 5l-6 6" },
  expired: { label: "Expired", tone: "bg-pill-neutral-bg text-pill-neutral-ink", glyph: "M8 4v4l2.5 1.5" },
  cancelled: { label: "Withdrawn", tone: "bg-pill-neutral-bg text-pill-neutral-ink", glyph: "M4.5 8h7" },
};

export const statusLabel = (s: Status) => STATUS[s]?.label ?? s;

export function StatusPill({ status }: { status: Status }) {
  const s = STATUS[status] ?? { label: status, tone: STATUS.pending_votes.tone, glyph: "" };
  return (
    <span className={`${base} ${s.tone}`}>
      <svg aria-hidden="true" viewBox="0 0 16 16" className="size-3 fill-none stroke-current stroke-[1.75]">
        <path d={s.glyph} />
      </svg>
      {s.label}
    </span>
  );
}
