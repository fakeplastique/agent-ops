import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api/client";
import { goTo } from "@/lib/api/navigate";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const reply = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
  vi.fn(() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json", ...headers },
      }),
    ),
  );
const envelope = (code: string, message = code, details = {}) => ({ error: { code, message, details } });

async function failure(p: Promise<unknown>): Promise<ApiError> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(ApiError);
  return err as ApiError;
}

beforeEach(() => window.history.replaceState(null, "", "/decisions?status=pending_votes"));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
});

describe("api client", () => {
  it("reads JSON from /api/v1 on the same origin", async () => {
    const fetchMock = reply(200, { items: [] });
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/decision-requests")).toEqual({ items: [] });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/decision-requests", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("sends a JSON body and returns undefined on 204", async () => {
    const fetchMock = reply(204);
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/agents/1", { method: "DELETE", body: { a: 1 } })).toBeUndefined();
    const init = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(init.body).toBe('{"a":1}');
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
  });

  // An expired session must not look like an empty list: send the user to sign in and come back.
  it("401 redirects to login keeping the return path, and still rejects", async () => {
    vi.stubGlobal("fetch", reply(401, envelope("unauthenticated")));
    const err = await failure(api("/me"));
    expect(err.status).toBe(401);
    expect(goTo).toHaveBeenCalledWith("/login?next=%2Fdecisions%3Fstatus%3Dpending_votes");
  });

  it("401 on the login page itself does not loop", async () => {
    window.history.replaceState(null, "", "/login");
    vi.stubGlobal("fetch", reply(401, envelope("unauthenticated")));
    await failure(api("/auth/session"));
    expect(goTo).not.toHaveBeenCalled();
  });

  it("429 surfaces Retry-After in seconds", async () => {
    vi.stubGlobal("fetch", reply(429, envelope("rate_limited"), { "Retry-After": "17" }));
    const err = await failure(api("/decision-requests"));
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(17);
  });

  it("keeps the error envelope and the request id", async () => {
    vi.stubGlobal(
      "fetch",
      reply(500, envelope("internal_error", "Internal server error", { request_id: "req-1" }), { "X-Request-ID": "req-1" }),
    );
    const err = await failure(api("/decision-requests"));
    expect(err).toMatchObject({ status: 500, code: "internal_error", requestId: "req-1" });
  });

  it("a non-JSON failure (proxy 502) is still an ApiError, not a parse crash", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("<html>Bad gateway</html>", { status: 502 }))));
    const err = await failure(api("/decision-requests"));
    expect(err).toMatchObject({ status: 502, code: "http_502", requestId: null });
  });

  it("a network failure is status 0, never a resolved empty value", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const err = await failure(api("/decision-requests"));
    expect(err).toMatchObject({ status: 0, code: "network" });
  });

  // A hung request would otherwise leave the skeleton up forever with no Retry.
  it("a request with no answer in 15 s fails as a timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => new Promise((_, rej) => init.signal?.addEventListener("abort", () => rej(init.signal?.reason)))),
    );
    const p = failure(api("/decision-requests"));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await p).toMatchObject({ status: 0, code: "timeout" });
  });

  it("a caller abort is passed through, not reported as a network error", async () => {
    const ctl = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => new Promise((_, rej) => init.signal?.addEventListener("abort", () => rej(init.signal?.reason)))),
    );
    const p = api("/decision-requests", { signal: ctl.signal });
    ctl.abort();
    await expect(p).rejects.toHaveProperty("name", "AbortError");
  });
});
