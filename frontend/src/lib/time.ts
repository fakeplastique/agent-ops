export type Tone = "normal" | "warning" | "overdue";
export type TimeLeft = { ms: number; label: string; tone: Tone; note?: string };

const MIN = 60_000;
/** Under this much time left the figure turns to the warning colour and says so in text. */
export const SOON_MS = 15 * MIN;

const ms = (at: string | number | Date) => (at instanceof Date ? at.getTime() : typeof at === "number" ? at : Date.parse(at));

/** Whole minutes as "49 min", "1 h 05 min" or "1 d 2 h". */
function span(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
  return `${Math.floor(minutes / 1440)} d ${Math.floor((minutes % 1440) / 60)} h`;
}

/** Time left to dueAt, rounded down so it never promises more than there is. Null if dueAt is not a date. */
export function timeLeft(dueAt: string | number | Date, now: number): TimeLeft | null {
  const due = ms(dueAt);
  if (Number.isNaN(due)) return null;
  const left = due - now;
  if (left <= 0) {
    const over = Math.floor(-left / MIN);
    return { ms: left, tone: "overdue", label: over < 1 ? "Overdue" : `Overdue by ${span(over)}` };
  }
  const minutes = Math.floor(left / MIN);
  if (minutes < 1) return { ms: left, tone: "warning", note: "Due soon", label: "Under 1 min left" };
  const warn = left < SOON_MS;
  return { ms: left, tone: warn ? "warning" : "normal", note: warn ? "Due soon" : undefined, label: `${span(minutes)} left` };
}

/** Window and used minutes, with used = window - left (left floored as in timeLeft) so the two figures always add up. */
export function windowUsed(startedAt: string | number | Date, dueAt: string | number | Date, now: number) {
  const start = ms(startedAt);
  const due = ms(dueAt);
  const windowMin = Math.round((due - start) / MIN);
  if (Number.isNaN(windowMin) || windowMin < 1) return null;
  const leftMin = Math.floor(Math.max(due - now, 0) / MIN);
  const usedMin = Math.min(Math.max(windowMin - leftMin, 0), windowMin);
  return { usedMin, windowMin, percent: Math.round((100 * usedMin) / windowMin) };
}

/** Next instant the floored minute count changes: the delay to the next whole-minute boundary of dueAt, or null. */
export function msToNextMinute(dueAt: string | number | Date, now: number): number | null {
  const due = ms(dueAt);
  if (Number.isNaN(due)) return null;
  return (((due - now) % MIN) + MIN) % MIN;
}

/** Local HH:MM, prefixed with the date ("4 Nov 20:00") when it is not the same local day as now. Empty if invalid. */
export function clockTime(at: string | number | Date, now: number): string {
  const t = ms(at);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date(now).toDateString()) return hm;
  return `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ${hm}`;
}
