import { render } from "@testing-library/react";
import { vi } from "vitest";
import type { ReactElement } from "react";

import { AppContext } from "@/components/shell/AppShell";
import type { Decision, Page, PendingStep, Step, StepsPage, Vote, VotesPage } from "@/lib/api/types";

export const NOW = Date.now();
export const iso = (min: number) => new Date(NOW + min * 60_000).toISOString();

export const json = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
  Promise.resolve(
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } }),
  );
export const failure = (status: number, code: string, message = code, details: Record<string, unknown> = {}) =>
  json(status, { error: { code, message, details } });

export const page = <T,>(items: T[], extra: Record<string, unknown> = {}): Page<T> & Record<string, unknown> => ({
  items, total: items.length, skip: 0, limit: 20, nextLink: null, ...extra,
});

export const decision = (o: Partial<Decision> = {}): Decision => ({
  id: "d1", title: "Refund order #48213", description: "Customer reports damage", action_type: "payments.refund",
  payload: { order_id: 48213, amount: 1240 }, risk_level: "high", effective_risk_level: "high", status: "pending_approval",
  version: 1, content_hash: "7f3a91d2aaaaaaaaaaaaaaaaaaaac4e2", requested_by_agent_id: "a1", requested_by_user_id: null,
  voting_deadline_at: iso(-20), escalation_reason: null, decided_at: null, created_at: iso(-30), updated_at: iso(-1), ...o,
});
export const vote = (verdict: Vote["verdict"], o: Partial<Vote> = {}): Vote => ({
  session_id: `s-${verdict}-${Math.random().toString(16).slice(2, 8)}`, round: 1, verdict, confidence: 0.9,
  rationale: `rationale ${verdict}`, request_version: 1, created_at: iso(-25), revealed_at: iso(-20), ...o,
});
export const step = (level: number, status: Step["status"] = "pending", o: Partial<Step> = {}): Step => ({
  id: `st${level}`, request_version: 1, level, required_role: level === 1 ? "manager" : "org_admin", status,
  due_at: iso(49), decided_at: null, decided_by: null, stands_in_for_level: null, created_at: iso(-11), ...o,
});
export const pending = (o: Partial<PendingStep> = {}): PendingStep => ({
  id: "st1", decision_request_id: "d1", request_version: 1, level: 1, required_role: "manager", due_at: iso(49),
  title: "Refund order #48213", effective_risk_level: "high", requested_by_agent_id: "a1", ...o,
});
export const votesPage = (items: Vote[], cast = items.length): VotesPage => ({ ...page(items), cast, quorum: 3 }) as VotesPage;
export const stepsPage = (items: Step[]): StepsPage =>
  ({
    ...page(items),
    quorum: 3,
    levels: [{ level: 1, role: "manager", timeout_minutes: 60 }, { level: 2, role: "org_admin", timeout_minutes: 60 }],
  }) as StepsPage;

type Handler = unknown | ((init: RequestInit, url: string) => Promise<Response> | unknown);

/**
 * Stubs fetch by "METHOD /path" (query included only when the key has one). A handler is a body (200), a Response promise
 * (from json/failure) or a function of the request; unrouted calls are 404s. Calls are recorded as [method, url, init].
 */
export function serve(routes: Record<string, Handler>) {
  const calls: [string, string, RequestInit][] = [];
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = url.replace("/api/v1", "");
    calls.push([method, path, init]);
    const key = [`${method} ${path}`, `${method} ${path.split("?")[0]}`].find((k) => k in routes);
    if (!key) return failure(404, "not_found");
    const h = routes[key];
    const out = typeof h === "function" ? (h as (i: RequestInit, u: string) => unknown)(init, path) : h;
    return out instanceof Promise || out instanceof Response ? out : json(200, out);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls, count: (key: string) => calls.filter(([m, p]) => `${m} ${p}` === key || `${m} ${p.split("?")[0]}` === key).length };
}

/** Renders inside the app context a screen expects from the shell. */
export function renderInApp(ui: ReactElement, { role = "manager", refreshInbox = vi.fn() } = {}) {
  const view = render(
    <AppContext.Provider value={{ session: { user_id: "u1", organization_id: "o1", role }, refreshInbox }}>{ui}</AppContext.Provider>,
  );
  return { ...view, refreshInbox };
}
