import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const reply = (status: number) =>
  vi.fn(() => Promise.resolve(new Response("{}", { status, headers: { "content-type": "application/json" } })));

afterEach(() => vi.unstubAllGlobals());

describe("api client request options", () => {
  it("sends extra headers such as Idempotency-Key next to the JSON content type", async () => {
    const fetchMock = reply(200);
    vi.stubGlobal("fetch", fetchMock);
    await api("/approval-steps/1/decide", { method: "POST", body: { a: 1 }, headers: { "Idempotency-Key": "k-1" } });
    const headers = new Headers(((fetchMock.mock.calls[0] as unknown[])[1] as RequestInit).headers);
    expect(headers.get("idempotency-key")).toBe("k-1");
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("sends a File as the raw body, so the browser sets its own content type", async () => {
    const fetchMock = reply(201);
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "a.txt", { type: "text/plain" });
    await api("/decision-requests/1/attachments?filename=a.txt", { method: "POST", body: file });
    const init = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(init.body).toBe(file);
    expect(new Headers(init.headers).get("content-type")).toBeNull();
  });

  it("sends FormData as is, so the browser sets the multipart boundary", async () => {
    const fetchMock = reply(201);
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.set("a", "b");
    await api("/x", { method: "POST", body: form });
    expect(((fetchMock.mock.calls[0] as unknown[])[1] as RequestInit).body).toBe(form);
  });
});
