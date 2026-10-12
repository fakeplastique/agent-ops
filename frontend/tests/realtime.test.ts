import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { type RealtimeEvent, type SocketLike, createRealtimeClient } from "@/lib/realtime/client";

const ev = (seq: number, type = "decision.created"): RealtimeEvent => ({
  id: `e${seq}`,
  seq,
  type,
  data: {},
  created_at: "2026-01-01T00:00:00Z",
});
const net = () => new ApiError(0, "network", "No response from the server");

type Reply = { events?: RealtimeEvent[]; after?: number; org?: string } | Error;

// Scripted API: each /events call takes the next reply (a quiet long-poll never answers until aborted).
function fakeApi(replies: Reply[], extra: (path: string) => unknown = () => undefined, quiet = false) {
  const calls: string[] = [];
  const times: number[] = [];
  const request = vi.fn(async (path: string, opts: { signal?: AbortSignal } = {}) => {
    calls.push(path);
    times.push(Date.now());
    const special = extra(path);
    if (special !== undefined) return special;
    const r = replies.shift();
    if (r instanceof Error) throw r;
    if (!r && quiet) {
      // A quiet server: a poll with wait=N answers empty after N seconds.
      await new Promise((done) => setTimeout(done, Number(/wait=(\d+)/.exec(path)?.[1] ?? 0) * 1000));
      return { organization_id: "o1", events: [], after: Number(/after=(\d+)/.exec(path)?.[1] ?? 0) };
    }
    if (!r) return new Promise((_, reject) => opts.signal?.addEventListener("abort", () => reject(opts.signal?.reason)));
    const events = r.events ?? [];
    return { organization_id: r.org ?? "o1", events, after: r.after ?? Math.max(0, ...events.map((e) => e.seq)) };
  });
  return { request: request as never, calls, times };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(document, "hidden");
});

const run = async (ms = 0) => vi.advanceTimersByTimeAsync(ms);

describe("realtime client", () => {
  it("duplicate and out-of-order frames applied once by seq", async () => {
    const { request } = fakeApi([
      { events: [] }, // catch-up: nothing to replay
      { events: [ev(2), ev(1), ev(2)] },
      { events: [ev(2), ev(3)] },
      { events: [ev(5)] },
      { events: [ev(4), ev(5)] }, // 4 arrives after 5 and is still news
    ]);
    const got: number[][] = [];
    const c = createRealtimeClient({ request, onEvents: (e) => got.push(e.map((x) => x.seq)) });
    c.start();
    await run(1);
    expect(got).toEqual([[1, 2], [3], [5], [4]]);
    expect(c.cursor()).toBe(5);
    c.stop();
  });

  it("history on connect is flagged as replay, later events are not", async () => {
    const { request } = fakeApi([{ events: [ev(1), ev(2)] }, { events: [ev(3)] }]);
    const seen: [number, boolean][] = [];
    const c = createRealtimeClient({ request, onEvents: (e, m) => e.forEach((x) => seen.push([x.seq, m.replay])) });
    c.start();
    await run(1);
    expect(seen).toEqual([[1, true], [2, true], [3, false]]);
    c.stop();
  });

  it("fallback ladder WS -> long-poll -> adaptive polling, resumes from last seq", async () => {
    const urls: string[] = [];
    let n = 0;
    const openSocket = (url: string) => {
      urls.push(url);
      const s: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, close: () => s.onclose?.() };
      const first = n++ === 0;
      void Promise.resolve().then(() => {
        if (first) {
          s.onopen?.(); // opens, delivers seq 5 (twice), then drops at once: not a healthy connection
          s.onmessage?.({ data: JSON.stringify(ev(5)) });
          s.onmessage?.({ data: JSON.stringify({ events: [ev(5)] }) });
        }
        s.onclose?.(); // the second socket never opens
      });
      return s;
    };
    const { request, calls } = fakeApi(
      [{ events: [] }, net(), net(), net(), { events: [ev(6)] }],
      (p) => (p === "/realtime/ticket" ? { ticket: "t1" } : undefined),
    );
    const got: number[] = [];
    const c = createRealtimeClient({ request, openSocket, wsOrigin: "wss://api.test", onEvents: (e) => got.push(...e.map((x) => x.seq)) });
    c.start();
    await run(120_000);
    expect(urls).toEqual(["wss://api.test/ws?ticket=t1&after=0", "wss://api.test/ws?ticket=t1&after=5"]);
    const feed = calls.filter((p) => p.startsWith("/events"));
    expect(feed.slice(1, 4)).toEqual(Array(3).fill("/events?after=5&wait=0&limit=100")); // long-poll resumes at 5, probing first
    expect(feed[4]).toBe("/events?after=5&wait=0&limit=100"); // then short polling, same cursor
    expect(got).toEqual([5, 6]); // 5 once, never twice
    expect(c.connection()).toEqual({ status: "live", transport: "polling" });
    c.stop();
  });

  it("offline: shows paused, keeps polling, and recovers by itself", async () => {
    const states: string[] = [];
    const replies: Reply[] = [{ events: [] }, ...Array(6).fill(net())];
    const { request, calls } = fakeApi(replies, () => undefined, true);
    const c = createRealtimeClient({ request, onEvents: () => {}, onState: (s) => states.push(s.status) });
    c.start();
    await run(60_000);
    expect(states).toContain("paused");
    expect(calls.length).toBeGreaterThanOrEqual(5); // kept trying while down
    await run(60_000); // the failures are used up: the next poll answers
    expect(c.connection().status).toBe("live");
    c.stop();
  });

  it("a 401 stops the client instead of hammering the API", async () => {
    const { request, calls } = fakeApi([new ApiError(401, "unauthenticated", "x")]);
    const c = createRealtimeClient({ request, onEvents: () => {} });
    c.start();
    await run(120_000);
    expect(calls).toHaveLength(1);
    c.stop();
  });

  // seq is per organization: a cursor kept across an org switch would skip the new org's first events.
  it("an organization change resets the cursor and tells the app to drop its state", async () => {
    const { request, calls } = fakeApi([
      { events: [ev(7)] },
      { events: [ev(8)] },
      { events: [ev(1)], org: "o2" }, // another org's feed
      { events: [ev(1)], org: "o2" }, // catch-up from 0 in the new org
    ]);
    const onReset = vi.fn();
    const got: number[] = [];
    const c = createRealtimeClient({ request, onReset, onEvents: (e) => got.push(...e.map((x) => x.seq)) });
    c.start();
    await run(1);
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(calls[3]).toBe("/events?after=0&wait=0&limit=100"); // catch-up restarts from 0
    expect(got).toEqual([7, 8, 1]);
    c.stop();
  });

  // Without jitter every tab and every client retries on the same beat after an outage.
  it("retries are jittered between half and the full backoff", async () => {
    const gap = async (random: number) => {
      const { request, times } = fakeApi([net(), net()]);
      const c = createRealtimeClient({ request, onEvents: () => {}, random: () => random });
      c.start();
      await run(5_000);
      c.stop();
      return times[1] - times[0];
    };
    expect(await gap(0)).toBe(500);
    expect(await gap(1)).toBe(1000);
  });

  it("honours Retry-After on a ticket 429 before asking again", async () => {
    const tickets: number[] = [];
    const { request } = fakeApi([{ events: [] }], (p) => {
      if (p !== "/realtime/ticket") return undefined;
      tickets.push(Date.now());
      throw new ApiError(429, "rate_limited", "x", {}, null, 40);
    });
    const c = createRealtimeClient({ request, wsOrigin: "wss://api.test", random: () => 0, onEvents: () => {} });
    c.start();
    await run(41_000);
    expect(tickets[1] - tickets[0]).toBe(40_000);
    c.stop();
  });

  // A multi-tab cap or a blocked WebSocket must not pin a session to polling until reload.
  it("re-promotes to long-poll after a quiet interval", async () => {
    const { request, calls } = fakeApi([{ events: [] }, net(), net(), net()], () => undefined, true);
    const c = createRealtimeClient({ request, onEvents: () => {} });
    c.start();
    await run(240_000);
    expect(calls.filter((p) => p.includes("wait=25"))).toHaveLength(0); // demoted to short polling
    await run(120_000);
    expect(calls.some((p) => p.includes("wait=25"))).toBe(true);
    expect(c.connection().status).toBe("live");
    c.stop();
  });

  it("a failed promotion probe falls back quietly, without a 'paused' banner", async () => {
    const states: string[] = [];
    const replies: Reply[] = [{ events: [] }, net(), net(), net()];
    const { request } = fakeApi(replies, (p) => {
      if (p.includes("wait=25")) throw net(); // the promotion probe (a long-poll) always fails; polling carries on
      return undefined;
    }, true);
    const c = createRealtimeClient({ request, onEvents: () => {}, onState: (s) => states.push(s.status) });
    c.start();
    await run(240_000); // demoted to polling, live again
    const before = states.length;
    await run(120_000);
    expect(states.slice(before)).not.toContain("paused");
    c.stop();
  });

  it("a hidden tab does not hold a long-poll; it resumes when visible", async () => {
    let hidden = true;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    const { request, calls } = fakeApi([{ events: [] }], () => undefined, true);
    const c = createRealtimeClient({ request, onEvents: () => {} });
    c.start();
    await run(60_000);
    expect(calls).toHaveLength(1); // only the catch-up
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    await run(1);
    expect(calls[1]).toContain("wait=0");
    c.stop();
  });

  // After "signed out" the client is stopped, says so, and can be started again; it is not "reconnecting".
  it("401 and 403 end in a stopped state with a reason, and start() works again", async () => {
    for (const [status, reason] of [[401, "unauthenticated"], [403, "forbidden"]] as const) {
      const { request, calls } = fakeApi([new ApiError(status, "x", "x"), new ApiError(status, "x", "x")]);
      const c = createRealtimeClient({ request, onEvents: () => {} });
      c.start();
      await run(10_000);
      expect(c.connection()).toMatchObject({ status: "stopped", reason });
      c.start();
      await run(10_000);
      expect(calls).toHaveLength(2);
      c.stop();
    }
  });

  it("recovery clears 'paused' at once: the first poll after a failure does not wait", async () => {
    const { request, calls } = fakeApi([{ events: [] }, net()], () => undefined, true);
    const c = createRealtimeClient({ request, onEvents: () => {}, random: () => 1 });
    c.start();
    await run(1_500);
    expect(calls[2]).toContain("wait=0"); // the retry after the failure is a quick probe
    expect(c.connection().status).toBe("live");
    c.stop();
  });

  it("a healthy socket closing and reconnecting never shows 'paused'", async () => {
    const states: string[] = [];
    let n = 0;
    const openSocket = () => {
      const s: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, close: () => s.onclose?.() };
      const first = n++ === 0;
      void Promise.resolve().then(() => {
        s.onopen?.();
        if (first) setTimeout(() => s.onclose?.(), 6_000); // up for 6 s, then rotated by the server
      });
      return s;
    };
    const { request } = fakeApi([{ events: [] }], (p) => (p === "/realtime/ticket" ? { ticket: "t" } : undefined));
    const c = createRealtimeClient({ request, openSocket, wsOrigin: "wss://api.test", onEvents: () => {}, onState: (s) => states.push(s.status) });
    c.start();
    await run(12_000);
    expect(n).toBe(2);
    expect(states).not.toContain("paused");
    c.stop();
  });

  // A half-open socket delivers nothing and never closes; a quiet-socket probe of the feed finds what it missed.
  it("a silent socket is probed against the feed; missed events reconnect it", async () => {
    let opened = 0;
    const openSocket = () => {
      opened++;
      const s: SocketLike = { onopen: null, onmessage: null, onclose: null, onerror: null, close: () => s.onclose?.() };
      void Promise.resolve().then(() => s.onopen?.());
      return s;
    };
    const { request } = fakeApi([{ events: [] }, { events: [ev(9)] }], (p) => (p === "/realtime/ticket" ? { ticket: "t" } : undefined));
    const got: number[] = [];
    const c = createRealtimeClient({ request, openSocket, wsOrigin: "wss://api.test", onEvents: (e) => got.push(...e.map((x) => x.seq)) });
    c.start();
    await run(70_000);
    expect(got).toEqual([9]);
    expect(opened).toBe(2);
    c.stop();
  });

  // NEXT_PUBLIC_* is inlined at build time: without a build arg the image never has a WebSocket origin.
  it("the web image build receives NEXT_PUBLIC_WS_ORIGIN", () => {
    expect(readFileSync(`${__dirname}/../Dockerfile`, "utf8")).toMatch(/ARG NEXT_PUBLIC_WS_ORIGIN\nENV NEXT_PUBLIC_WS_ORIGIN=\$NEXT_PUBLIC_WS_ORIGIN/);
    expect(readFileSync(`${__dirname}/../../compose.yaml`, "utf8")).toContain("NEXT_PUBLIC_WS_ORIGIN");
  });
});
