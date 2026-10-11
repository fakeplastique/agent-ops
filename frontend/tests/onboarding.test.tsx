import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Onboarding } from "@/app/(app)/onboarding/Onboarding";
import { goTo } from "@/lib/api/navigate";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const SESSION = { user_id: "u1", organization_id: null, role: null };

// Routes by path; `create` answers POST /organizations.
function serve(create: () => Promise<Response>) {
  const fetchMock = vi.fn((url: string) => (url === "/api/v1/auth/session" ? json(200, SESSION) : create()));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => window.history.replaceState(null, "", "/onboarding"));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
  localStorage.clear();
});

describe("Onboarding", () => {
  it("shows no form until the session is known", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<Onboarding />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create organization" })).not.toBeInTheDocument();
  });

  it("a session read that fails shows the error with Retry, not the form", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json(503, { error: { code: "unavailable", message: "x", details: {} } })));
    render(<Onboarding />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });

  it("derives the slug from the name until the slug is edited, and points invitees to their link", async () => {
    serve(() => json(201, {}));
    render(<Onboarding />);
    const name = await screen.findByLabelText("Name");
    await userEvent.type(name, "Northwind Tradérs Ltd.");
    expect(screen.getByLabelText("Slug")).toHaveValue("northwind-traders-ltd");
    await userEvent.clear(screen.getByLabelText("Slug"));
    await userEvent.type(screen.getByLabelText("Slug"), "nw");
    await userEvent.type(name, " 2");
    expect(screen.getByLabelText("Slug")).toHaveValue("nw");
    expect(screen.getByText(/open the invitation link/i)).toBeInTheDocument();
  });

  it("creating posts name and slug, then clears the client and loads the inbox", async () => {
    localStorage.setItem("draft", "left by the previous organization");
    const fetchMock = serve(() => json(201, { id: "org-n", name: "Northwind", slug: "northwind", plan_key: "free" }));
    render(<Onboarding />);
    await userEvent.type(await screen.findByLabelText("Name"), "Northwind");
    await userEvent.click(screen.getByRole("button", { name: "Create organization" }));
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/organizations");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Northwind", slug: "northwind" });
    expect(localStorage.length).toBe(0);
    expect(goTo).toHaveBeenCalledWith("/inbox");
  });

  it("a taken slug is reported on the slug field and nothing else changes", async () => {
    serve(() => json(409, { error: { code: "slug_taken", message: "slug taken", details: {} } }));
    render(<Onboarding />);
    await userEvent.type(await screen.findByLabelText("Name"), "Northwind");
    await userEvent.click(screen.getByRole("button", { name: "Create organization" }));
    const slug = screen.getByLabelText("Slug");
    expect(await screen.findByText("That slug is taken. Try another.")).toBeInTheDocument();
    expect(slug).toHaveAttribute("aria-invalid", "true");
    expect(slug).toHaveFocus();
    expect(goTo).not.toHaveBeenCalled();
  });

  it("an invalid slug is caught before sending", async () => {
    const fetchMock = serve(() => json(201, {}));
    render(<Onboarding />);
    await userEvent.type(await screen.findByLabelText("Name"), "N");
    await userEvent.click(screen.getByRole("button", { name: "Create organization" }));
    expect(screen.getByLabelText("Slug")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).toHaveBeenCalledTimes(1); // the session read only
  });

  it("a failed create keeps what was typed and says so", async () => {
    serve(() => Promise.reject(new TypeError("offline")));
    render(<Onboarding />);
    await userEvent.type(await screen.findByLabelText("Name"), "Northwind");
    await userEvent.click(screen.getByRole("button", { name: "Create organization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The organization was not created");
    expect(screen.getByRole("alert")).toHaveTextContent(/Check your connection/);
    expect(screen.getByLabelText("Name")).toHaveValue("Northwind");
  });

  it("a server error keeps the form and does not blame the connection", async () => {
    serve(() => json(500, { error: { code: "internal", message: "boom", details: {} } }));
    render(<Onboarding />);
    await userEvent.type(await screen.findByLabelText("Name"), "Northwind");
    await userEvent.click(screen.getByRole("button", { name: "Create organization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The organization was not created");
    expect(screen.getByRole("alert")).not.toHaveTextContent(/connection/);
    expect(screen.getByLabelText("Name")).toHaveValue("Northwind");
  });
});
