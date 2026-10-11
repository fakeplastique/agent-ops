import { ApiError, api } from "@/lib/api/client";

// Realtime feed (spec 5.3): WebSocket, then long-poll, then adaptive polling, all resuming from the last seq.
// Delivery is at-least-once, so every event is applied once by seq. Events are notifications to re-read, never state.
export type RealtimeEvent = { id: string; seq: number; type: string; data: Record<string, unknown>; created_at: string };
export type Transport = "websocket" | "long-poll" | "polling";
// "stopped" is not "reconnecting": the session ended (401) or access was lost (403) and polling again would not help.
export type Connection = {
  status: "connecting" | "live" | "paused" | "stopped";
  transport: Transport | null;
  reason?: "unauthenticated" | "forbidden";
};

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
  /** 0..1, for retry jitter; tests pin it. */
  random?: () => number;
};

const PAGE = 100;
const LONG_POLL_WAIT_S = 25;
const LONG_POLL_TIMEOUT_MS = 40_000; // longer than the server's wait, so a quiet poll is not a timeout
const POLL_MIN_MS = 2_000;
const POLL_MAX_MS = 30_000;
const HIDDEN_POLL_MS = 30_000;
const WS_HEALTHY_MS = 5_000;
const WS_PROBE_MS = 60_000; // a socket silent this long is checked against the feed
const PROMOTE_MS = 5 * 60_000; // a demoted client tries the rung above again after this
const SEEN_KEEP = 2_000;

type Feed = { organization_id: string; events: RealtimeEvent[]; after: number };

const isEvent = (v: unknown): v is RealtimeEvent =>
  !!v && typeof v === "object" && Number.isInteger((v as RealtimeEvent).seq) && typeof (v as RealtimeEvent).type === "string";

export function createRealtimeClient(o: ClientOptions) {
  const request = o.request ?? api;
  const openSocket = o.openSocket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  const rnd = o.random ?? Math.random;
  // Between half and the full delay, so tabs and clients do not retry on one beat after an outage.
  const jitter = (ms: number) => ms * (0.5 + 0.5 * rnd());
  const backoff = (n: number) => jitter(Math.min(1_000 * 2 ** Math.max(0, n - 1), POLL_MAX_MS));
  const retryAfter = (seconds: number) => Math.max(1, seconds) * 1_000 * (1 + 0.25 * rnd());

  let ac = new AbortController();
  let running = false;
  let cursor = 0;
  let org: string | null = null;
  let seen = new Set<number>();
  let socket: SocketLike | null = null;
  let conn: Connection = { status: "connecting", transport: null };
  let ticketWait: number | null = null;
  let needCatchUp = false;

  const set = (status: Connection["status"], transport: Transport | null = conn.transport, reason?: Connection["reason"]) => {
    if (conn.status === status && conn.transport === transport && conn.reason === reason) return;
    conn = { status, transport, reason };
    o.onState?.(conn);
  };

  // Each seq is applied once, even if it arrives twice or after a later one; the cursor only moves forward.
  const apply = (events: RealtimeEvent[], replay: boolean): number => {
    const fresh = [...new Map(events.filter((e) => !seen.has(e.seq)).map((e) => [e.seq, e])).values()].sort((a, b) => a.seq - b.seq);
    for (const e of fresh) {
      seen.add(e.seq);
      cursor = Math.max(cursor, e.seq);
    }
    if (seen.size > SEEN_KEEP) seen = new Set([...seen].filter((s) => s > cursor - SEEN_KEEP));
    if (fresh.length) o.onEvents(fresh, { replay });
    return fresh.length;
  };

  // Resolves after ms or when the client is stopped; the abort listener never outlives the wait.
  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const { signal } = ac;
      const done = () => {
        clearTimeout(t);
        signal.removeEventListener("abort", done);
        resolve();
      };
      const t = setTimeout(done, ms);
      signal.addEventListener("abort", done, { once: true });
    });

  // A hidden tab holds no long-poll: wait for it to be shown (or the client to stop).
  const whenVisible = () =>
    new Promise<void>((resolve) => {
      const { signal } = ac;
      const done = () => {
        if (document.hidden && !signal.aborted) return;
        document.removeEventListener("visibilitychange", done);
        signal.removeEventListener("abort", done);
        resolve();
      };
      if (!document.hidden) return resolve();
      document.addEventListener("visibilitychange", done);
      signal.addEventListener("abort", done, { once: true });
    });

  const resetOrg = (next: string | null) => {
    cursor = 0;
    seen = new Set();
    org = next;
    needCatchUp = true;
    o.onReset?.();
  };

  const fetchFeed = async (wait: number, timeoutMs?: number): Promise<Feed> => {
    const q = `after=${cursor}&wait=${wait}&limit=${PAGE}`;
    return request<Feed>(`/events?${q}`, { signal: ac.signal, timeoutMs });
  };

  // Returns how long to wait before the next try, or null when the client must stop.
  // 401/403 end the run (running=false, state "stopped"); polling again would not help.
  const onFailure = (err: unknown): number | null => {
    if (ac.signal.aborted) return null;
    if (err instanceof ApiError) {
      if (err.status === 401 || err.status === 403) {
        running = false;
        set("stopped", null, err.status === 401 ? "unauthenticated" : "forbidden");
        return null;
      }
      if (err.status === 404) resetOrg(null); // the organization changed under the poll: the next answer decides
      if (err.status === 429) return retryAfter(err.retryAfter ?? 1);
    }
    return 0; // the caller applies its own backoff
  };

  // History first, so nothing replayed is mistaken for news: pages of `wait=0` until one is not full.
  const catchUp = async (): Promise<boolean> => {
    let failures = 0;
    needCatchUp = false;
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
        if (feed.events.length < PAGE) {
          needCatchUp = false;
          return true;
        }
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
    ticketWait = null;
    let url: string;
    try {
      const { ticket } = await request<{ ticket: string }>("/realtime/ticket", { method: "POST", signal: ac.signal });
      url = `${o.wsOrigin}/ws?ticket=${encodeURIComponent(ticket)}&after=${cursor}`;
    } catch (err) {
      const wait = onFailure(err);
      if (wait === null) throw err;
      ticketWait = wait || null; // a 429's Retry-After
      return "ticket-failed";
    }
    return new Promise((resolve) => {
      const { signal } = ac;
      const ws = openSocket(url);
      socket = ws;
      let openedAt = 0;
      let lastFrame = Date.now();
      let checking = false;
      // No heartbeat is defined, so a half-open socket looks like a quiet one: ask the feed what it missed.
      const probe = setInterval(async () => {
        if (!openedAt || checking || Date.now() - lastFrame < WS_PROBE_MS) return;
        checking = true;
        try {
          const feed = await fetchFeed(0);
          if (org !== null && feed.organization_id !== org) {
            resetOrg(feed.organization_id);
            ws.close();
          } else if (apply(feed.events, false) > 0) {
            cursor = Math.max(cursor, feed.after);
            ws.close(); // it missed events: reconnect and replay
          }
        } catch {
          /* a failed probe says nothing about the socket */
        } finally {
          checking = false;
        }
      }, WS_PROBE_MS);
      const onAbort = () => ws.close();
      signal.addEventListener("abort", onAbort, { once: true });
      ws.onopen = () => {
        openedAt = Date.now();
        lastFrame = openedAt;
        set("live", "websocket");
      };
      ws.onmessage = (e) => {
        lastFrame = Date.now();
        try {
          const body = JSON.parse(String(e.data));
          apply((Array.isArray(body?.events) ? body.events : [body]).filter(isEvent), false);
        } catch {
          /* a malformed frame is ignored; the next one carries its own seq */
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        clearInterval(probe);
        signal.removeEventListener("abort", onAbort);
        if (socket === ws) socket = null;
        resolve({ opened: openedAt > 0, upMs: openedAt ? Date.now() - openedAt : 0 });
      };
    });
  };

  async function run() {
    const ladder: Transport[] = o.wsOrigin ? ["websocket", "long-poll", "polling"] : ["long-poll", "polling"];
    let rung = 0;
    let failures = 0;
    let probing = false; // trying the rung above after a quiet interval
    let demotedAt = 0;
    let interval = POLL_MIN_MS;
    const demote = () => {
      if (rung < ladder.length - 1) rung++;
      demotedAt = Date.now();
      failures = 0;
      probing = false;
    };
    while (running) {
      if (!(await catchUp())) return;
      let again = true; // stay on the ladder until the organization changes
      while (running && again) {
        if (rung > 0 && !probing && Date.now() - demotedAt >= PROMOTE_MS) {
          rung--;
          probing = true;
          demotedAt = Date.now();
          failures = 0;
        }
        try {
          if (ladder[rung] === "websocket") {
            const r = await runSocket();
            if (needCatchUp) again = false;
            if (r !== "ticket-failed" && r.opened && r.upMs >= WS_HEALTHY_MS) {
              failures = 0;
              probing = false;
              set("connecting"); // a rotated socket reconnects; that is not an outage
              await sleep(backoff(1));
            } else if (probing || ++failures >= 2) demote(); // a failed promotion falls back without a banner
            else {
              set("paused");
              await sleep(ticketWait ?? backoff(failures));
            }
          } else if (ladder[rung] === "long-poll") {
            if (document.hidden) {
              await whenVisible();
              continue;
            }
            // Probe with wait=0 until live so "paused" clears at once instead of after a 25 s wait.
            again = await live(conn.status === "live" ? LONG_POLL_WAIT_S : 0, "long-poll", LONG_POLL_TIMEOUT_MS);
            failures = 0;
            probing = false;
          } else {
            const before = cursor;
            again = await live(0, "polling");
            failures = 0;
            probing = false;
            interval = cursor > before ? POLL_MIN_MS : Math.min(interval * 1.5, POLL_MAX_MS);
            await sleep(document.hidden ? Math.max(jitter(interval), HIDDEN_POLL_MS) : jitter(interval));
          }
        } catch (err) {
          const wait = onFailure(err);
          if (wait === null) return;
          if (probing) {
            demote();
            continue;
          }
          set("paused");
          failures++;
          if (ladder[rung] === "long-poll" && failures >= 3) demote();
          if (ladder[rung] === "polling") interval = Math.min(interval * 2, POLL_MAX_MS);
          await sleep(wait || (ladder[rung] === "polling" ? jitter(interval) : backoff(failures)));
        }
      }
    }
  }

  return {
    start() {
      if (running) return;
      running = true;
      ac = new AbortController();
      set("connecting", null);
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
