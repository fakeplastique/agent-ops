import type { Page } from "@playwright/test";

// The API faked at the browser edge for the app screens: two organizations, org A with one request waiting on the user.
const mins = (n: number) => new Date(Date.now() + n * 60_000).toISOString();
const err = (code: string) => ({ error: { code, message: code, details: {} } });
const list = <T,>(items: T[], extra: Record<string, unknown> = {}) => ({ items, total: items.length, skip: 0, limit: 20, nextLink: null, ...extra });

export const TITLE_A = "Refund order #48213 of Northwind";
export const TITLE_B = "Rotate keys of Contoso";

export type AppState = { user: string | null; org: string; decided: string[]; decideCalls: number };

export async function fakeApp(page: Page, opts: { user?: string; org?: string } = {}) {
  const state: AppState = { user: opts.user ?? "user-a", org: opts.org ?? "org-a", decided: [], decideCalls: 0 };
  const titleOf = () => (state.org === "org-a" ? TITLE_A : TITLE_B);
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    const method = route.request().method();
    const path = pathname.replace("/api/v1", "");
    if (path === "/auth/session") {
      if (!state.user) return route.fulfill({ status: 401, json: err("unauthenticated") });
      return route.fulfill({ json: { user_id: state.user, organization_id: state.org, role: "manager" } });
    }
    if (path === "/auth/logout") {
      state.user = null;
      return route.fulfill({ status: 204 });
    }
    if (path === "/auth/organization" && method === "POST") {
      state.org = (route.request().postDataJSON() as { organization_id: string }).organization_id;
      return route.fulfill({ json: { user_id: state.user, organization_id: state.org, role: "manager" } });
    }
    if (path === "/me/organizations")
      return route.fulfill({ json: { items: [{ id: "org-a", name: "Northwind", role: "manager" }, { id: "org-b", name: "Contoso", role: "manager" }] } });
    if (path === "/agents") return route.fulfill({ json: list([{ id: "ag1", name: "billing-agent", status: "active" }]) });
    if (path === "/me/pending-approvals") {
      const open = state.decided.includes(state.org) ? [] : [{
        id: `st-${state.org}`, decision_request_id: `d-${state.org}`, request_version: 1, level: 1, required_role: "manager",
        due_at: mins(49), title: titleOf(), effective_risk_level: "high", requested_by_agent_id: "ag1",
      }];
      return route.fulfill({ json: list(open) });
    }
    if (path === `/decision-requests/d-${state.org}`)
      return route.fulfill({ json: {
        id: `d-${state.org}`, title: titleOf(), description: "Customer reports damage", action_type: "payments.refund",
        payload: { order_id: 48213, amount: 1240 }, risk_level: "high", effective_risk_level: "high",
        status: state.decided.includes(state.org) ? "approved" : "pending_approval", version: 1,
        content_hash: "7f3a91d2aaaaaaaaaaaaaaaaaaaac4e2", requested_by_agent_id: "ag1", requested_by_user_id: null,
        voting_deadline_at: mins(-20), escalation_reason: null, decided_at: state.decided.includes(state.org) ? mins(0) : null,
        created_at: mins(-30), updated_at: mins(-1),
      } });
    if (path.endsWith("/votes")) {
      const v = (verdict: string, round = 1) => ({ session_id: `${verdict}-${Math.random().toString(16).slice(2, 10)}`, round, verdict, confidence: 0.9, rationale: `rationale ${verdict}`, request_version: 1, created_at: mins(-25), revealed_at: mins(-20) });
      return route.fulfill({ json: list([v("approve"), v("approve"), v("reject")], { cast: 3, quorum: 3 }) });
    }
    if (path.endsWith("/steps"))
      return route.fulfill({ json: list(
        [{ id: `st-${state.org}`, request_version: 1, level: 1, required_role: "manager", status: state.decided.includes(state.org) ? "approved" : "pending", due_at: mins(49), decided_at: null, decided_by: null, stands_in_for_level: null, created_at: mins(-11) }],
        { quorum: 3, levels: [{ level: 1, role: "manager", timeout_minutes: 60 }, { level: 2, role: "org_admin", timeout_minutes: 60 }] },
      ) });
    if (path.startsWith("/approval-steps/") && path.endsWith("/decide")) {
      state.decideCalls += 1;
      state.decided.push(state.org);
      return route.fulfill({ json: { step_id: `st-${state.org}`, status: "approved", request_status: "approved", next_step_id: null } });
    }
    return route.fulfill({ status: 404, json: err("not_found") });
  });
  return state;
}
