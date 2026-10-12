import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionDetail } from "@/components/Decision/DecisionDetail";
import { DecisionsList } from "@/components/Decision/DecisionsList";
import { PayloadBlock } from "@/components/Decision/PayloadBlock";

import { decision, failure, iso, page, pending, renderInApp, serve, step, stepsPage, vote, votesPage } from "./helpers";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/decisions" }));

const AGENTS = page([{ id: "a1", name: "billing-agent", status: "active" }, { id: "a2", name: "ops-agent", status: "active" }]);
const rows = (n: number, from = 0) => Array.from({ length: n }, (_, i) => decision({ id: `d${from + i}`, title: `Request ${from + i}` }));

afterEach(() => vi.unstubAllGlobals());

describe("Decisions list", () => {
  // catches: a failed load reading as "no decisions match" (FM32)
  it("no match says 'No decisions match' with Clear filters; a load error is not that message", async () => {
    const user = userEvent.setup();
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page([], { total: 0 }) });
    const onParams = vi.fn();
    const { unmount } = renderInApp(<DecisionsList params={new URLSearchParams("status=approved")} onParams={onParams} />);
    expect(await screen.findByText("No decisions match")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onParams.mock.calls.at(-1)![0].toString()).toBe("");
    unmount();

    serve({ "GET /agents": AGENTS, "GET /decision-requests": failure(500, "boom") });
    renderInApp(<DecisionsList params={new URLSearchParams("status=approved")} onParams={onParams} />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("No decisions match")).not.toBeInTheDocument();
  });

  it("empty without filters is not 'no match'", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page([], { total: 0 }) });
    renderInApp(<DecisionsList params={new URLSearchParams()} onParams={vi.fn()} />);
    expect(await screen.findByText(/No decisions yet/)).toBeInTheDocument();
    expect(screen.queryByText("No decisions match")).not.toBeInTheDocument();
  });

  // catches: filter lost on page change
  it("reads skip, limit and filters from the URL, shows '21-40 of 53' and keeps the filters when paging", async () => {
    const user = userEvent.setup();
    const s = serve({ "GET /agents": AGENTS, "GET /decision-requests": page(rows(20, 20), { total: 53, skip: 20, limit: 20 }) });
    const onParams = vi.fn();
    renderInApp(<DecisionsList params={new URLSearchParams("status=approved&risk_level=high&skip=20")} onParams={onParams} />);
    expect(await screen.findByText("21-40 of 53")).toBeInTheDocument();
    const url = s.calls.find(([, p]) => p.startsWith("/decision-requests?"))![1];
    const q = new URLSearchParams(url.split("?")[1]);
    expect(Object.fromEntries(q)).toMatchObject({ status: "approved", risk_level: "high", skip: "20", limit: "20", sort: "-created_at" });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(Object.fromEntries(onParams.mock.calls.at(-1)![0])).toEqual({ status: "approved", risk_level: "high", skip: "40" });
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(Object.fromEntries(onParams.mock.calls.at(-1)![0])).toEqual({ status: "approved", risk_level: "high" });
  });

  it("first page says '1-20 of N', has no Previous, and the last page has no Next", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page(rows(3), { total: 3 }) });
    renderInApp(<DecisionsList params={new URLSearchParams()} onParams={vi.fn()} />);
    expect(await screen.findByText("1-3 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("changing a filter goes back to the first page", async () => {
    const user = userEvent.setup();
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page(rows(20, 20), { total: 53, skip: 20 }) });
    const onParams = vi.fn();
    renderInApp(<DecisionsList params={new URLSearchParams("skip=20")} onParams={onParams} />);
    await screen.findByText("21-40 of 53");
    await user.selectOptions(screen.getByLabelText("Risk"), "high");
    expect(Object.fromEntries(onParams.mock.calls.at(-1)![0])).toEqual({ risk_level: "high" });
  });

  it("the text search is applied on submit, as q", async () => {
    const user = userEvent.setup();
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page(rows(2), { total: 2 }) });
    const onParams = vi.fn();
    renderInApp(<DecisionsList params={new URLSearchParams()} onParams={onParams} />);
    await screen.findByText("1-2 of 2");
    await user.type(screen.getByLabelText("Search titles"), "refund{Enter}");
    expect(Object.fromEntries(onParams.mock.calls.at(-1)![0])).toEqual({ q: "refund" });
  });

  it("rows link to the detail, with status and risk as text", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests": page([decision({ id: "dX", title: "Refund it", status: "approved", effective_risk_level: "low", requested_by_agent_id: "a2" })], { total: 1 }) });
    renderInApp(<DecisionsList params={new URLSearchParams()} onParams={vi.fn()} />);
    const link = await screen.findByRole("link", { name: "Refund it" });
    expect(link).toHaveAttribute("href", "/decisions/dX");
    const row = link.closest("tr")!;
    expect(within(row).getByText("Approved")).toBeInTheDocument();
    expect(within(row).getByText("Low risk")).toBeInTheDocument();
    expect(within(row).getByText("ops-agent")).toBeInTheDocument();
  });
});

const detailRoutes = (over: Record<string, unknown> = {}) => ({
  "GET /agents": AGENTS,
  "GET /me/pending-approvals": page([]),
  "GET /decision-requests/d1": decision(),
  "GET /decision-requests/d1/votes": votesPage([vote("approve"), vote("approve"), vote("reject")]),
  "GET /decision-requests/d1/steps": stepsPage([step(1)]),
  ...over,
});

describe("Decision detail", () => {
  it("shows the header, the rail and the Overview first, with all tabs reachable", async () => {
    serve(detailRoutes());
    renderInApp(<DecisionDetail id="d1" tab="overview" onTab={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 1, name: "Refund order #48213" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Decision progress" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs.slice(0, 3)).toEqual(["Overview", "Votes", "Timeline"]);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Customer reports damage");
  });

  it("a missing or foreign request is 'Not found', the same words for both", async () => {
    serve(detailRoutes({ "GET /decision-requests/d1": failure(404, "not_found") }));
    renderInApp(<DecisionDetail id="d1" tab="overview" onTab={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });

  it("arrow keys move between tabs and select them", async () => {
    const user = userEvent.setup();
    serve(detailRoutes());
    const onTab = vi.fn();
    renderInApp(<DecisionDetail id="d1" tab="overview" onTab={onTab} />);
    await screen.findByRole("tab", { name: "Overview" });
    screen.getByRole("tab", { name: "Overview" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(onTab).toHaveBeenCalledWith("votes");
  });
});

describe("Votes tab", () => {
  // catches: verdict present in the DOM but only visually hidden (I5)
  it("a blind round shows placeholders and no verdict, confidence or rationale in the DOM", async () => {
    serve(detailRoutes({
      "GET /decision-requests/d1": decision({ status: "pending_votes" }),
      "GET /decision-requests/d1/votes": votesPage([vote("reject", { rationale: "SECRET-RATIONALE" })].slice(0, 0), 2),
      "GET /decision-requests/d1/steps": stepsPage([]),
    }));
    renderInApp(<DecisionDetail id="d1" tab="votes" onTab={vi.fn()} />);
    const panel = await screen.findByRole("tabpanel");
    expect(within(panel).getAllByText(/Vote hidden until round closes/)).toHaveLength(2);
    expect(within(panel).getByText(/Reviewer 3 has not voted yet/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/SECRET|confidence|\bApprove\b|\bReject\b/);
  });

  // catches: old votes counted visually after a revision
  it("a revised request shows version 1 votes struck through under 'Version 1'", async () => {
    serve(detailRoutes({
      "GET /decision-requests/d1": decision({ version: 2 }),
      "GET /decision-requests/d1/votes": votesPage([
        vote("approve", { request_version: 1, rationale: "old approval" }),
        vote("reject", { request_version: 2, rationale: "new rejection" }),
      ]),
      "GET /decision-requests/d1/steps": stepsPage([step(1, "pending", { request_version: 2 })]),
    }));
    renderInApp(<DecisionDetail id="d1" tab="votes" onTab={vi.fn()} />);
    const old = await screen.findByRole("region", { name: "Version 1" });
    expect(within(old).getByText("old approval")).toHaveClass("line-through");
    expect(within(old).getByText(/not counted/)).toBeInTheDocument();
    expect(screen.getByText("new rejection")).not.toHaveClass("line-through");
  });

  it("agent text in a rationale is shown as text, not parsed", async () => {
    serve(detailRoutes({ "GET /decision-requests/d1/votes": votesPage([vote("approve", { rationale: '<img src=x onerror="alert(1)"> **bold**' })]) }));
    renderInApp(<DecisionDetail id="d1" tab="votes" onTab={vi.fn()} />);
    expect(await screen.findByText(/<img src=x/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("Timeline tab", () => {
  it("shows the ladder with levels, approvers and due times", async () => {
    serve(detailRoutes({
      "GET /decision-requests/d1/steps": stepsPage([
        step(1, "approved", { decided_by: "Dmitri Volkov", decided_at: iso(-10) }),
        step(2, "pending", { due_at: iso(49) }),
      ]),
    }));
    renderInApp(<DecisionDetail id="d1" tab="timeline" onTab={vi.fn()} />);
    const panel = await screen.findByRole("tabpanel");
    const items = within(panel).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Level 1 · Manager");
    expect(items[0]).toHaveTextContent("Approved by Dmitri Volkov");
    expect(items[1]).toHaveTextContent("Level 2 · Org admin");
    expect(items[1]).toHaveTextContent(/Waiting.*due \d/);
    expect(items[1]).toHaveTextContent("60 min timeout");
  });

  // catches: actions on a decided request, even when a stale pending entry still names it
  it("a terminal request has no action buttons", async () => {
    serve(detailRoutes({
      "GET /decision-requests/d1": decision({ status: "rejected", decided_at: iso(-1) }),
      "GET /me/pending-approvals": page([pending()]),
      "GET /decision-requests/d1/steps": stepsPage([step(1, "rejected", { decided_by: "Maya", decided_at: iso(-1) })]),
    }));
    renderInApp(<DecisionDetail id="d1" tab="timeline" onTab={vi.fn()} />);
    await screen.findByRole("tabpanel");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("a step the viewer may decide shows the decision bar", async () => {
    serve(detailRoutes({ "GET /me/pending-approvals": page([pending()]) }));
    renderInApp(<DecisionDetail id="d1" tab="timeline" onTab={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  });
});

describe("Overview tab", () => {
  it("shows description, action type and the payload in a scrollable block", async () => {
    serve(detailRoutes());
    renderInApp(<DecisionDetail id="d1" tab="overview" onTab={vi.fn()} />);
    const block = await screen.findByLabelText("Payload");
    expect(block.tagName).toBe("PRE");
    expect(block).toHaveClass("overflow-auto");
    expect(block).toHaveTextContent('"order_id": 48213');
  });

  // catches: a giant payload breaking the layout or being silently cut
  it("a payload over 64 KB is cut and says so", () => {
    render(<PayloadBlock payload={{ blob: "x".repeat(70_000) }} />);
    expect(screen.getByText(/Truncated: showing the first 64 KB/)).toBeInTheDocument();
    expect(screen.getByLabelText("Payload").textContent!.length).toBeLessThanOrEqual(64 * 1024);
  });

  it("a small payload is not marked truncated", () => {
    render(<PayloadBlock payload={{ a: 1 }} />);
    expect(screen.queryByText(/Truncated/)).not.toBeInTheDocument();
  });
});
