import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Inbox } from "@/components/Inbox/Inbox";
import type { ClientOptions, RealtimeEvent } from "@/lib/realtime/client";
import { RealtimeProvider } from "@/lib/realtime/react";

import { decision, page, pending, renderInApp, serve, step, stepsPage, vote, votesPage } from "./helpers";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

let opts: ClientOptions;
const make = (o: ClientOptions) => {
  opts = o;
  return { start: vi.fn(), stop: vi.fn(), cursor: () => 0, connection: () => ({ status: "live" as const, transport: "long-poll" as const }) };
};
const ev = (type: string, data: Record<string, unknown> = {}): RealtimeEvent => ({ id: "e1", seq: 1, type, data, created_at: "" });

// catches: realtime built and unit-tested but never mounted, so a screen only changes on reload (found by the S48 journey)
describe("Inbox on the live feed", () => {
  it("a step event re-reads the list in place and shows the new row", async () => {
    let rows = [pending()];
    const routes = serve({
      "GET /agents": page([{ id: "a1", name: "billing-agent", status: "active" }]),
      "GET /me/pending-approvals": () => page(rows),
      "GET /decision-requests/d1": decision(),
      "GET /decision-requests/d1/votes": votesPage([vote("approve"), vote("approve"), vote("reject")]),
      "GET /decision-requests/d1/steps": stepsPage([step(1)]),
    });
    renderInApp(
      <RealtimeProvider make={make as never}>
        <Inbox />
      </RealtimeProvider>,
    );
    await screen.findByRole("button", { name: /Refund order #48213/ });
    const before = routes.count("GET /me/pending-approvals");

    rows = [pending(), pending({ id: "st9", decision_request_id: "d2", title: "Rotate staging API keys" })];
    act(() => opts.onEvents([ev("step.created", { decision_request_id: "d2" })], { replay: false }));
    expect(await screen.findByRole("button", { name: /Rotate staging API keys/ })).toBeInTheDocument();
    await waitFor(() => expect(routes.count("GET /me/pending-approvals")).toBeGreaterThan(before));
  });

  it("an unrelated event leaves the list alone", async () => {
    const routes = serve({
      "GET /agents": page([{ id: "a1", name: "billing-agent", status: "active" }]),
      "GET /me/pending-approvals": page([pending()]),
      "GET /decision-requests/d1": decision(),
      "GET /decision-requests/d1/votes": votesPage([vote("approve")]),
      "GET /decision-requests/d1/steps": stepsPage([step(1)]),
    });
    renderInApp(
      <RealtimeProvider make={make as never}>
        <Inbox />
      </RealtimeProvider>,
    );
    await screen.findByRole("button", { name: /Refund order #48213/ });
    const before = routes.count("GET /me/pending-approvals");
    act(() => opts.onEvents([ev("plan.changed")], { replay: false }));
    expect(routes.count("GET /me/pending-approvals")).toBe(before);
  });
});
