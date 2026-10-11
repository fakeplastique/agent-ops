import { ApiError, api } from "@/lib/api/client";

// Realtime feed (spec 5.3): WebSocket, then long-poll, then adaptive polling, all resuming from the last seq.
// Delivery is at-least-once, so every event is applied once by seq. Events are notifications to re-read, never state.
export type RealtimeEvent = { id: string; seq: number; type: string; data: Record<string, unknown>; created_at: string };
export type Transport = "websocket" | "long-poll" | "polling";
export type Connection = { status: "connecting" | "live" | "paused"; transport: Transport | null };

// The slice of WebSocket this client uses; tests fake it.
export type SocketLike = {
  onopen: (() => void) | null;
  onmessage: ((e: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  close: () => void;
};

export type ClientOptions = {
  /** replay is true for history delivered while catching up on connect: count it, do not flash "Updated" for it. */
  onEvents: (events: RealtimeEvent[], meta: { replay: boolean }) => void;
  onState?: (c: Connection) => void;
  /** The organization changed: seq numbers restart, so drop everything held for the old one. */
  onReset?: () => void;
  /** wss origin of the API (`NEXT_PUBLIC_WS_ORIGIN`); without it the ladder starts at long-poll. */
  wsOrigin?: string | null;
  openSocket?: (url: string) => SocketLike;
  request?: typeof api;
};

const PAGE = 100;
const LONG_POLL_WAIT_S = 25;
const LONG_POLL_TIMEOUT_MS = 40_000; // longer than the server's wait, so a quiet poll is not a timeout
const POLL_MIN_MS = 2_000;
const POLL_MAX_MS = 30_000;
const HIDDEN_POLL_MS = 30_000;
const WS_HEALTHY_MS = 5_000;
const SEEN_KEEP = 2_000;

const backoff = (n: number) => Math.min(1_000 * 2 ** Math.max(0, n - 1), POLL_MAX_MS);

type Feed = { organization_id: string; events: RealtimeEvent[]; after: number };

const isEvent = (v: unknown): v is RealtimeEvent =>
  !!v && typeof v === "object" && Number.isInteger((v as RealtimeEvent).seq) && typeof (v as RealtimeEvent).type === "string";

export function createRealtimeClient(o: ClientOptions) {
  const request = o.request ?? api;
  const openSocket =
    o.openSocket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  let ac = new AbortController();
  let running = false;
  let cursor = 0;
  let org: string | null = null;
  let seen = new Set<number>();
  let socket: SocketLike | null = null;
  let conn: Connection = { status: "connecting", transport: null };

  const set = (status: Connection["status"], transport: Transport | null = conn.transport) => {
    if (conn.status === status && conn.transport === transport) return;
    conn = { status, transport };
    o.onState?.(conn);
  };

  // Each seq is applied once, even if it arrives twice or after a later one; the cursor only moves forward.
  const apply = (events: RealtimeEvent[], replay: boolean) => {
    const fresh = [...new Map(events.filter((e) => !seen.has(e.seq)).map((e) => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq);
    for (const e of fresh) {
      seen.add(e.seq);
      cursor = Math.max(cursor, e.seq);
    }
    if (seen.size > SEEN_KEEP) seen = new Set([...seen].filter((s) => s > cursor - SEEN_KEEP));
    if (fresh.length) o.onEvents(fresh, { replay });
  };

  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const t = setTimeout(resolve, ms);
      ac.signal.addEventListener("abort", () => (clearTimeout(t), resolve()), { once: true });
    });

  const resetOrg = (next: string | null) => {
    cursor = 0;
    seen = new Set();
    org = next;
    o.onReset?.();
  };

  const fetchFeed = async (wait: number, timeoutMs?: number): Promise<Feed> => {
    const q = `after=${cursor}&wait=${wait}&limit=${PAGE}`;
    return request<Feed>(`/events?${q}`, { signal: ac.signal, timeoutMs });
  };

  // Returns how long to wait before the next try, or null when the client must stop (not signed in / no access).
  const onFailure = (err: unknown): number | null => {
    if (ac.signal.aborted) return null;
    if (err instanceof ApiError) {
      if (err.status === 401 || err.status === 403) return (set("paused"), null);
      if (err.status === 404) resetOrg(null); // the organization changed under the poll: the next answer decides
      if (err.status === 429) return Math.max(1, err.retryAfter ?? 1) * 1000;
    }
    return 0; // the caller applies its own backoff
  };

  // History first, so nothing replayed is mistaken for news: pages of `wait=0` until one is not full.
  const catchUp = async (): Promise<boolean> => {
    let failures = 0;
    while (running) {
      try {
        const feed = await fetchFeed(0);
        if (org !== null && feed.organization_id !== org) {
          resetOrg(feed.organization_id);
          continue;
        }
        org = feed.organization_id;
        apply(feed.events, true);
        cursor = Math.max(cursor, feed.after);
        failures = 0;
        if (feed.events.length < PAGE) return true;
      } catch (err) {
        const wait = onFailure(err);
        if (wait === null) return false;
        set("paused");
        await sleep(wait || backoff(++failures));
      }
    }
    return false;
  };

  // One live poll. Returns false if the organization changed (the caller must catch up again).
  const live = async (wait: number, transport: Transport, timeoutMs?: number): Promise<boolean> => {
    const feed = await fetchFeed(wait, timeoutMs);
    if (org !== null && feed.organization_id !== org) {
      resetOrg(feed.organization_id);
      return false;
    }
    org = feed.organization_id;
    cursor = Math.max(cursor, feed.after);
    set("live", transport);
    apply(feed.events, false);
    return true;
  };

  // Resolves with whether the socket opened and how long it stayed up.
  const runSocket = async (): Promise<{ opened: boolean; upMs: number } | "ticket-failed"> => {
    let url: string;
    try {
      const { ticket } = await request<{ ticket: string }>("/realtime/ticket", { method: "POST", signal: ac.signal });
      url = `${o.wsOrigin}/ws?ticket=${encodeURIComponent(ticket)}&after=${cursor}`;
    } catch (err) {
      if (onFailure(err) === null) throw err;
      return "ticket-failed";
    }
    return new Promise((resolve) => {
      const ws = openSocket(url);
      socket = ws;
      let openedAt = 0;
      const done = () => {
        if (socket === ws) socket = null;
        resolve({ opened: openedAt > 0, upMs: openedAt ? Date.now() - openedAt : 0 });
      };
      ws.onopen = () => {
        openedAt = Date.now();
        set("live", "websocket");
      };
      ws.onmessage = (e) => {
        try {
          const body = JSON.parse(String(e.data));
          apply((Array.isArray(body?.events) ? body.events : [body]).filter(isEvent), false);
        } catch {
          /* a malformed frame is ignored; the next one carries its own seq */
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = done;
      ac.signal.addEventListener("abort", () => ws.close(), { once: true });
    });
  };

  async function run() {
    let rung: Transport = o.wsOrigin ? "websocket" : "long-poll";
    let failures = 0;
    let interval = POLL_MIN_MS;
    while (running) {
      if (!(await catchUp())) return;
      let again = true; // stay on the ladder until the organization changes
      while (running && again) {
        try {
          if (rung === "websocket") {
            const r = await runSocket();
            if (r === "ticket-failed" || !r.opened || r.upMs < WS_HEALTHY_MS) failures++;
            else failures = 0;
            if (failures >= 2) (rung = "long-poll", (failures = 0));
            else if (running) {
              set("paused");
              await sleep(backoff(failures || 1));
            }
          } else if (rung === "long-poll") {
            again = await live(LONG_POLL_WAIT_S, "long-poll", LONG_POLL_TIMEOUT_MS);
            failures = 0;
          } else {
            const before = cursor;
            again = await live(0, "polling");
            interval = cursor > before ? POLL_MIN_MS : Math.min(interval * 1.5, POLL_MAX_MS);
            await sleep(document.hidden ? Math.max(interval, HIDDEN_POLL_MS) : interval);
          }
        } catch (err) {
          const wait = onFailure(err);
          if (wait === null) return;
          set("paused");
          failures++;
          if (rung === "long-poll" && failures >= 3) (rung = "polling", (failures = 0));
          if (rung === "polling") interval = Math.min(interval * 2, POLL_MAX_MS);
          await sleep(wait || (rung === "polling" ? interval : backoff(failures)));
        }
      }
    }
  }

  return {
    start() {
      if (running) return;
      running = true;
      ac = new AbortController();
      void run();
    },
    stop() {
      running = false;
      ac.abort();
      socket?.close();
    },
    cursor: () => cursor,
    connection: () => conn,
  };
}

export type RealtimeClient = ReturnType<typeof createRealtimeClient>;
