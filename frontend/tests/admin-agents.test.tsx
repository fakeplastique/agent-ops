import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentsView } from "@/app/(app)/agents/AgentsView";
import { MembersView } from "@/app/(app)/members/MembersView";
import { TeamsView } from "@/app/(app)/teams/TeamsView";

import { err, mockApi, page, session } from "./api-mock";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

const owner = { user_id: "u-me", email: "maya@x.io", full_name: "Maya Okafor", role: "org_admin", team_id: null };
const agent = (over: object) => ({
  id: "a1",
  name: "refund-bot",
  description: "Issues refunds",
  vendor: "acme",
  owner_user_id: "u-me",
  status: "active",
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  ...over,
});
const agentRoutes = (agents: object[]) => ({
  "GET /auth/session": session(),
  "GET /agents?limit=100": page(agents),
  "GET /members?limit=100": page([owner]),
});

describe("TokenDialog (agents)", () => {
  it("shows the token once, copies it, cannot be reopened and leaves no trace in the DOM", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    let created = false;
    mockApi({
      ...agentRoutes([]),
      "GET /agents?limit=100": () => ({ body: page(created ? [agent({})] : []) }),
      "POST /agents": () => {
        created = true;
        return { status: 201, body: { ...agent({}), token: "aop_secret-value-1" } };
      },
    });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.type(screen.getByLabelText("Name"), "refund-bot");
    await user.click(screen.getByRole("button", { name: "Create agent" }));

    const dialog = await screen.findByRole("dialog", { name: /Token for refund-bot/ });
    expect(within(dialog).getByLabelText("Token")).toHaveValue("aop_secret-value-1");
    await user.click(within(dialog).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("aop_secret-value-1");
    expect(await within(dialog).findByText("Copied.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "I have saved it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain("aop_secret-value-1");
    // The row offers a new token (rotate), never the old one.
    expect(await screen.findByRole("button", { name: "Rotate token" })).toBeInTheDocument();
    expect(screen.queryByText(/aop_secret/)).not.toBeInTheDocument();
  });

  it("rotate shows the replacement token in the same dialog and clears it on close", async () => {
    const user = userEvent.setup();
    mockApi({ ...agentRoutes([agent({})]), "POST /agents/a1/rotate": { body: { id: "a1", token: "aop_rotated-2" } } });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Rotate token" }));
    await user.click(screen.getByRole("button", { name: "Rotate" }));
    expect(await screen.findByLabelText("Token")).toHaveValue("aop_rotated-2");
    await user.click(screen.getByRole("button", { name: "I have saved it" }));
    expect(document.body.innerHTML).not.toContain("aop_rotated-2");
  });

  it("Escape does not close it: only 'I have saved it' does", async () => {
    const user = userEvent.setup();
    mockApi({ ...agentRoutes([agent({})]), "POST /agents/a1/rotate": { body: { id: "a1", token: "aop_rotated-2" } } });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Rotate token" }));
    await user.click(screen.getByRole("button", { name: "Rotate" }));
    await screen.findByLabelText("Token");
    await user.keyboard("{Escape}");
    expect(screen.getByLabelText("Token")).toHaveValue("aop_rotated-2");
  });

  it("a second secret opens fresh: 'Copied.' from the first does not carry over", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn(() => Promise.resolve()) }, configurable: true });
    let n = 0;
    mockApi({ ...agentRoutes([agent({})]), "POST /agents/a1/rotate": () => ({ body: { id: "a1", token: `aop_rotated-${++n}` } }) });
    render(<AgentsView />);
    for (const round of [1, 2]) {
      await user.click(await screen.findByRole("button", { name: "Rotate token" }));
      await user.click(screen.getByRole("button", { name: "Rotate" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByLabelText("Token")).toHaveValue(`aop_rotated-${round}`);
      expect(within(dialog).queryByText("Copied.")).not.toBeInTheDocument();
      if (round === 1) await user.click(within(dialog).getByRole("button", { name: "Copy" }));
      if (round === 1) await within(dialog).findByText("Copied.");
      await user.click(within(dialog).getByRole("button", { name: "I have saved it" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }
  });

  it("a stray click outside does not discard the token", async () => {
    const user = userEvent.setup();
    mockApi({ ...agentRoutes([agent({})]), "POST /agents/a1/rotate": { body: { id: "a1", token: "aop_rotated-2" } } });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Rotate token" }));
    await user.click(screen.getByRole("button", { name: "Rotate" }));
    await screen.findByLabelText("Token");
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    expect(screen.getByLabelText("Token")).toHaveValue("aop_rotated-2");
  });
});

describe("Agents", () => {
  it("a revoked agent is greyed with its revoke date and has no actions", async () => {
    mockApi(
      agentRoutes([
        agent({ id: "a1", name: "live-bot" }),
        agent({ id: "a2", name: "old-bot", status: "revoked", updated_at: "2026-10-20T10:00:00Z" }),
      ]),
    );
    render(<AgentsView />);
    const old = (await screen.findByText("old-bot")).closest("li")!;
    expect(within(old).getByText("Revoked")).toBeInTheDocument();
    expect(within(old).getByText(/20 Oct 2026/)).toBeInTheDocument();
    expect(within(old).queryByRole("button")).not.toBeInTheDocument();
    const live = screen.getByText("live-bot").closest("li")!;
    expect(within(live).getByRole("button", { name: "Rotate token" })).toBeInTheDocument();
    expect(within(live).getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("revoke asks first, then sends one DELETE and refreshes the list", async () => {
    const user = userEvent.setup();
    let revoked = false;
    const api = mockApi({
      ...agentRoutes([]),
      "GET /agents?limit=100": () => ({ body: page([agent(revoked ? { status: "revoked", updated_at: "2026-11-03T08:00:00Z" } : {})]) }),
      "DELETE /agents/a1": () => {
        revoked = true;
        return { status: 204 };
      },
    });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(api.count("DELETE /agents/a1")).toBe(0);
    await user.click(screen.getByRole("button", { name: "Revoke agent" }));
    expect(await screen.findByText("Revoked")).toBeInTheDocument();
    expect(api.count("DELETE /agents/a1")).toBe(1);
  });

  it("cancelling a confirm returns focus to the button that asked", async () => {
    const user = userEvent.setup();
    mockApi(agentRoutes([agent({})]));
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Revoke" })).toHaveFocus();
  });

  it("a failed read is an error with Retry, never an empty list", async () => {
    const user = userEvent.setup();
    let ok = false;
    mockApi({
      ...agentRoutes([]),
      "GET /agents?limit=100": () => (ok ? { body: page([]) } : err(500, "internal", "boom", { request_id: "req-9" })),
    });
    render(<AgentsView />);
    expect(await screen.findByRole("heading", { name: "Something went wrong on our side" })).toBeInTheDocument();
    expect(screen.queryByText(/No agents yet/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New agent" })).not.toBeInTheDocument();
    ok = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
  });

  it("an empty organization gets one sentence and the create action", async () => {
    mockApi(agentRoutes([]));
    render(<AgentsView />);
    expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "New agent" }).length).toBeGreaterThan(0);
  });

  it("a duplicate name says so and keeps the form", async () => {
    const user = userEvent.setup();
    mockApi({ ...agentRoutes([]), "POST /agents": err(409, "already_exists", "an agent with this name exists") });
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "New agent" }));
    await user.type(screen.getByLabelText("Name"), "refund-bot");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("An agent with this name already exists");
    expect(screen.getByLabelText("Name")).toHaveValue("refund-bot");
  });

  it("a member sees the list read-only", async () => {
    mockApi({ ...agentRoutes([agent({})]), "GET /auth/session": session("member") });
    render(<AgentsView />);
    expect(await screen.findByText("refund-bot")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New agent" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rotate token" })).not.toBeInTheDocument();
  });
});

const team = (id: string, name: string, parent: string | null = null) => ({ id, name, parent_team_id: parent, created_at: "2026-10-01T09:00:00Z" });

describe("Teams", () => {
  it("is forbidden for members and makes no admin read", async () => {
    const api = mockApi({ "GET /auth/session": session("member"), "GET /teams?limit=100": page([]) });
    render(<TeamsView />);
    expect(await screen.findByRole("heading", { name: "You do not have access to this" })).toBeInTheDocument();
    expect(screen.getByText(/org admin role/)).toBeInTheDocument();
    expect(api.count("GET /teams?limit=100")).toBe(0);
  });

  it("a cross-tenant or missing team list answers with the plain Not found text", async () => {
    mockApi({ "GET /auth/session": session(), "GET /teams?limit=100": err(404, "not_found", "not found") });
    render(<TeamsView />);
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });

  it("shows the tree with sub-teams nested and refuses a delete the API refuses with its reason", async () => {
    const user = userEvent.setup();
    mockApi({
      "GET /auth/session": session(),
      "GET /teams?limit=100": page([team("t1", "Support"), team("t2", "Refunds", "t1")]),
      "DELETE /teams/t1": err(409, "team_in_use", "the team still has sub-teams"),
    });
    render(<TeamsView />);
    const child = (await screen.findByText("Refunds")).closest("li")!;
    expect(child.parentElement!.closest("li")).toHaveTextContent("Support");
    await user.click(within(screen.getByText("Support").closest("li")!).getAllByRole("button", { name: "Delete" })[0]);
    await user.click(screen.getByRole("button", { name: "Delete team" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("still has sub-teams, members, invitations or a policy");
  });

  it("an empty tree offers the create action", async () => {
    mockApi({ "GET /auth/session": session(), "GET /teams?limit=100": page([]) });
    render(<TeamsView />);
    expect(await screen.findByText(/No teams yet/)).toBeInTheDocument();
  });
});

const member = (id: string, name: string, role: string) => ({ user_id: id, email: `${name}@x.io`, full_name: name, role, team_id: null });
const membersRoutes = (members: object[], invitations: object[] = []) => ({
  "GET /auth/session": session(),
  "GET /members?limit=100": page(members),
  "GET /invitations?limit=100": page(invitations),
});

describe("Members", () => {
  it("the last org admin shows the inline reason on remove and demote, and sends nothing", async () => {
    const user = userEvent.setup();
    const api = mockApi(membersRoutes([member("u-me", "Maya", "org_admin"), member("u-2", "Noor", "member")]));
    render(<MembersView />);
    const row = (await screen.findByText("Maya")).closest("li")!;
    expect(within(row).getByText(/last org admin/i)).toBeInTheDocument();
    const role = within(row).getByLabelText("Role for Maya");
    expect(role).toHaveAttribute("aria-disabled", "true");
    await user.selectOptions(role, "member");
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    expect(api.calls.filter((c) => c.key.startsWith("PATCH") || c.key.startsWith("DELETE"))).toEqual([]);
  });

  it("another org admin lifts the restriction", async () => {
    mockApi(membersRoutes([member("u-me", "Maya", "org_admin"), member("u-2", "Noor", "org_admin")]));
    render(<MembersView />);
    const row = (await screen.findByText("Maya")).closest("li")!;
    expect(within(row).queryByText(/last org admin/i)).not.toBeInTheDocument();
    expect(within(row).getByLabelText("Role for Maya")).not.toHaveAttribute("aria-disabled", "true");
  });

  it("a server refusal (409 last_org_admin) shows the same reason", async () => {
    const user = userEvent.setup();
    // The list is cut at 100 so the screen cannot know; the API decides.
    mockApi({
      ...membersRoutes([member("u-me", "Maya", "org_admin")]),
      "GET /members?limit=100": page([member("u-me", "Maya", "org_admin"), member("u-3", "Ola", "member")], 150),
      "PATCH /members/u-me": err(409, "last_org_admin", "an organization keeps at least one org_admin"),
    });
    render(<MembersView />);
    const row = (await screen.findByText("Maya")).closest("li")!;
    await user.selectOptions(within(row).getByLabelText("Role for Maya"), "member");
    expect(await screen.findByRole("alert")).toHaveTextContent(/last org admin/i);
  });

  it("changing a role sends one PATCH and shows the answer", async () => {
    const user = userEvent.setup();
    let role = "member";
    const api = mockApi({
      ...membersRoutes([]),
      "GET /members?limit=100": () => ({ body: page([member("u-me", "Maya", "org_admin"), member("u-2", "Noor", role)]) }),
      "PATCH /members/u-2": ({ body }) => {
        role = body.role;
        return { body: member("u-2", "Noor", role) };
      },
    });
    render(<MembersView />);
    const row = (await screen.findByText("Noor")).closest("li")!;
    await user.selectOptions(within(row).getByLabelText("Role for Noor"), "manager");
    await waitFor(() => expect(within(screen.getByText("Noor").closest("li")!).getByLabelText("Role for Noor")).toHaveValue("manager"));
    expect(api.count("PATCH /members/u-2")).toBe(1);
  });
});

describe("Invitations", () => {
  const pending = { id: "i1", email: "new@x.io", role: "member", team_id: null, expires_at: "2026-11-10T09:00:00Z" };

  it("copy the link once, then it is gone after closing and after a reload; revoke sends one DELETE", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    let made = false;
    const api = mockApi({
      ...membersRoutes([member("u-me", "Maya", "org_admin")]),
      "GET /invitations?limit=100": () => ({ body: page(made ? [pending] : []) }),
      "POST /invitations": () => {
        made = true;
        return { status: 201, body: { ...pending, token: "tok-abc" } };
      },
      "DELETE /invitations/i1": { status: 204 },
    });
    const { unmount } = render(<MembersView />);
    await user.type(await screen.findByLabelText("Email"), "New@x.io");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    const dialog = await screen.findByRole("dialog", { name: /Invitation for new@x.io/ });
    const link = within(dialog).getByLabelText("Invitation link") as HTMLTextAreaElement;
    expect(link.value).toBe(`${window.location.origin}/invite/tok-abc`);
    await user.click(within(dialog).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/invite/tok-abc`);
    await user.click(within(dialog).getByRole("button", { name: "I have saved it" }));
    expect(document.body.innerHTML).not.toContain("tok-abc");

    // After a reload the pending row has no link to show, only Revoke.
    unmount();
    render(<MembersView />);
    const row = (await screen.findByText("new@x.io")).closest("li")!;
    expect(document.body.innerHTML).not.toContain("tok-abc");
    await user.click(within(row).getByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Revoke invitation" }));
    await waitFor(() => expect(api.count("DELETE /invitations/i1")).toBe(1));
  });

  it("a taken seat or bad email names the problem and keeps the form", async () => {
    const user = userEvent.setup();
    mockApi({ ...membersRoutes([member("u-me", "Maya", "org_admin")]), "POST /invitations": err(422, "invalid_input", "email is not valid") });
    render(<MembersView />);
    await user.type(await screen.findByLabelText("Email"), "bad");
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("email is not valid");
    expect(screen.getByLabelText("Email")).toHaveValue("bad");
  });

  it("members are forbidden here, and no invitation read is made", async () => {
    const api = mockApi({ ...membersRoutes([]), "GET /auth/session": session("member") });
    render(<MembersView />);
    expect(await screen.findByRole("heading", { name: "You do not have access to this" })).toBeInTheDocument();
    expect(api.count("GET /invitations?limit=100")).toBe(0);
  });
});
