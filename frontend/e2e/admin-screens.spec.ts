import { expect, test, type Page } from "@playwright/test";

// S42-S44 smoke at 375 and 1280: every admin screen renders its data in the Shell, nothing scrolls sideways, and a named
// screenshot is kept. The API is faked at the browser edge (mock backend), with long content to exercise truncation.

const LONG = "refund-approval-assistant-for-the-emea-support-queue-with-a-very-long-name";
const now = "2026-11-03T09:00:00Z";
const page_ = <T,>(items: T[]) => ({ items, total: items.length, skip: 0, limit: 100, nextLink: null });
const plan = {
  plan: "pro",
  limits: { requests_per_month: null, max_quorum: null, max_rounds: 2, max_levels: 5 },
  usage: { requests: { used: 12, limit: null, period: "2026-11" } },
  flags: ["analytics", "telegram", "attachments"],
};
const point = (d: number, n: number) => ({ date: `2026-10-${String(d).padStart(2, "0")}`, requests: n, approved: 0, rejected: 0, auto_approved: 0 });

const data: Record<string, unknown> = {
  "/auth/session": { user_id: "u1", organization_id: "o1", role: "org_admin" },
  "/auth/demo/users": null, // 404: not a demo
  "/agents": page_([
    { id: "a1", name: LONG, description: "Issues refunds under 200 EUR", vendor: "acme", owner_user_id: "u1", status: "active", created_at: now, updated_at: now },
    { id: "a2", name: "old-bot", description: "", vendor: null, owner_user_id: "u1", status: "revoked", created_at: now, updated_at: now },
  ]),
  "/members": page_([{ user_id: "u1", email: "maya.okafor@example.com", full_name: "Maya Okafor", role: "org_admin", team_id: null }]),
  "/invitations": page_([{ id: "i1", email: "new.person@example.com", role: "member", team_id: null, expires_at: "2026-11-10T09:00:00Z" }]),
  "/teams": page_([
    { id: "t1", name: "Support", parent_team_id: null, created_at: now },
    { id: "t2", name: "Refunds", parent_team_id: "t1", created_at: now },
  ]),
  "/escalation-policies": page_([
    {
      id: "p1", team_id: null, name: "default", quorum_size: 3, require_distinct_agents: true, rounds: 1, confidence_threshold: 0.8,
      levels: [{ role: "team_lead", timeout_minutes: 30 }, { role: "org_admin", timeout_minutes: 60 }],
      voting_timeout_minutes: 60, risk_floors: { "refund.issue": "high" }, created_at: now, updated_at: now,
    },
  ]),
  "/organizations/current/plan": plan,
  "/webhook-subscriptions": page_([{ id: "w1", url: `https://hooks.example.com/${LONG}`, events: ["decision.created", "step.decided"], active: true, created_at: now, updated_at: now }]),
  "/me/telegram": { linked: false },
  "/me/tokens": page_([{ id: "k1", name: "laptop", created_at: now, revoked_at: null }]),
  "/stats/summary": {
    first_day: "2026-10-05", last_day: "2026-11-03", days: 30, requests: 20, by_status: { approved: 12, rejected: 3, pending_approval: 5 },
    by_risk: { low: 10, medium: 6, high: 4 }, auto_approval_rate: 0.35, median_seconds_to_decision: 270, average_seconds_to_decision: 300,
    votes_per_agent: [{ agent_id: "a1", name: LONG, votes: 41 }],
  },
  "/stats/timeseries": { first_day: "2026-10-05", last_day: "2026-10-07", days: 3, points: [point(5, 4), point(6, 0), point(7, 16)] },
};

async function fakeApi(page: Page) {
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path in data) {
      const body = data[path];
      return body === null
        ? route.fulfill({ status: 404, json: { error: { code: "not_found", message: "x", details: {} } } })
        : route.fulfill({ json: body });
    }
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "x", details: {} } } });
  });
}

const SCREENS: [string, string, string][] = [
  ["agents", "/agents", "Agents"],
  ["teams", "/teams", "Teams"],
  ["members", "/members", "Members"],
  ["policies", "/policies", "Policies"],
  ["webhooks", "/webhooks", "Webhooks"],
  ["plan", "/plan", "Plan and usage"],
  ["settings", "/settings", "Settings"],
  ["analytics", "/analytics", "Analytics"],
];

for (const [name, path, title] of SCREENS) {
  test(`${name} renders in the shell with no sideways scroll`, async ({ page }, info) => {
    await fakeApi(page);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/admin-${name}-${info.project.name}.png`, fullPage: true });
  });
}
