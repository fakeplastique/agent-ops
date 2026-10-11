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

/** The window and how much of it is used, from the same two instants as timeLeft. Null if not a positive window. */
export function windowUsed(startedAt: string | number | Date, dueAt: string | number | Date, now: number) {
  const start = ms(startedAt);
  const due = ms(dueAt);
  const window = due - start;
  if (Number.isNaN(window) || window <= 0) return null;
  const used = Math.min(Math.max(now - start, 0), window);
  return {
    usedMin: Math.floor(used / MIN),
    windowMin: Math.round(window / MIN),
    percent: Math.round((100 * used) / window),
  };
}

/** Local HH:MM, or "" for an invalid date. */
export function clockTime(at: string | number | Date): string {
  const t = ms(at);
  return Number.isNaN(t) ? "" : new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
