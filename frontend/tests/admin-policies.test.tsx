import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PoliciesView } from "@/app/(app)/policies/PoliciesView";
import { WebhooksView } from "@/app/(app)/webhooks/WebhooksView";

import { type Req, err, mockApi, page, session } from "./api-mock";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const policy = (over: object = {}) => ({
  id: "p1",
  team_id: null,
  name: "default",
  quorum_size: 3,
  require_distinct_agents: true,
  rounds: 1,
  confidence_threshold: 0.8,
  levels: [
    { role: "team_lead", timeout_minutes: 30 },
    { role: "org_admin", timeout_minutes: 60 },
  ],
  voting_timeout_minutes: 60,
  risk_floors: { "refund.issue": "high" },
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  ...over,
});
const planFree = {
  plan: "free",
  limits: { requests_per_month: 100, max_quorum: 3, max_rounds: 1, max_levels: 2 },
  usage: { requests: { used: 1, limit: 100, period: "2026-11" } },
  flags: [],
};
const planPro = { ...planFree, plan: "pro", limits: { ...planFree.limits, max_quorum: null, max_rounds: 2, max_levels: 5 }, flags: ["analytics"] };
const routes = (over: Record<string, unknown> = {}) => ({
  "GET /auth/session": session(),
  "GET /escalation-policies?limit=100": page([policy()]),
  "GET /organizations/current/plan": planFree,
  "GET /teams?limit=100": page([{ id: "t1", name: "Support", parent_team_id: null, created_at: "2026-10-01T09:00:00Z" }]),
  ...over,
});

async function edit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Edit default" }));
  return screen.getByRole("form", { name: "Edit policy default" });
}
const setNumber = async (user: ReturnType<typeof userEvent.setup>, el: HTMLElement, v: string) => {
  await user.clear(el);
  await user.type(el, v);
};

describe("Policies", () => {
  it("Free allows up to 3 reviewers: shown inline as you type, before any save", async () => {
    const user = userEvent.setup();
    const api = mockApi(routes());
    render(<PoliciesView />);
    const form = await edit(user);
    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "5");
    expect(within(form).getByText("Free allows up to 3 reviewers")).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    expect(api.calls.some((c) => c.key.startsWith("PATCH"))).toBe(false);
  });

  it("names the limit from the 402 details when the screen's plan data was stale", async () => {
    const user = userEvent.setup();
    mockApi(
      routes({
        "GET /organizations/current/plan": planPro,
        "GET /escalation-policies/p1": policy(),
        "PATCH /escalation-policies/p1": err(402, "policy_above_plan", "the free plan allows quorum_size up to 3", {
          limit: "quorum_size",
          max: 3,
          value: 5,
          plan: "free",
        }),
      }),
    );
    render(<PoliciesView />);
    const form = await edit(user);
    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "5");
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    expect(await within(form).findByText("Free allows up to 3 reviewers")).toBeInTheDocument();
    expect(within(form).getByLabelText("Reviewers needed")).toHaveValue(5); // the edit is kept
  });

  it("saves one PATCH with the edited values and shows the new summary", async () => {
    const user = userEvent.setup();
    let saved = policy();
    const api = mockApi(
      routes({
        "GET /escalation-policies?limit=100": () => ({ body: page([saved]) }),
        "GET /escalation-policies/p1": () => ({ body: saved }),
        "PATCH /escalation-policies/p1": ({ body }: Req) => {
          saved = policy({ ...body, updated_at: "2026-11-03T10:00:00Z" });
          return { body: saved };
        },
      }),
    );
    render(<PoliciesView />);
    const form = await edit(user);
    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "2");
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText(/2 reviewers/)).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    const patches = api.calls.filter((c) => c.key === "PATCH /escalation-policies/p1");
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toEqual({ quorum_size: 2, if_updated_at: "2026-10-01T09:00:00Z" });
  });

  it("leaving with unsaved changes asks first: Cancel, a nav link and closing the tab", async () => {
    const user = userEvent.setup();
    mockApi(routes());
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <>
        <a href="/agents">Agents</a>
        <PoliciesView />
      </>,
    );
    let form = await edit(user);
    // Clean: nothing to ask.
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "2");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);

    await user.click(screen.getByRole("link", { name: "Agents" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("form", { name: "Edit policy default" })).toBeInTheDocument(); // declined: still editing

    confirm.mockReturnValue(true);
    form = screen.getByRole("form", { name: "Edit policy default" });
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("a stale save is answered 409: the conflict is shown, Load takes the current version", async () => {
    const user = userEvent.setup();
    const api = mockApi(
      routes({
        "GET /escalation-policies/p1": policy({ quorum_size: 1, updated_at: "2026-11-03T09:59:00Z" }),
        "PATCH /escalation-policies/p1": err(409, "stale_version", "stale"),
      }),
    );
    render(<PoliciesView />);
    const form = await edit(user);
    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "2");
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("This policy changed since you opened it")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.key.startsWith("PATCH"))[0].body.if_updated_at).toBe("2026-10-01T09:00:00Z");
    await user.click(screen.getByRole("button", { name: "Load the current version" }));
    expect(within(screen.getByRole("form")).getByLabelText("Reviewers needed")).toHaveValue(1);
  });

  it("sends only the changed fields with the version it started from, and after Keep editing the newer version", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const api = mockApi(
      routes({
        "GET /escalation-policies/p1": policy({ rounds: 1, updated_at: "2026-11-03T09:59:00Z" }),
        "PATCH /escalation-policies/p1": () => (++attempt === 1 ? err(409, "stale_version", "stale") : { body: policy() }),
      }),
    );
    render(<PoliciesView />);
    const form = await edit(user);
    await setNumber(user, within(form).getByLabelText("Reviewers needed"), "2");
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    await user.click(await screen.findByRole("button", { name: "Keep editing" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(api.count("PATCH /escalation-policies/p1")).toBe(2));
    const [first, second] = api.calls.filter((c) => c.key.startsWith("PATCH")).map((c) => c.body);
    expect(first).toEqual({ quorum_size: 2, if_updated_at: "2026-10-01T09:00:00Z" }); // only the changed field
    expect(second).toEqual({ quorum_size: 2, if_updated_at: "2026-11-03T09:59:00Z" }); // B4: not the old version again
  });

  it("levels: add and remove keep the order; more levels than the plan allows is named inline", async () => {
    const user = userEvent.setup();
    mockApi(routes());
    render(<PoliciesView />);
    const form = await edit(user);
    await user.click(within(form).getByRole("button", { name: "Add level" }));
    expect(within(form).getByText("Free allows up to 2 approval levels")).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Remove level 3" }));
    expect(within(form).queryByText("Free allows up to 2 approval levels")).not.toBeInTheDocument();
  });

  it("a failed read is an error with Retry; a member sees policies read-only", async () => {
    mockApi(routes({ "GET /escalation-policies?limit=100": err(500, "internal", "boom") }));
    const { unmount } = render(<PoliciesView />);
    expect(await screen.findByRole("heading", { name: "Something went wrong on our side" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    unmount();

    mockApi(routes({ "GET /auth/session": session("member") }));
    render(<PoliciesView />);
    expect(await screen.findByText(/3 reviewers/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit|New policy/ })).not.toBeInTheDocument();
  });

  it("an organization with no policy gets one sentence and the create action; Free offers no team scope", async () => {
    const user = userEvent.setup();
    mockApi(routes({ "GET /escalation-policies?limit=100": page([]) }));
    render(<PoliciesView />);
    expect(await screen.findByText(/No policy yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New policy" }));
    const scope = screen.getByLabelText("Applies to");
    expect(within(scope).getAllByRole("option").map((o) => o.textContent)).toEqual(["Organization default"]);
    expect(screen.getByText(/Per-team policies need the Enterprise plan/)).toBeInTheDocument();
  });
});

const hook = (over: object = {}) => ({
  id: "w1",
  url: "https://hooks.example.com/aop",
  events: ["decision.created", "step.decided"],
  active: true,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  ...over,
});
const delivery = (over: object = {}) => ({
  id: "d1",
  event_id: "e1",
  event_type: "step.decided",
  status: "delivered",
  attempt: 1,
  next_attempt_at: null,
  last_status_code: 200,
  last_error: null,
  created_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:01Z",
  ...over,
});
const hookRoutes = (hooks: object[], over: Record<string, unknown> = {}) => ({
  "GET /auth/session": session(),
  "GET /webhook-subscriptions?limit=100": page(hooks),
  ...over,
});

describe("Webhooks", () => {
  it("the secret is shown once on create and is gone after closing", async () => {
    const user = userEvent.setup();
    let made = false;
    mockApi(
      hookRoutes([], {
        "GET /webhook-subscriptions?limit=100": () => ({ body: page(made ? [hook()] : []) }),
        "POST /webhook-subscriptions": ({ body }: Req) => {
          made = true;
          expect(body).toEqual({ url: "https://hooks.example.com/aop", events: ["step.decided"] });
          return { status: 201, body: { ...hook(), secret: "whsec_once-only" } };
        },
      }),
    );
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "New webhook" }));
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/aop");
    await user.click(screen.getByRole("checkbox", { name: "step.decided" }));
    await user.click(screen.getByRole("button", { name: "Create webhook" }));
    const dialog = await screen.findByRole("dialog", { name: /Signing secret/ });
    expect(within(dialog).getByLabelText("Signing secret")).toHaveValue("whsec_once-only");
    await user.click(within(dialog).getByRole("button", { name: "I have saved it" }));
    expect(document.body.innerHTML).not.toContain("whsec_once-only");
    expect(await screen.findByText("https://hooks.example.com/aop")).toBeInTheDocument();
  });

  it("a blocked URL names the rule that refused it and keeps the form", async () => {
    const user = userEvent.setup();
    mockApi(
      hookRoutes([], {
        "POST /webhook-subscriptions": err(422, "webhook_url_refused", "webhook URL refused: private address", { rule: "private address" }),
      }),
    );
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "New webhook" }));
    await user.type(screen.getByLabelText("URL"), "https://10.0.0.1/x");
    await user.click(screen.getByRole("checkbox", { name: "step.decided" }));
    await user.click(screen.getByRole("button", { name: "Create webhook" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This address is refused: private address");
    expect(screen.getByLabelText("URL")).toHaveValue("https://10.0.0.1/x");
  });

  it("needs at least one event before it sends anything", async () => {
    const user = userEvent.setup();
    const api = mockApi(hookRoutes([]));
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "New webhook" }));
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/aop");
    await user.click(screen.getByRole("button", { name: "Create webhook" }));
    expect(screen.getByText("Pick at least one event")).toBeInTheDocument();
    expect(api.calls.some((c) => c.key.startsWith("POST"))).toBe(false);
  });

  it("rotate shows the replacement secret once", async () => {
    const user = userEvent.setup();
    mockApi(hookRoutes([hook()], { "POST /webhook-subscriptions/w1/rotate-secret": { body: { id: "w1", secret: "whsec_new" } } }));
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Rotate secret" }));
    await user.click(screen.getByRole("button", { name: "Rotate" }));
    expect(await screen.findByLabelText("Signing secret")).toHaveValue("whsec_new");
    await user.click(screen.getByRole("button", { name: "I have saved it" }));
    expect(document.body.innerHTML).not.toContain("whsec_new");
  });

  it("pause and resume send one PATCH each", async () => {
    const user = userEvent.setup();
    let active = true;
    const api = mockApi(
      hookRoutes([], {
        "GET /webhook-subscriptions?limit=100": () => ({ body: page([hook({ active })]) }),
        "PATCH /webhook-subscriptions/w1": ({ body }: Req) => {
          active = body.active;
          return { body: hook({ active }) };
        },
      }),
    );
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(api.count("PATCH /webhook-subscriptions/w1")).toBe(1);
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("a failed pause does not leak its error into the New webhook form", async () => {
    const user = userEvent.setup();
    mockApi(hookRoutes([hook()], { "PATCH /webhook-subscriptions/w1": err(500, "internal", "boom") }));
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New webhook" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("members are forbidden here and no webhook read is made", async () => {
    const api = mockApi(hookRoutes([], { "GET /auth/session": session("member") }));
    render(<WebhooksView />);
    expect(await screen.findByRole("heading", { name: "You do not have access to this" })).toBeInTheDocument();
    expect(api.count("GET /webhook-subscriptions?limit=100")).toBe(0);
  });

  it("empty and failed lists are different states", async () => {
    mockApi(hookRoutes([]));
    const { unmount } = render(<WebhooksView />);
    expect(await screen.findByText(/No webhooks yet/)).toBeInTheDocument();
    unmount();
    mockApi(hookRoutes([], { "GET /webhook-subscriptions?limit=100": err(500, "internal", "boom") }));
    render(<WebhooksView />);
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/No webhooks yet/)).not.toBeInTheDocument();
  });
});

describe("Deliveries", () => {
  const dead = delivery({ id: "d2", status: "dead", attempt: 5, last_status_code: null, last_error: "timeout", event_type: "decision.created" });
  const deliveries = (rows: object[], over: Record<string, unknown> = {}) =>
    hookRoutes([hook()], { "GET /webhook-subscriptions/w1/deliveries?limit=100": page(rows), ...over });

  it("failed rows show a dash and a row-level retry; redeliver sends one request", async () => {
    const user = userEvent.setup();
    const api = mockApi(
      deliveries([delivery(), dead], {
        "POST /webhook-subscriptions/w1/deliveries/d2/redeliver": { status: 202, body: { ...dead, status: "pending", attempt: 0 } },
      }),
    );
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Deliveries" }));
    const row = (await screen.findByText("decision.created", { selector: "p" })).closest("li")!;
    expect(within(row).getByText("Failed")).toBeInTheDocument();
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).getByText(/timeout/)).toBeInTheDocument();
    const ok = screen.getByText("step.decided", { selector: "p" }).closest("li")!;
    expect(within(ok).queryByRole("button", { name: "Redeliver" })).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Redeliver" }));
    await waitFor(() => expect(api.count("POST /webhook-subscriptions/w1/deliveries/d2/redeliver")).toBe(1));
    expect(await within(screen.getByText("decision.created", { selector: "p" }).closest("li")!).findByText("Queued")).toBeInTheDocument();
  });

  it("a paused subscription refuses redelivery and says to resume first", async () => {
    const user = userEvent.setup();
    mockApi(
      deliveries([dead], {
        "POST /webhook-subscriptions/w1/deliveries/d2/redeliver": err(409, "subscription_paused", "paused"),
      }),
    );
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Deliveries" }));
    await user.click(await screen.findByRole("button", { name: "Redeliver" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This webhook is paused. Resume it, then redeliver.");
  });

  it("a failed deliveries read shows the error with Retry, not an empty list", async () => {
    const user = userEvent.setup();
    mockApi(deliveries([], { "GET /webhook-subscriptions/w1/deliveries?limit=100": err(500, "internal", "boom") }));
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Deliveries" }));
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/No deliveries yet/)).not.toBeInTheDocument();
  });

  it("no deliveries yet is its own sentence", async () => {
    const user = userEvent.setup();
    mockApi(deliveries([]));
    render(<WebhooksView />);
    await user.click(await screen.findByRole("button", { name: "Deliveries" }));
    expect(await screen.findByText(/No deliveries yet/)).toBeInTheDocument();
  });
});
