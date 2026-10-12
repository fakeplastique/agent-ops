import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionDetail } from "@/components/Decision/DecisionDetail";
import { Attachments } from "@/components/Decision/tabs/Attachments";
import { Audit } from "@/components/Decision/tabs/Audit";
import { Discussion } from "@/components/Decision/tabs/Discussion";
import { Receipt } from "@/components/Decision/tabs/Receipt";

import { decision, failure, iso, page, renderInApp, serve, step, stepsPage, vote, votesPage } from "./helpers";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/decisions/d1" }));

afterEach(() => vi.unstubAllGlobals());

const AGENTS = page([{ id: "a1", name: "billing-agent", status: "active" }]);
const msg = (o: Record<string, unknown> = {}) => ({
  id: `m${Math.random()}`, author_type: "agent", author_id: "a1", body: "hello", request_version: 1, created_at: iso(-5), ...o,
});
const OPEN = decision({ status: "pending_approval" });

describe("Discussion", () => {
  // catches: HTML or link injection from agent text (FM25)
  it("<img onerror>, markdown and javascript: links render as inert text", async () => {
    serve({
      "GET /agents": AGENTS,
      "GET /decision-requests/d1/messages": page([
        msg({ body: '<img src=x onerror="alert(1)">' }),
        msg({ body: "**bold** [click](javascript:alert(1)) <script>x()</script>" }),
      ]),
    });
    renderInApp(<Discussion decision={OPEN} />);
    expect(await screen.findByText(/<img src=x onerror/)).toBeInTheDocument();
    expect(screen.getByText(/\*\*bold\*\* \[click\]\(javascript:alert\(1\)\)/)).toBeInTheDocument();
    expect(document.querySelector("img, script, strong, b")).toBeNull();
    expect(document.querySelector("a[href^='javascript']")).toBeNull();
    expect(screen.queryByRole("link", { name: "click" })).not.toBeInTheDocument();
  });

  // catches: posting that the API will reject
  it("posting is disabled with an explanation while the thread is system-only", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests/d1/messages": page([msg({ author_type: "system", author_id: null, body: "Voting opened" })]) });
    renderInApp(<Discussion decision={decision({ status: "pending_votes" })} />);
    expect(await screen.findByText("Voting opened")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Post" })).toBeDisabled();
    expect(screen.getByText(/Reviewers vote privately first/)).toBeInTheDocument();
  });

  it("a decided request is read-only and says why", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests/d1/messages": page([msg()]) });
    renderInApp(<Discussion decision={decision({ status: "approved" })} />);
    await screen.findByText("hello");
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByText(/already decided/)).toBeInTheDocument();
  });

  it("an open thread posts the body, clears the box and re-reads the thread", async () => {
    const user = userEvent.setup();
    const s = serve({
      "GET /agents": AGENTS,
      "GET /decision-requests/d1/messages": page([]),
      "POST /decision-requests/d1/messages": { id: "m9", request_version: 1, author_type: "user" },
    });
    renderInApp(<Discussion decision={OPEN} />);
    const box = await screen.findByLabelText("Message");
    expect(screen.getByRole("button", { name: "Post" })).toBeDisabled(); // nothing typed
    await user.type(box, "Please attach the delivery photo");
    await user.click(screen.getByRole("button", { name: "Post" }));
    const [, , init] = s.calls.find(([m]) => m === "POST")!;
    expect(JSON.parse(String(init.body))).toEqual({ body: "Please attach the delivery photo" });
    await waitFor(() => expect(box).toHaveValue(""));
    await waitFor(() => expect(s.count("GET /decision-requests/d1/messages")).toBe(2));
  });

  it("a rejected post keeps the text and names what happened", async () => {
    const user = userEvent.setup();
    serve({ "GET /agents": AGENTS, "GET /decision-requests/d1/messages": page([]), "POST /decision-requests/d1/messages": failure(409, "discussion_closed", "raw server text") });
    renderInApp(<Discussion decision={OPEN} />);
    await user.type(await screen.findByLabelText("Message"), "late note");
    await user.click(screen.getByRole("button", { name: "Post" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The discussion is closed");
    expect(screen.getByLabelText("Message")).toHaveValue("late note");
    expect(document.body.textContent).not.toContain("raw server text");
  });

  it("an empty thread says so, and a failed read is not that message", async () => {
    serve({ "GET /agents": AGENTS, "GET /decision-requests/d1/messages": failure(500, "boom") });
    renderInApp(<Discussion decision={OPEN} />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/No messages yet/)).not.toBeInTheDocument();
  });
});

const RECEIPT = { alg: "HS256", kid: "k1", payload: { request_id: "d1", amount: 1240 }, signature: "sigsigsig" };
const approved = decision({ status: "approved", decided_at: iso(-2) });

describe("Receipt", () => {
  // catches: a verify result cached from before the request changed
  it("Verify shows valid, then the result is dropped when the request changes and verifies as broken", async () => {
    const user = userEvent.setup();
    let valid = true;
    serve({
      "GET /decision-requests/d1/receipt": RECEIPT,
      "POST /receipts/verify": () => ({ valid }),
    });
    const view = renderInApp(<Receipt decision={approved} />);
    await screen.findByText("sigsigsig");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/Valid\. It matches/)).toBeInTheDocument();

    valid = false;
    view.rerender(
      <Receipt decision={{ ...approved, content_hash: "changed-hash", updated_at: iso(1) }} />,
    );
    expect(screen.queryByText(/Valid\. It matches/)).not.toBeInTheDocument();
    await screen.findByText("sigsigsig");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/Broken\. The request no longer matches/)).toBeInTheDocument();
  });

  it("verify posts the receipt object itself", async () => {
    const user = userEvent.setup();
    const s = serve({ "GET /decision-requests/d1/receipt": RECEIPT, "POST /receipts/verify": { valid: true } });
    renderInApp(<Receipt decision={approved} />);
    await user.click(await screen.findByRole("button", { name: "Verify" }));
    const [, , init] = s.calls.find(([m]) => m === "POST")!;
    expect(JSON.parse(String(init.body))).toEqual(RECEIPT);
  });

  it("a request without a receipt makes no receipt call and says why", () => {
    const s = serve({});
    const { unmount } = renderInApp(<Receipt decision={decision({ status: "rejected" })} />);
    expect(screen.getByText(/No receipt is issued/)).toBeInTheDocument();
    unmount();
    renderInApp(<Receipt decision={OPEN} />);
    expect(screen.getByText(/signed when the request is approved/i)).toBeInTheDocument();
    expect(s.calls).toHaveLength(0);
  });

  it("a failed verify call is an error with a retry, never 'broken'", async () => {
    const user = userEvent.setup();
    serve({ "GET /decision-requests/d1/receipt": RECEIPT, "POST /receipts/verify": failure(503, "unavailable") });
    renderInApp(<Receipt decision={approved} />);
    await user.click(await screen.findByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not verify");
    expect(screen.queryByText(/Broken/)).not.toBeInTheDocument();
  });
});

const FILE = { id: "f1", decision_request_id: "d1", filename: "<b>photo</b>.png", content_type: "image/png", size_bytes: 2048, uploaded_by_type: "agent", uploaded_by_id: "a1", created_at: iso(-4) };

describe("Attachments", () => {
  // catches: a dead-end for Free: the gate names the plan and the way up
  it("Free shows the plan gate", async () => {
    serve({ "GET /decision-requests/d1/attachments": failure(402, "plan_feature_required", "x", { required_plan: "pro", flag: "attachments" }) });
    renderInApp(<Attachments decision={OPEN} />);
    expect(await screen.findByRole("heading", { name: "Attachments is on the Pro plan" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add file/ })).not.toBeInTheDocument();
  });

  it("lists files as text with a download link through the API, and size in words", async () => {
    serve({ "GET /decision-requests/d1/attachments": page([FILE]) });
    renderInApp(<Attachments decision={OPEN} />);
    const link = await screen.findByRole("link", { name: "<b>photo</b>.png" });
    expect(link).toHaveAttribute("href", "/api/v1/decision-requests/d1/attachments/f1/download");
    expect(document.querySelector("b")).toBeNull();
    expect(screen.getByText(/2 KB/)).toBeInTheDocument();
  });

  it("an empty list offers the first upload", async () => {
    serve({ "GET /decision-requests/d1/attachments": page([]) });
    renderInApp(<Attachments decision={OPEN} />);
    expect(await screen.findByText(/No files attached/)).toBeInTheDocument();
    expect(screen.getByLabelText("Add a file")).toBeInTheDocument();
  });

  it("uploads the raw file with its type and name, then re-reads the list", async () => {
    const user = userEvent.setup();
    const s = serve({ "GET /decision-requests/d1/attachments": page([]), "POST /decision-requests/d1/attachments": () => ({ ...FILE, filename: "a b.txt" }) });
    renderInApp(<Attachments decision={OPEN} />);
    const file = new File(["hello"], "a b.txt", { type: "text/plain" });
    await user.upload(await screen.findByLabelText("Add a file"), file);
    await waitFor(() => expect(s.count("POST /decision-requests/d1/attachments")).toBe(1));
    const [, path, init] = s.calls.find(([m]) => m === "POST")!;
    expect(path).toBe("/decision-requests/d1/attachments?filename=a%20b.txt");
    expect(init.body).toBe(file);
    await waitFor(() => expect(s.count("GET /decision-requests/d1/attachments")).toBe(2));
  });

  // catches: raw server text shown for a refused file
  it.each([
    [415, "unsupported_media_type", /That file type is not allowed\. Allowed: PDF, PNG, JPEG, GIF, WebP, TXT, CSV, JSON, DOCX and XLSX/],
    [413, "payload_too_large", /larger than 10 MB/],
  ])("a %s upload error names the rule, not the server text", async (status, code, rule) => {
    const user = userEvent.setup();
    serve({ "GET /decision-requests/d1/attachments": page([]), "POST /decision-requests/d1/attachments": failure(status, code, "SERVER-INTERNAL-TEXT") });
    renderInApp(<Attachments decision={OPEN} />);
    await user.upload(await screen.findByLabelText("Add a file"), new File(["x"], "a.exe", { type: "application/x-msdownload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(rule);
    expect(document.body.textContent).not.toContain("SERVER-INTERNAL-TEXT");
  });

  it("a file over 10 MB is refused before any request", async () => {
    const user = userEvent.setup();
    const s = serve({ "GET /decision-requests/d1/attachments": page([]) });
    renderInApp(<Attachments decision={OPEN} />);
    const big = new File(["x"], "big.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });
    await user.upload(await screen.findByLabelText("Add a file"), big);
    expect(await screen.findByRole("alert")).toHaveTextContent(/larger than 10 MB/);
    expect(s.count("POST /decision-requests/d1/attachments")).toBe(0);
  });
});

const row = (o: Record<string, unknown>) => ({ id: `e${Math.random()}`, entity_type: "decision_request", entity_id: "d1", action: "created", actor_type: "agent", actor_id: "a1", data: {}, created_at: iso(-30), ...o });

describe("Audit", () => {
  // catches: the audit tab bypassing the blind placeholders
  it("shows no verdict, confidence or rationale for a vote before the round is revealed", async () => {
    serve({
      "GET /agents": AGENTS,
      "GET /audit-logs": page([
        row({ entity_type: "vote", action: "cast", data: { decision_request_id: "d1", verdict: "reject", confidence: 0.31, rationale: "SECRET", round: 1 } }),
        row({ entity_type: "approval_step", action: "created", actor_type: "system", actor_id: null, data: { level: 1, required_role: "manager" } }),
      ]),
    });
    renderInApp(<Audit decision={decision({ status: "pending_votes" })} />);
    expect(await screen.findByText(/Vote cast/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/reject|0\.31|SECRET/i);
  });

  it("after the reveal a vote row names the verdict", async () => {
    serve({ "GET /agents": AGENTS, "GET /audit-logs": page([row({ entity_type: "vote", action: "cast", data: { verdict: "reject", round: 1 } })]) });
    renderInApp(<Audit decision={OPEN} />);
    expect(await screen.findByText(/Vote cast: reject/)).toBeInTheDocument();
  });

  it("asks for this request's trail, oldest first, with who acted and when", async () => {
    const s = serve({
      "GET /agents": AGENTS,
      "GET /audit-logs": page([
        row({ action: "created", data: { status: "pending_votes" } }),
        row({ entity_type: "approval_step", action: "decided", actor_type: "user", actor_id: "u1234567-aaaa", data: { level: 1, decision: "approve" } }),
      ], { total: 2 }),
    });
    renderInApp(<Audit decision={OPEN} />);
    const items = await screen.findAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Request created");
    expect(items[0]).toHaveTextContent("billing-agent");
    expect(items[1]).toHaveTextContent("Level 1 step approve");
    expect(items[1]).toHaveTextContent("Person u1234567");
    expect(s.calls.find(([, p]) => p.startsWith("/audit-logs"))![1]).toContain("decision_request_id=d1");
  });

  it("a failed read shows Retry and an empty trail says so", async () => {
    serve({ "GET /agents": AGENTS, "GET /audit-logs": failure(500, "boom") });
    const { unmount } = renderInApp(<Audit decision={OPEN} />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    unmount();
    serve({ "GET /agents": AGENTS, "GET /audit-logs": page([]) });
    renderInApp(<Audit decision={OPEN} />);
    expect(await screen.findByText(/Nothing has happened to this request yet/)).toBeInTheDocument();
  });
});

describe("Detail tabs wiring", () => {
  it("offers all seven tabs in order and shows the chosen one", async () => {
    serve({
      "GET /agents": AGENTS,
      "GET /me/pending-approvals": page([]),
      "GET /decision-requests/d1": OPEN,
      "GET /decision-requests/d1/votes": votesPage([vote("approve")]),
      "GET /decision-requests/d1/steps": stepsPage([step(1)]),
      "GET /decision-requests/d1/messages": page([msg({ body: "from the thread" })]),
    });
    renderInApp(<DecisionDetail id="d1" tab="discussion" onTab={vi.fn()} />);
    const tabs = (await screen.findAllByRole("tab")).map((t) => t.textContent);
    expect(tabs).toEqual(["Overview", "Votes", "Timeline", "Discussion", "Attachments", "Audit", "Receipt"]);
    const panel = screen.getByRole("tabpanel");
    expect(await within(panel).findByText("from the thread")).toBeInTheDocument();
  });
});
