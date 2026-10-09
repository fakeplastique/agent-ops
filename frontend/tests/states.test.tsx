import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { EmptyState, ReadView, type Read } from "@/components/states/States";
import { ApiError } from "@/lib/api/client";

type Rows = { items: string[] };
const EMPTY = "Nothing is waiting on you.";

function view(read: Partial<Read<Rows>>, extra: Partial<Parameters<typeof ReadView<Rows>>[0]> = {}) {
  const full: Read<Rows> = { status: "success", refetch: vi.fn(), ...read };
  const utils = render(
    <ReadView
      read={full}
      isEmpty={(d) => d.items.length === 0}
      empty={<EmptyState message={EMPTY} action={<a href="/decisions">Open decisions</a>} />}
      {...extra}
    >
      {(d) => (
        <ul>
          {d.items.map((i) => (
            <li key={i}>
              {i} <button type="button">Approve</button>
            </li>
          ))}
        </ul>
      )}
    </ReadView>,
  );
  return { ...utils, read: full };
}

const err = (status: number, code = `http_${status}`, extra: { details?: object; requestId?: string; retryAfter?: number } = {}) =>
  new ApiError(status, code, "server text", (extra.details ?? {}) as Record<string, unknown>, extra.requestId ?? null, extra.retryAfter ?? null);

describe("ReadView (I22: a failed or unfinished read is never shown as empty, and offers no writes)", () => {
  it("5xx renders the error with the request id and Retry, never the empty message", async () => {
    const { read } = view({ status: "error", error: err(500, "internal_error", { requestId: "req-7f3a" }) });
    expect(screen.getByText("req-7f3a")).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(read.refetch).toHaveBeenCalledOnce();
  });

  it("a network error renders Retry and the connection hint, never the empty message", () => {
    view({ status: "error", error: new ApiError(0, "network", "No response from the server") });
    expect(screen.getByText(/No response from the server/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });

  // A refetch that fails keeps the old rows in the query cache; showing them as current would hide the failure.
  it("a failed refetch with stale data shows the error, not the stale rows", () => {
    view({ status: "error", data: { items: ["refund #12"] }, error: err(503) });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/refund #12/)).not.toBeInTheDocument();
  });

  it("an unexpected throw (not an ApiError) is still an error with Retry", () => {
    view({ status: "error", error: new SyntaxError("Unexpected token <") });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });

  it("429 tells how long to wait", () => {
    view({ status: "error", error: err(429, "rate_limited", { retryAfter: 17 }) });
    expect(screen.getByText(/17 seconds/)).toBeInTheDocument();
  });

  it("loading has aria-busy and no write controls", () => {
    const { container } = view({ status: "pending" });
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });

  it("401 says the session ended (the client is redirecting), not empty", () => {
    view({ status: "error", error: err(401, "unauthenticated") });
    expect(screen.getByText(/session ended/i)).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("403 names the role needed", () => {
    view({ status: "error", error: err(403, "forbidden") }, { role: "org admin" });
    expect(screen.getByText("You do not have access to this")).toBeInTheDocument();
    expect(screen.getByText(/org admin role/)).toBeInTheDocument();
  });

  // Different text for "missing" and "someone else's" would tell a caller the id exists in another tenant.
  it("404 shows the same text for a missing id and a cross-tenant id", () => {
    const a = view({ status: "error", error: new ApiError(404, "not_found", "decision request not found") });
    const missing = a.container.textContent;
    a.unmount();
    const b = view({ status: "error", error: new ApiError(404, "not_found", "no such row in this tenant", { id: "x" }) });
    expect(b.container.textContent).toBe(missing);
    expect(screen.getByText("Not found")).toBeInTheDocument();
  });

  it("402 shows what the feature does, the plan needed and the upgrade path", () => {
    view(
      { status: "error", error: err(402, "plan_feature_required", { details: { flag: "analytics", required_plan: "pro" } }) },
      { gate: { feature: "Analytics", description: "See how long decisions take and who votes how." } },
    );
    expect(screen.getByRole("heading", { name: "Analytics is on the Pro plan" })).toBeInTheDocument();
    expect(screen.getByText("See how long decisions take and who votes how.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Plan and usage" })).toHaveAttribute("href", "/plan");
    expect(screen.queryByRole("button", { name: /Switch/ })).not.toBeInTheDocument();
  });

  it("402 in demo mode offers the plan switch", async () => {
    const onSwitchPlan = vi.fn();
    view(
      { status: "error", error: err(402, "plan_feature_required", { details: { required_plan: "pro" } }) },
      { gate: { feature: "Analytics", description: "d", onSwitchPlan } },
    );
    await userEvent.click(screen.getByRole("button", { name: "Switch to Pro" }));
    expect(onSwitchPlan).toHaveBeenCalledWith("pro");
  });

  // Retry unmounts once the read succeeds; keyboard focus must not fall back to <body>.
  it("after a successful Retry, focus lands on the loaded region", async () => {
    const refetch = vi.fn();
    const failed: Read<Rows> = { status: "error", error: err(500), refetch };
    const ui = (read: Read<Rows>) => <ReadView read={read}>{(d) => <p>{d.items.join(",")}</p>}</ReadView>;
    const { rerender } = render(ui(failed));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    rerender(ui({ status: "success", data: { items: ["refund #12"] }, refetch }));
    expect(screen.getByText("refund #12").closest("[tabindex='-1']")).toHaveFocus();
  });

  it("success with no rows shows the empty sentence and its one action", () => {
    view({ status: "success", data: { items: [] } });
    expect(screen.getByText(EMPTY)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open decisions" })).toBeInTheDocument();
  });

  it("success with rows renders them", () => {
    view({ status: "success", data: { items: ["refund #12"] } });
    expect(screen.getByText(/refund #12/)).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });
});
