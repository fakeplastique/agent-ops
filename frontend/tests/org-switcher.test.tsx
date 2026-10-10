import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgSwitcher } from "@/components/OrgSwitcher";
import { Shell } from "@/components/shell/Shell";
import { goTo } from "@/lib/api/navigate";
import { resetClient } from "@/lib/session";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/decisions" }));

const ORGS = [
  { id: "org-a", name: "Northwind" },
  { id: "org-b", name: "Contoso" },
];
const json = (status: number, body: unknown) =>
  vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })));

beforeEach(() => window.history.replaceState(null, "", "/decisions?selected=dr_a1#votes"));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
  localStorage.clear();
});

describe("OrgSwitcher", () => {
  it("with one organization shows its name and nothing to switch", () => {
    render(<OrgSwitcher organizations={[ORGS[0]]} currentId="org-a" />);
    expect(screen.getByText("Northwind")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("switching selects on the server, clears local state and loads the inbox fresh", async () => {
    localStorage.setItem("draft", "org A reject comment");
    const fetchMock = json(200, { user_id: "u", organization_id: "org-b", role: "member" });
    vi.stubGlobal("fetch", fetchMock);
    render(<OrgSwitcher organizations={ORGS} currentId="org-a" />);
    const select = screen.getByRole("combobox", { name: "Organization" });
    expect(select).toHaveValue("org-a");
    await userEvent.selectOptions(select, "org-b");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/organization", expect.objectContaining({ method: "POST" }));
    expect(localStorage.length).toBe(0);
    expect(goTo).toHaveBeenCalledWith("/inbox");
  });

  it("a refused switch says so and shows the organization still in use", async () => {
    vi.stubGlobal("fetch", json(404, { error: { code: "not_found", message: "not found", details: {} } }));
    render(<OrgSwitcher organizations={ORGS} currentId="org-a" />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Organization" }), "org-b");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not switch. You are still in Northwind.");
    expect(screen.getByRole("combobox", { name: "Organization" })).toHaveValue("org-a");
    expect(goTo).not.toHaveBeenCalled();
  });

  // The Shell renders the org slot in the sidebar and again in the menu sheet (S36 r1 F8).
  it("rendered in both shell slots, ids stay unique and each is labelled", async () => {
    render(
      <Shell org={<OrgSwitcher organizations={ORGS} currentId="org-a" />}>
        <p>body</p>
      </Shell>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    const sheet = screen.getByRole("dialog", { name: "Menu" });
    expect(within(sheet).getByRole("combobox", { name: "Organization" })).toHaveValue("org-a");
    const ids = [...document.querySelectorAll("[id]")].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  // Another tab signed out, signed in as someone else or switched organization: this tab must not keep org A on screen.
  it("reloads when another tab changes identity, but not for its own change", async () => {
    render(<OrgSwitcher organizations={ORGS} currentId="org-a" />);
    resetClient(); // this tab's own change: its caller navigates
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(goTo).not.toHaveBeenCalled();

    const other = new BroadcastChannel("aop.identity");
    other.postMessage("identity-changed");
    other.close();
    await waitFor(() => expect(goTo).toHaveBeenCalledWith("/decisions?selected=dr_a1"));
  });

  // Back after sign-out can restore this page from the back/forward cache with user A's DOM still in it.
  it("reloads when the page comes back from the back/forward cache", () => {
    render(<OrgSwitcher organizations={ORGS} currentId="org-a" />);
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));
    expect(goTo).not.toHaveBeenCalled();
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    expect(goTo).toHaveBeenCalledWith("/decisions?selected=dr_a1");
  });

  it("a second change while one is in flight sends nothing", async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    render(<OrgSwitcher organizations={[...ORGS, { id: "org-c", name: "Fabrikam" }]} currentId="org-a" />);
    const select = screen.getByRole("combobox", { name: "Organization" });
    await userEvent.selectOptions(select, "org-b");
    await userEvent.selectOptions(select, "org-c");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
