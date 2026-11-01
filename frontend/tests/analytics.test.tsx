import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalyticsView } from "@/app/(app)/analytics/AnalyticsView";

import { err, mockApi, session } from "./api-mock";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

const PLAN = "GET /organizations/current/plan";
const SUMMARY = "GET /stats/summary?days=30";
const SERIES = "GET /stats/timeseries?days=30";
const plan = (flags: string[]) => ({
  plan: flags.includes("analytics") ? "pro" : "free",
  limits: { requests_per_month: null, max_quorum: null, max_rounds: 2, max_levels: 5 },
  usage: { requests: { used: 0, limit: null, period: "2026-11" } },
  flags,
});
const empty = {
  first_day: "2026-10-05",
  last_day: "2026-11-03",
  days: 30,
  requests: 0,
  by_status: {},
  by_risk: {},
  auto_approval_rate: null,
  median_seconds_to_decision: null,
  average_seconds_to_decision: null,
  votes_per_agent: [],
};
const point = (date: string, requests: number) => ({ date, requests, approved: 0, rejected: 0, auto_approved: 0 });
const emptySeries = { first_day: "2026-10-05", last_day: "2026-11-03", days: 30, points: Array.from({ length: 30 }, (_, i) => point(`2026-10-${String(i + 1).padStart(2, "0")}`, 0)) };
const routes = (over: Record<string, unknown> = {}) => ({
  "GET /auth/session": session("member"),
  [PLAN]: plan(["analytics"]),
  [SUMMARY]: empty,
  [SERIES]: emptySeries,
  "GET /auth/demo/users": err(404, "not_found", "nf"),
  ...over,
});

describe("Analytics", () => {
  it("no data shows zeros and dashes and draws no series", async () => {
    mockApi(routes());
    render(<AnalyticsView />);
    const requests = (await screen.findByText("Requests")).closest("div")!;
    expect(within(requests).getByText("0")).toBeInTheDocument();
    const rate = screen.getByText("Auto-approval rate").closest("div")!;
    expect(within(rate).getByText("—")).toBeInTheDocument();
    const median = screen.getByText("Median time to decision").closest("div")!;
    expect(within(median).getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/No requests in the last 30 days/)).toBeInTheDocument();
    // catches: invented points. No chart image, and no bar has any length.
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    for (const bar of document.querySelectorAll("[data-bar]")) expect(bar).toHaveAttribute("data-value", "0");
    expect(screen.getByText(/No votes yet/)).toBeInTheDocument();
  });

  it("with data: KPIs, status and risk bars, a daily series and votes per agent", async () => {
    mockApi(
      routes({
        [SUMMARY]: {
          ...empty,
          requests: 20,
          by_status: { approved: 12, rejected: 3, pending_approval: 5 },
          by_risk: { low: 10, medium: 6, high: 4 },
          auto_approval_rate: 0.35,
          median_seconds_to_decision: 270,
          votes_per_agent: [{ agent_id: "a1", name: "refund-bot", votes: 41 }],
        },
        [SERIES]: { ...emptySeries, points: [point("2026-11-01", 4), point("2026-11-02", 0), point("2026-11-03", 16)] },
      }),
    );
    render(<AnalyticsView />);
    expect(await screen.findByText("20")).toBeInTheDocument();
    expect(screen.getByText("35%")).toBeInTheDocument();
    expect(screen.getByText("4 min 30 s")).toBeInTheDocument();
    const approved = screen.getByText("Approved").closest("li")!;
    expect(within(approved).getByText("12")).toBeInTheDocument();
    expect(screen.getByText("refund-bot")).toBeInTheDocument();
    // The series is exactly the three points the API sent, summarised for assistive tech.
    const chart = screen.getByRole("img", { name: /Requests per day.*peak 16 on 3 Nov/ });
    expect(chart.querySelectorAll("rect")).toHaveLength(3);
  });

  it("Free shows the plan gate and makes no stats request", async () => {
    const api = mockApi(routes({ [PLAN]: plan([]) }));
    render(<AnalyticsView />);
    expect(await screen.findByRole("heading", { name: "Analytics is on the Pro plan" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.key.includes("/stats/"))).toBe(false);
  });

  it("a 402 from the stats API (plan changed under us) shows the same gate", async () => {
    mockApi(routes({ [SUMMARY]: err(402, "plan_feature_required", "pro", { flag: "analytics", required_plan: "pro" }) }));
    render(<AnalyticsView />);
    expect(await screen.findByRole("heading", { name: "Analytics is on the Pro plan" })).toBeInTheDocument();
  });

  it("a failed stats read is an error with Retry, never zeros", async () => {
    mockApi(routes({ [SUMMARY]: err(500, "internal", "boom") }));
    render(<AnalyticsView />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Auto-approval rate")).not.toBeInTheDocument();
  });

  it("the window can change to 7 or 90 days and asks for exactly that", async () => {
    const api = mockApi(routes({ "GET /stats/summary?days=7": { ...empty, days: 7 }, "GET /stats/timeseries?days=7": emptySeries }));
    const { default: userEvent } = await import("@testing-library/user-event");
    render(<AnalyticsView />);
    await userEvent.setup().selectOptions(await screen.findByLabelText("Period"), "7");
    expect(await screen.findByText(/No requests in the last 7 days/)).toBeInTheDocument();
    expect(api.count("GET /stats/summary?days=7")).toBe(1);
  });
});
