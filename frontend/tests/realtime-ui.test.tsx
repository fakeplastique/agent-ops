import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectionBanner } from "@/components/ConnectionBanner";
import type { ClientOptions, RealtimeEvent } from "@/lib/realtime/client";
import { RealtimeProvider, useUpdatedNotice } from "@/lib/realtime/react";

// A client whose callbacks the test drives by hand.
let opts: ClientOptions;
const make = (o: ClientOptions) => {
  opts = o;
  return { start: vi.fn(), stop: vi.fn(), cursor: () => 0, connection: () => ({ status: "connecting" as const, transport: null }) };
};
const ev = (seq: number, data: Record<string, unknown> = {}): RealtimeEvent => ({ id: `e${seq}`, seq, type: "step.decided", data, created_at: "" });

afterEach(() => vi.restoreAllMocks());

describe("ConnectionBanner", () => {
  it("offline shows 'Live updates paused, reconnecting' and clears when live again", () => {
    render(
      <RealtimeProvider make={make as never}>
        <ConnectionBanner />
      </RealtimeProvider>,
    );
    expect(screen.queryByText(/Live updates paused/)).not.toBeInTheDocument(); // connecting is not a failure
    act(() => opts.onState?.({ status: "paused", transport: "long-poll" }));
    expect(screen.getByRole("status")).toHaveTextContent("Live updates paused, reconnecting");
    act(() => opts.onState?.({ status: "live", transport: "polling" }));
    expect(screen.queryByText(/Live updates paused/)).not.toBeInTheDocument();
  });

  // A banner or update that steals focus or scrolls would jump the list under the user's cursor.
  it("focus and scroll are unchanged by connection changes and updates", async () => {
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const into = vi.fn();
    Element.prototype.scrollIntoView = into;
    render(
      <RealtimeProvider make={make as never}>
        <ConnectionBanner />
        <button type="button">Row 3</button>
      </RealtimeProvider>,
    );
    const row = screen.getByRole("button", { name: "Row 3" });
    row.focus();
    act(() => opts.onState?.({ status: "paused", transport: null }));
    act(() => opts.onEvents([ev(1)], { replay: false }));
    act(() => opts.onState?.({ status: "live", transport: "websocket" }));
    expect(row).toHaveFocus();
    expect(scroll).not.toHaveBeenCalled();
    expect(into).not.toHaveBeenCalled();
  });
});

// A stand-in for the Decision detail: server data re-read on demand, a comment draft in local state.
function Detail({ refetch }: { refetch: () => unknown }) {
  const [draft, setDraft] = useState("");
  const { updated } = useUpdatedNotice((e) => e.data.decision_request_id === "d1", refetch);
  return (
    <>
      {updated && <p role="status">Updated</p>}
      <label>
        Comment
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} />
      </label>
    </>
  );
}

describe("Detail", () => {
  it("event for the open item shows 'Updated' and does not overwrite a comment in progress", async () => {
    const refetch = vi.fn();
    render(
      <RealtimeProvider make={make as never}>
        <Detail refetch={refetch} />
      </RealtimeProvider>,
    );
    const box = screen.getByLabelText("Comment");
    await userEvent.type(box, "Needs the invoice first");

    act(() => opts.onEvents([ev(1, { decision_request_id: "other" })], { replay: false }));
    act(() => opts.onEvents([ev(2, { decision_request_id: "d1" })], { replay: true })); // history, not news
    expect(screen.queryByText("Updated")).not.toBeInTheDocument();
    expect(refetch).not.toHaveBeenCalled();

    act(() => opts.onEvents([ev(3, { decision_request_id: "d1" })], { replay: false }));
    expect(screen.getByText("Updated")).toBeInTheDocument();
    expect(refetch).toHaveBeenCalledTimes(1); // re-read, not a silent overwrite
    expect(box).toHaveValue("Needs the invoice first");
    expect(box).toHaveFocus();
  });
});
