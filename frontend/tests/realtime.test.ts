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
function fakeApi(replies: Reply[], extra: (path: string) => unknown = () => undefined) {
  const calls: string[] = [];
  const request = vi.fn(async (path: string, opts: { signal?: AbortSignal } = {}) => {
    calls.push(path);
    const special = extra(path);
    if (special !== undefined) return special;
    const r = replies.shift();
    if (r instanceof Error) throw r;
    if (!r) return new Promise((_, reject) => opts.signal?.addEventListener("abort", () => reject(opts.signal?.reason)));
    const events = r.events ?? [];
    return { organization_id: r.org ?? "o1", events, after: r.after ?? Math.max(0, ...events.map((e) => e.seq)) };
  });
  return { request: request as never, calls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

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
    expect(feed.slice(1, 4)).toEqual(Array(3).fill("/events?after=5&wait=25&limit=100")); // long-poll resumes at 5
    expect(feed[4]).toBe("/events?after=5&wait=0&limit=100"); // then short polling, same cursor
    expect(got).toEqual([5, 6]); // 5 once, never twice
    expect(c.connection()).toEqual({ status: "live", transport: "polling" });
    c.stop();
  });

  it("offline: shows paused, keeps polling, and recovers by itself", async () => {
    const states: string[] = [];
    const replies: Reply[] = [{ events: [] }, ...Array(6).fill(net())];
    const { request, calls } = fakeApi(replies);
    const c = createRealtimeClient({ request, onEvents: () => {}, onState: (s) => states.push(s.status) });
    c.start();
    await run(60_000);
    expect(states).toContain("paused");
    expect(calls.length).toBeGreaterThanOrEqual(5); // kept trying while down
    replies.push({ events: [ev(1)] });
    await run(60_000);
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
});
