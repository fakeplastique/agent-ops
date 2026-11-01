import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Inbox } from "@/components/Inbox/Inbox";

import { decision, failure, json, page, pending, renderInApp, serve, step, stepsPage, vote, votesPage } from "./helpers";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const AGENTS = page([{ id: "a1", name: "billing-agent", status: "active" }]);
const PENDING = page([
  pending(),
  pending({ id: "st9", decision_request_id: "d2", title: "Rotate staging API keys", effective_risk_level: "medium", due_at: new Date(Date.now() + 133 * 60_000).toISOString() }),
]);

// Routes for the open request d1: its votes make a 2-1 split, level 1 is the pending step.
const detail = (over: Record<string, unknown> = {}) => ({
  "GET /decision-requests/d1": decision({ version: 1 }),
  "GET /decision-requests/d1/votes": votesPage([vote("approve"), vote("approve"), vote("reject")]),
  "GET /decision-requests/d1/steps": stepsPage([step(1)]),
  ...over,
});
const base = (over: Record<string, unknown> = {}) => ({
  "GET /agents": AGENTS,
  "GET /me/pending-approvals": PENDING,
  ...detail(),
  ...over,
});

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

async function openFirst() {
  const row = await screen.findByRole("button", { name: /Refund order #48213/ });
  await userEvent.click(row);
  return row;
}

describe("Inbox list", () => {
  it("nothing waiting says so in the trade's words, with no Retry", async () => {
    serve(base({ "GET /me/pending-approvals": page([]) }));
    renderInApp(<Inbox />);
    expect(await screen.findByText("Nothing is waiting on you")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  // catches: a failed read rendered as an empty inbox (FM32)
  it("a failed list read shows Retry and never the empty message", async () => {
    serve(base({ "GET /me/pending-approvals": failure(503, "unavailable") }));
    renderInApp(<Inbox />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing is waiting on you")).not.toBeInTheDocument();
  });

  it("lists steps in the API's due order with agent, risk, level and time left", async () => {
    serve(base());
    renderInApp(<Inbox />);
    const rows = await screen.findAllByRole("button", { name: /Refund order|Rotate staging/ });
    expect(rows[0]).toHaveTextContent("Refund order #48213");
    expect(rows[0]).toHaveTextContent("billing-agent");
    expect(rows[0]).toHaveTextContent("High risk");
    expect(rows[0]).toHaveTextContent("Level 1 · Manager");
    expect(rows[0]).toHaveTextContent(/4[89] min left/);
    expect(rows[1]).toHaveTextContent("2 h");
  });

  // catches: layout break from a long agent-authored title; the full text must stay reachable
  it("a long title truncates on one line and keeps the full text", async () => {
    const long = "Delete 4,102 inactive trial accounts and every attachment they ever uploaded to the shared workspace";
    serve(base({ "GET /me/pending-approvals": page([pending({ title: long })]) }));
    renderInApp(<Inbox />);
    const title = await screen.findByText(long);
    expect(title).toHaveClass("truncate");
    expect(title).toHaveAttribute("title", long);
  });

  it("arrow keys move between rows, Enter opens, and the pane shows the request", async () => {
    const user = userEvent.setup();
    serve(base());
    renderInApp(<Inbox />);
    const rows = await screen.findAllByRole("button", { name: /Refund order|Rotate staging/ });
    rows[0].focus();
    await user.keyboard("{ArrowDown}");
    expect(rows[1]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(rows[0]).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { level: 2, name: "Refund order #48213" })).toBeInTheDocument();
    expect(rows[0]).toHaveAttribute("aria-current", "true");
  });

  it("A and R shortcuts are announced, can be turned off, and then do nothing", async () => {
    const user = userEvent.setup();
    const s = serve(base({ "POST /approval-steps/st1/decide": { step_id: "st1", status: "approved", request_status: "approved", next_step_id: null } }));
    renderInApp(<Inbox />);
    await openFirst();
    await screen.findByRole("button", { name: "Approve" });
    expect(screen.getByText(/Shortcuts: A approves, R rejects/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn shortcuts off" }));
    await user.keyboard("a");
    expect(s.count("POST /approval-steps/st1/decide")).toBe(0);
    expect(screen.getByText(/Shortcuts are off/)).toBeInTheDocument();
  });

  it("A approves from the keyboard through the same button", async () => {
    const user = userEvent.setup();
    const s = serve(base({ "POST /approval-steps/st1/decide": { step_id: "st1", status: "approved", request_status: "approved", next_step_id: null } }));
    renderInApp(<Inbox />);
    const row = await openFirst();
    await screen.findByRole("button", { name: "Approve" });
    row.focus();
    await user.keyboard("a");
    await waitFor(() => expect(s.count("POST /approval-steps/st1/decide")).toBe(1));
  });
});

describe("Inbox pane", () => {
  it("shows the finding, the rail and the votes of the open request", async () => {
    serve(base());
    renderInApp(<Inbox />);
    await openFirst();
    expect(await screen.findByRole("region", { name: "Decision progress" })).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 reviewers rejected it/)).toBeInTheDocument();
    expect(screen.getByText(/rationale reject/)).toBeInTheDocument();
  });

  // catches: a verdict (or a count of approvals) shown before the round closes
  it("a blind round shows no verdict, rationale or approval count", async () => {
    serve(base({
      "GET /decision-requests/d1": decision({ status: "pending_votes" }),
      "GET /decision-requests/d1/votes": votesPage([], 2),
      "GET /decision-requests/d1/steps": stepsPage([]),
    }));
    renderInApp(<Inbox />);
    await openFirst();
    await screen.findByRole("region", { name: "Decision progress" });
    expect(screen.getAllByText(/hidden until/i).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/rationale|reviewers? (approved|rejected)|rejected it|\bapprove\b/i);
  });

  it("a pane read that fails shows Retry for the pane, not an empty pane", async () => {
    serve(base({ "GET /decision-requests/d1": failure(500, "boom") }));
    renderInApp(<Inbox />);
    await openFirst();
    expect(await screen.findAllByRole("button", { name: "Retry" })).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

describe("Inbox decide", () => {
  const ok = { step_id: "st1", status: "approved", request_status: "approved", next_step_id: null };

  // catches: reject without a reason
  it("Reject stays disabled until a comment exists", async () => {
    const user = userEvent.setup();
    serve(base());
    renderInApp(<Inbox />);
    await openFirst();
    const reject = await screen.findByRole("button", { name: "Reject" });
    expect(reject).toBeDisabled();
    await user.type(screen.getByLabelText(/Comment/), "Above the limit");
    expect(reject).toBeEnabled();
    await user.clear(screen.getByLabelText(/Comment/));
    await user.type(screen.getByLabelText(/Comment/), "   ");
    expect(reject).toBeDisabled();
  });

  it("approve sends version, decision and comment with an Idempotency-Key, then shows the outcome and refreshes the count", async () => {
    const user = userEvent.setup();
    const s = serve(base({ "POST /approval-steps/st1/decide": ok }));
    const { refreshInbox } = renderInApp(<Inbox />);
    await openFirst();
    await user.type(await screen.findByLabelText(/Comment/), "Looks right");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const [, , init] = s.calls.find(([m]) => m === "POST")!;
    expect(JSON.parse(String(init.body))).toEqual({ request_version: 1, decision: "approve", comment: "Looks right" });
    expect(new Headers(init.headers).get("idempotency-key")).toMatch(/\S{8,}/);
    expect(await screen.findByText(/You approved this/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(refreshInbox).toHaveBeenCalled();
  });

  // catches: duplicate decide on a double click
  it("a double click sends one request with one key", async () => {
    const user = userEvent.setup();
    let release: (r: Response) => void = () => {};
    const s = serve(base({ "POST /approval-steps/st1/decide": () => new Promise<Response>((r) => (release = r)) }));
    renderInApp(<Inbox />);
    await openFirst();
    const approve = await screen.findByRole("button", { name: "Approve" });
    await user.dblClick(approve);
    expect(s.count("POST /approval-steps/st1/decide")).toBe(1);
    release(await json(200, ok));
    await screen.findByText(/You approved this/);
  });

  // catches: a second decision attempt on a pane whose step was decided elsewhere
  it("409 already decided removes the buttons and shows the outcome", async () => {
    const user = userEvent.setup();
    let decided = false;
    serve(base({
      "POST /approval-steps/st1/decide": () => { decided = true; return failure(409, "already_decided"); },
      "GET /decision-requests/d1": () => json(200, decision({ status: decided ? "approved" : "pending_approval", decided_at: decided ? "2026-11-03T14:30:00Z" : null })),
    }));
    renderInApp(<Inbox />);
    await openFirst();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/already decided/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(await screen.findByText("Approved", { selector: "span" })).toBeInTheDocument();
  });

  it("a stale version says 'This request changed' and Reload re-reads it", async () => {
    const user = userEvent.setup();
    const s = serve(base({ "POST /approval-steps/st1/decide": failure(409, "stale_version") }));
    renderInApp(<Inbox />);
    await openFirst();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This request changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    const before = s.count("GET /decision-requests/d1");
    await user.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(s.count("GET /decision-requests/d1")).toBeGreaterThan(before));
  });

  it("a request already revised past the listed step is stale before any press", async () => {
    serve(base({ "GET /decision-requests/d1": decision({ version: 2 }) }));
    renderInApp(<Inbox />);
    await openFirst();
    expect(await screen.findByText("This request changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  // catches: blind retries while offline
  it("the first network failure disables both actions until the connection is checked", async () => {
    const user = userEvent.setup();
    const s = serve(base({ "POST /approval-steps/st1/decide": () => Promise.reject(new TypeError("offline")) }));
    renderInApp(<Inbox />);
    await openFirst();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/Nothing changed\. Check your connection/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(s.count("POST /approval-steps/st1/decide")).toBe(1);
    await user.click(screen.getByRole("button", { name: "Check connection" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
  });

  it("a forbidden decision names the rule and keeps nothing half-done", async () => {
    const user = userEvent.setup();
    serve(base({ "POST /approval-steps/st1/decide": failure(403, "forbidden", "not allowed to decide this step") }));
    renderInApp(<Inbox />);
    await openFirst();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/can't decide this step/i)).toBeInTheDocument();
  });
});

// Review A1-A4: stale reload, shortcuts that act only on what is open and visible, and the target named.
describe("Inbox review fixes", () => {
  const ok = { step_id: "st1", status: "approved", request_status: "approved", next_step_id: null };
  const three = page([
    pending({ id: "st1", decision_request_id: "d1", title: "First request" }),
    pending({ id: "st2", decision_request_id: "d2", title: "Second request" }),
    pending({ id: "st3", decision_request_id: "d3", title: "Third request" }),
  ]);
  const threeRoutes = (over: Record<string, unknown> = {}) => {
    const r: Record<string, unknown> = { "GET /agents": AGENTS, "GET /me/pending-approvals": three, ...over };
    for (const n of [1, 2, 3]) {
      r[`GET /decision-requests/d${n}`] = decision({ id: `d${n}`, title: `${["", "First", "Second", "Third"][n]} request` });
      r[`GET /decision-requests/d${n}/votes`] = votesPage([vote("approve")]);
      r[`GET /decision-requests/d${n}/steps`] = stepsPage([step(1, "pending", { id: `st${n}` })]);
      r[`POST /approval-steps/st${n}/decide`] = { ...ok, step_id: `st${n}` };
    }
    return { ...r, ...over };
  };

  // catches: focus row 3, press A, row 1 approved (A3)
  it("arrow keys open the row they move to, so A decides the row that has focus", async () => {
    const user = userEvent.setup();
    const s = serve(threeRoutes());
    renderInApp(<Inbox />);
    const rows = await screen.findAllByRole("button", { name: /request/ });
    rows[0].focus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(rows[2]).toHaveFocus();
    expect(rows[2]).toHaveAttribute("aria-current", "true");
    await screen.findByRole("heading", { level: 2, name: "Third request" });
    await user.keyboard("a");
    await waitFor(() => expect(s.count("POST /approval-steps/st3/decide")).toBe(1));
    expect(s.count("POST /approval-steps/st1/decide")).toBe(0);
  });

  it("a row that has focus but is not open never lets A act on the open one, and says to open it first", async () => {
    const user = userEvent.setup();
    const s = serve(threeRoutes());
    renderInApp(<Inbox />);
    const rows = await screen.findAllByRole("button", { name: /request/ });
    await screen.findByRole("button", { name: "Approve" }); // row 1 is open
    rows[2].focus(); // Tab-style focus: nothing was opened
    await user.keyboard("a");
    expect(s.calls.filter(([m]) => m === "POST")).toHaveLength(0);
    expect(screen.getByRole("status", { name: "Shortcut" })).toHaveTextContent(/Open “Third request” first/);
  });

  // catches: an approval from a pane nobody can see (A2)
  it("on a narrow screen with the list showing, A and R do nothing", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
    const s = serve(threeRoutes());
    renderInApp(<Inbox />);
    const rows = await screen.findAllByRole("button", { name: /request/ });
    await screen.findByRole("button", { name: "Approve" }); // mounted, but hidden by CSS at this width
    rows[0].focus();
    await user.keyboard("a");
    expect(s.calls.filter(([m]) => m === "POST")).toHaveLength(0);
    await user.click(rows[0]); // opening the request shows the pane
    await user.keyboard("a");
    await waitFor(() => expect(s.count("POST /approval-steps/st1/decide")).toBe(1));
  });

  it("the shortcut hint names the request it would decide", async () => {
    serve(threeRoutes());
    renderInApp(<Inbox />);
    expect(await screen.findByText(/Shortcuts: A approves, R rejects “First request”/)).toBeInTheDocument();
  });

  // catches: a Reload that cannot clear the notice because the step stays a snapshot of the old version (A1)
  it("Reload re-reads the inbox so a newer version replaces the stale step and the notice clears", async () => {
    const user = userEvent.setup();
    let revised = false;
    serve(base({
      "GET /me/pending-approvals": () => json(200, page([revised ? pending({ id: "st1b", request_version: 2 }) : pending()])),
      "GET /decision-requests/d1": () => json(200, decision({ version: revised ? 2 : 1 })),
      "GET /decision-requests/d1/steps": () => json(200, stepsPage([revised ? step(1, "pending", { id: "st1b", request_version: 2 }) : step(1)])),
      "POST /approval-steps/st1/decide": () => { revised = true; return failure(409, "stale_version"); },
    }));
    renderInApp(<Inbox />);
    await openFirst();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This request changed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(screen.queryByText("This request changed")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "Approve" })).toBeEnabled();
  });
});
