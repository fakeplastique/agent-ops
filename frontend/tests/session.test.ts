import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { goTo } from "@/lib/api/navigate";
import { rememberNext, resetClient, safeNext, signOut, switchOrganization, takeNext } from "@/lib/session";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const reply = (status: number, body?: unknown) =>
  vi.fn(() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );

let posted: unknown[] = [];
class FakeChannel {
  constructor(readonly name: string) {}
  postMessage(m: unknown) {
    posted.push(m);
  }
  close() {}
  addEventListener() {}
  removeEventListener() {}
}

beforeEach(() => {
  posted = [];
  vi.stubGlobal("BroadcastChannel", FakeChannel);
  localStorage.setItem("draft", "user A's refund note");
  sessionStorage.setItem("selected", "dr_a1");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
  localStorage.clear();
  sessionStorage.clear();
});

describe("safeNext", () => {
  it.each(["/inbox", "/decisions?status=pending_votes&page=2", "/invite/inv_abc", "/a/%2F%2Fevil.com"])("keeps %s", (p) =>
    expect(safeNext(p)).toBe(p),
  );

  it.each([
    "//evil.com",
    "//evil.com/inbox",
    "/\\evil.com",
    "\\\\evil.com",
    "/inbox\\..\\x",
    "https://evil.com",
    "http:/evil.com",
    "javascript:alert(1)",
    "evil.com",
    "inbox",
    "/\t/evil.com",
    "/\n/evil.com",
    "",
    null,
  ])("rejects %j", (p) => expect(safeNext(p)).toBeNull());
});

describe("next across the login round trip", () => {
  it("stores only a safe next and hands it back once", () => {
    rememberNext("/decisions/42");
    expect(takeNext()).toBe("/decisions/42");
    expect(takeNext()).toBeNull();
    rememberNext("//evil.com");
    expect(takeNext()).toBeNull();
  });

  it("re-checks what it reads back (storage is writable by any script on the origin)", () => {
    sessionStorage.setItem("aop.next", "//evil.com");
    expect(takeNext()).toBeNull();
  });
});

describe("identity change", () => {
  it("resetClient empties both storages and tells other tabs", () => {
    resetClient();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(posted).toEqual(["identity-changed"]);
  });

  it("signOut ends the server session, then clears and reloads into /login", async () => {
    const fetchMock = reply(204);
    vi.stubGlobal("fetch", fetchMock);
    await signOut();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/logout", expect.objectContaining({ method: "POST" }));
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(goTo).toHaveBeenCalledWith("/login");
  });

  // A failed logout must still drop this browser's copy of user A: the next person at the keyboard is the risk.
  it("signOut clears and leaves even when the server call fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    await signOut();
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(goTo).toHaveBeenCalledWith("/login");
  });

  it("switchOrganization selects on the server, then clears and does a full load of the inbox", async () => {
    const fetchMock = reply(200, { user_id: "u", organization_id: "org-b", role: "member" });
    vi.stubGlobal("fetch", fetchMock);
    await switchOrganization("org-b");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/auth/organization");
    expect(JSON.parse(String(init.body))).toEqual({ organization_id: "org-b" });
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(posted).toEqual(["identity-changed"]);
    expect(goTo).toHaveBeenCalledWith("/inbox");
  });

  // The session still points at org A; clearing or leaving would show org A's page with nothing in it.
  it("a refused switch keeps the client as it is and rejects", async () => {
    vi.stubGlobal("fetch", reply(404, { error: { code: "not_found", message: "not found", details: {} } }));
    await expect(switchOrganization("org-x")).rejects.toMatchObject({ status: 404 });
    expect(localStorage.getItem("draft")).toBe("user A's refund note");
    expect(goTo).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
  });
});
