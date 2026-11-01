import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useRead } from "@/lib/api/useRead";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const ok = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
const fail = () =>
  Promise.resolve(new Response(JSON.stringify({ error: { code: "x", message: "x", details: {} } }), { status: 500 }));

function Probe({ path }: { path: string | null }) {
  const r = useRead<{ n: string }>(path);
  return (
    <div>
      <p data-testid="s">{r.status}</p>
      <p data-testid="d">{r.data?.n ?? "-"}</p>
      <p data-testid="f">{String(r.isFetching)}</p>
      <button onClick={() => r.refetch()}>again</button>
    </div>
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("useRead", () => {
  it("reads the path, then shows the data", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok({ n: "a" })));
    render(<Probe path="/x" />);
    expect(screen.getByTestId("s")).toHaveTextContent("pending");
    await waitFor(() => expect(screen.getByTestId("d")).toHaveTextContent("a"));
    expect(screen.getByTestId("f")).toHaveTextContent("false");
  });

  // catches: the previous screen's rows shown under a new path (or new organization) while loading
  it("a new path is pending again, never the previous data", async () => {
    let release: () => void = () => {};
    const fetchMock = vi.fn((url: string) => (url.endsWith("/a") ? ok({ n: "A" }) : new Promise<Response>((r) => (release = () => r(new Response('{"n":"B"}', { status: 200 }))))));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<Probe path="/a" />);
    await waitFor(() => expect(screen.getByTestId("d")).toHaveTextContent("A"));
    rerender(<Probe path="/b" />);
    expect(screen.getByTestId("s")).toHaveTextContent("pending");
    expect(screen.getByTestId("d")).toHaveTextContent("-");
    await act(async () => release());
    await waitFor(() => expect(screen.getByTestId("d")).toHaveTextContent("B"));
  });

  it("a late answer for an old path is ignored", async () => {
    const pending: Record<string, (r: Response) => void> = {};
    vi.stubGlobal("fetch", vi.fn((url: string) => new Promise<Response>((r) => (pending[url] = r))));
    const { rerender } = render(<Probe path="/a" />);
    rerender(<Probe path="/b" />);
    await act(async () => pending["/api/v1/b"](new Response('{"n":"B"}', { status: 200 })));
    await act(async () => pending["/api/v1/a"](new Response('{"n":"A"}', { status: 200 })));
    expect(screen.getByTestId("d")).toHaveTextContent("B");
  });

  // catches: stale rows kept on screen after a refetch failed (spec 8.1: show the error)
  it("a failed refetch shows the error, not the stale data, and keeps the data while it is in flight", async () => {
    let next: () => Promise<Response> = () => ok({ n: "A" });
    vi.stubGlobal("fetch", vi.fn(() => next()));
    render(<Probe path="/x" />);
    await waitFor(() => expect(screen.getByTestId("d")).toHaveTextContent("A"));
    next = () => new Promise(() => {});
    await act(async () => screen.getByText("again").click());
    expect(screen.getByTestId("d")).toHaveTextContent("A");
    expect(screen.getByTestId("f")).toHaveTextContent("true");
    next = fail;
    await act(async () => screen.getByText("again").click());
    await waitFor(() => expect(screen.getByTestId("s")).toHaveTextContent("error"));
    expect(screen.getByTestId("d")).toHaveTextContent("-");
  });

  it("a null path makes no request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<Probe path={null} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
