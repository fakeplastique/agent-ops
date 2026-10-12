import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { goTo } from "@/lib/api/navigate";

vi.mock("next/navigation", () => ({ usePathname: () => "/inbox" }));
vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const ORGS = { items: [{ id: "o1", name: "Northwind", role: "manager" }, { id: "o2", name: "Contoso", role: "member" }] };

function serve(routes: Record<string, () => Promise<Response>>) {
  const fetchMock = vi.fn((url: string) => (routes[url] ?? (() => json(404, { error: { code: "not_found", message: "x", details: {} } })))());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const session = (org: string | null) => () => json(200, { user_id: "u1", organization_id: org, role: org ? "manager" : null });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
});

describe("AppShell", () => {
  it("shows no app chrome or children until the session is known", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<AppShell>secret</AppShell>);
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  // catches: a signed-in user with no organization landing on an app screen that can only 403
  it("a session without an organization is sent to onboarding and renders no screen", async () => {
    serve({ "/api/v1/auth/session": session(null) });
    render(<AppShell>secret</AppShell>);
    await waitFor(() => expect(goTo).toHaveBeenCalledWith("/onboarding"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("feeds the organization switcher from /me/organizations and the inbox count from pending approvals", async () => {
    serve({
      "/api/v1/auth/session": session("o1"),
      "/api/v1/me/organizations": () => json(200, ORGS),
      "/api/v1/me/pending-approvals?limit=1": () => json(200, { items: [], total: 3, skip: 0, limit: 1, nextLink: null }),
    });
    render(<AppShell>screen body</AppShell>);
    expect(await screen.findByText("screen body")).toBeInTheDocument();
    const select = await screen.findByRole("combobox", { name: "Organization" });
    expect(select).toHaveValue("o1");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Northwind", "Contoso"]);
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(await within(nav).findByRole("link", { name: /Inbox, 3 waiting/ })).toBeInTheDocument();
  });

  // catches: a failed organization read rendered as a single-organization account
  it("a failed organization read says so and offers Retry, not a bare name", async () => {
    let fail = true;
    serve({
      "/api/v1/auth/session": session("o1"),
      "/api/v1/me/organizations": () => (fail ? json(503, { error: { code: "x", message: "x", details: {} } }) : json(200, ORGS)),
      "/api/v1/me/pending-approvals?limit=1": () => json(200, { items: [], total: 0, skip: 0, limit: 1, nextLink: null }),
    });
    render(<AppShell>body</AppShell>);
    const retry = await screen.findByRole("button", { name: "Retry organizations" });
    fail = false;
    await userEvent.click(retry);
    expect(await screen.findByRole("combobox", { name: "Organization" })).toBeInTheDocument();
  });

  it("the user row names the role and signs out", async () => {
    const fetchMock = serve({
      "/api/v1/auth/session": session("o1"),
      "/api/v1/me/organizations": () => json(200, ORGS),
      "/api/v1/me/pending-approvals?limit=1": () => json(200, { items: [], total: 0, skip: 0, limit: 1, nextLink: null }),
      "/api/v1/auth/logout": () => json(204, null),
    });
    render(<AppShell>body</AppShell>);
    expect(await screen.findByText("Manager")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Sign out" })[0]);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/logout", expect.objectContaining({ method: "POST" }));
    await waitFor(() => expect(goTo).toHaveBeenCalledWith("/login"));
  });
});
