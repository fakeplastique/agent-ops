import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlanView } from "@/app/(app)/plan/PlanView";
import { SettingsView } from "@/app/(app)/settings/SettingsView";

import { type Req, err, mockApi, page, session } from "./api-mock";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const free = {
  plan: "free",
  limits: { requests_per_month: 100, max_quorum: 3, max_rounds: 1, max_levels: 2 },
  usage: { requests: { used: 12, limit: 100, period: "2026-11" } },
  flags: [],
};
const pro = {
  plan: "pro",
  limits: { requests_per_month: null, max_quorum: null, max_rounds: 2, max_levels: 5 },
  usage: { requests: { used: 12, limit: null, period: "2026-11" } },
  flags: ["analytics", "attachments", "telegram"],
};
const PLAN = "GET /organizations/current/plan";
const DEMO = "GET /auth/demo/users";
const notDemo = err(404, "not_found", "not found");

describe("Plan", () => {
  it("shows usage as n / limit and says unlimited when there is no limit", async () => {
    mockApi({ "GET /auth/session": session(), [PLAN]: free, [DEMO]: notDemo });
    const { unmount } = render(<PlanView />);
    expect(await screen.findByText("12 / 100")).toBeInTheDocument();
    expect(screen.getByText("Up to 3")).toBeInTheDocument(); // reviewers
    unmount();
    mockApi({ "GET /auth/session": session(), [PLAN]: pro, [DEMO]: notDemo });
    render(<PlanView />);
    expect(await screen.findByText("12 / unlimited")).toBeInTheDocument();
    expect(screen.getByText("Any number")).toBeInTheDocument();
  });

  it("lists every feature as included or not on this plan", async () => {
    mockApi({ "GET /auth/session": session(), [PLAN]: free, [DEMO]: notDemo });
    render(<PlanView />);
    const analytics = (await screen.findByText("Analytics")).closest("li")!;
    expect(within(analytics).getByText("Not on this plan")).toBeInTheDocument();
  });

  it("the demo switch is absent in production and present only in demo mode for an org admin", async () => {
    mockApi({ "GET /auth/session": session(), [PLAN]: free, [DEMO]: notDemo });
    const { unmount } = render(<PlanView />);
    await screen.findByText("12 / 100");
    await waitFor(() => expect(screen.queryByText(/Demo mode/)).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Switch to/ })).not.toBeInTheDocument();
    unmount();

    const api = mockApi({ "GET /auth/session": session(), [PLAN]: free, [DEMO]: { users: [] }, "PUT /organizations/current/plan": { body: pro } });
    const user = userEvent.setup();
    const second = render(<PlanView />);
    await user.click(await screen.findByRole("button", { name: "Switch to Pro" }));
    await waitFor(() => expect(api.count("PUT /organizations/current/plan")).toBe(1));
    expect(api.calls.find((c) => c.key.startsWith("PUT"))?.body).toEqual({ plan: "pro" });
    second.unmount();

    mockApi({ "GET /auth/session": session("member"), [PLAN]: free, [DEMO]: { users: [] } });
    render(<PlanView />);
    await screen.findByText("12 / 100");
    expect(screen.queryByRole("button", { name: /Switch to/ })).not.toBeInTheDocument();
  });

  it("a failed read is an error with Retry, not a blank plan", async () => {
    mockApi({ "GET /auth/session": session(), [PLAN]: err(500, "internal", "boom"), [DEMO]: notDemo });
    render(<PlanView />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/12 \//)).not.toBeInTheDocument();
  });
});

const me = { user_id: "u-me", email: "maya@x.io", full_name: "Maya Okafor", role: "org_admin", team_id: null };
const pat = { id: "k1", name: "laptop", created_at: "2026-10-01T09:00:00Z", revoked_at: null };
const settingsRoutes = (over: Record<string, unknown> = {}) => ({
  "GET /auth/session": session(),
  "GET /members?limit=100": page([me]),
  [PLAN]: pro,
  "GET /me/telegram": { linked: false },
  "GET /me/tokens?limit=100": page([]),
  [DEMO]: notDemo,
  ...over,
});

describe("Settings", () => {
  it("shows the profile", async () => {
    mockApi(settingsRoutes());
    render(<SettingsView />);
    expect(await screen.findByText("Maya Okafor")).toBeInTheDocument();
    expect(screen.getByText("maya@x.io")).toBeInTheDocument();
  });

  it("Free shows the Telegram plan gate and makes no Telegram request", async () => {
    const api = mockApi(settingsRoutes({ [PLAN]: free }));
    render(<SettingsView />);
    expect(await screen.findByRole("heading", { name: "Telegram approvals is on the Pro plan" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.key.includes("/me/telegram"))).toBe(false);
  });

  it("an expired Telegram code offers a new one instead of a dead end", async () => {
    const user = userEvent.setup();
    let n = 0;
    const api = mockApi(
      settingsRoutes({
        "POST /me/telegram/link-code": () => {
          n += 1;
          return { status: 201, body: { code: `code-${n}`, link: `https://t.me/bot?start=code-${n}`, expires_at: n === 1 ? "2020-01-01T00:00:00Z" : "2099-01-01T00:00:00Z" } };
        },
      }),
    );
    render(<SettingsView />);
    await user.click(await screen.findByRole("button", { name: "Link Telegram" }));
    expect(await screen.findByText("This code expired")).toBeInTheDocument();
    expect(screen.queryByText("code-1")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Get a new code" }));
    expect(await screen.findByText("code-2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Telegram" })).toHaveAttribute("href", "https://t.me/bot?start=code-2");
    expect(api.count("POST /me/telegram/link-code")).toBe(2);
  });

  it("a fresh code is shown with its deep link and a way to check the result", async () => {
    const user = userEvent.setup();
    let linked = false;
    mockApi(
      settingsRoutes({
        "GET /me/telegram": () => ({ body: { linked } }),
        "POST /me/telegram/link-code": { status: 201, body: { code: "abc123", link: null, expires_at: "2099-01-01T00:00:00Z" } },
      }),
    );
    render(<SettingsView />);
    await user.click(await screen.findByRole("button", { name: "Link Telegram" }));
    expect(await screen.findByText("abc123")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Telegram" })).not.toBeInTheDocument(); // no bot username configured
    linked = true;
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("Telegram is linked")).toBeInTheDocument();
    expect(screen.queryByText("abc123")).not.toBeInTheDocument();
  });

  it("a plan that lost Telegram answers 402 and the gate replaces the block", async () => {
    const user = userEvent.setup();
    mockApi(
      settingsRoutes({
        "POST /me/telegram/link-code": err(402, "plan_feature_required", "pro needed", { flag: "telegram", required_plan: "pro" }),
      }),
    );
    render(<SettingsView />);
    await user.click(await screen.findByRole("button", { name: "Link Telegram" }));
    expect(await screen.findByRole("heading", { name: "Telegram approvals is on the Pro plan" })).toBeInTheDocument();
  });

  it("linked: unlink asks first, then sends one DELETE", async () => {
    const user = userEvent.setup();
    let linked = true;
    const api = mockApi(
      settingsRoutes({
        "GET /me/telegram": () => ({ body: { linked } }),
        "DELETE /me/telegram": () => {
          linked = false;
          return { status: 204 };
        },
      }),
    );
    render(<SettingsView />);
    await user.click(await screen.findByRole("button", { name: "Unlink" }));
    expect(api.count("DELETE /me/telegram")).toBe(0);
    await user.click(screen.getByRole("button", { name: "Unlink Telegram" }));
    expect(await screen.findByRole("button", { name: "Link Telegram" })).toBeInTheDocument();
    expect(api.count("DELETE /me/telegram")).toBe(1);
  });

  it("a personal access token is shown once and is gone after closing", async () => {
    const user = userEvent.setup();
    let made = false;
    mockApi(
      settingsRoutes({
        "GET /me/tokens?limit=100": () => ({ body: page(made ? [pat] : []) }),
        "POST /me/tokens": ({ body }: Req) => {
          made = true;
          expect(body).toEqual({ name: "laptop" });
          return { status: 201, body: { ...pat, token: "pat_only-once" } };
        },
      }),
    );
    render(<SettingsView />);
    await user.type(await screen.findByLabelText("Token name"), "laptop");
    await user.click(screen.getByRole("button", { name: "Create token" }));
    const dialog = await screen.findByRole("dialog", { name: /Token "laptop"/ });
    expect(within(dialog).getByLabelText("Token")).toHaveValue("pat_only-once");
    await user.click(within(dialog).getByRole("button", { name: "I have saved it" }));
    expect(document.body.innerHTML).not.toContain("pat_only-once");
    expect(await screen.findByText("laptop")).toBeInTheDocument();
  });

  it("revoked tokens are greyed with their date and offer no action; live ones revoke after a confirm", async () => {
    const user = userEvent.setup();
    const api = mockApi(
      settingsRoutes({
        "GET /me/tokens?limit=100": page([pat, { ...pat, id: "k2", name: "old", revoked_at: "2026-10-20T10:00:00Z" }]),
        "DELETE /me/tokens/k1": { status: 204 },
      }),
    );
    render(<SettingsView />);
    const old = (await screen.findByText("old")).closest("li")!;
    expect(within(old).getByText(/Revoked 20 Oct 2026/)).toBeInTheDocument();
    expect(within(old).queryByRole("button")).not.toBeInTheDocument();
    await user.click(within(screen.getByText("laptop").closest("li")!).getByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Revoke token" }));
    await waitFor(() => expect(api.count("DELETE /me/tokens/k1")).toBe(1));
  });

  it("each block fails on its own: a failed token list does not blank the profile", async () => {
    mockApi(settingsRoutes({ "GET /me/tokens?limit=100": err(500, "internal", "boom") }));
    render(<SettingsView />);
    expect(await screen.findByText("Maya Okafor")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
