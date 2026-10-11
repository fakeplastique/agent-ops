import { act, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import { DecisionRail, type RailProps } from "@/components/DecisionRail/DecisionRail";
import { clockTime, timeLeft, windowUsed } from "@/lib/time";

const MIN = 60_000;
const START = Date.parse("2026-11-03T14:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

const base: RailProps = {
  quorum: { need: 3, approved: 3, revealedAt: "14:02" },
  levels: [
    { level: 1, role: "Reviewer", state: "done", note: "Ana, 14:10" },
    { level: 2, role: "Team lead", state: "current", note: "You" },
    { level: 3, role: "Org admin", state: "open" },
  ],
  outcome: "pending",
  risk: "medium",
  clock: { startedAt: iso(START), dueAt: iso(START + 60 * MIN), now: START + 11 * MIN },
};
const rail = (over: Partial<RailProps> = {}) => <DecisionRail {...base} {...over} />;

describe("time helpers", () => {
  it("under 15 minutes is a warning with text, past due says overdue, never negative minutes", () => {
    const due = START + 60 * MIN;
    expect(timeLeft(due, due - 49 * MIN)).toMatchObject({ label: "49 min left", tone: "normal" });
    expect(timeLeft(due, due - 14 * MIN)).toMatchObject({ label: "14 min left", tone: "warning", note: "Due soon" });
    const late = timeLeft(due, due + 3 * MIN)!;
    expect(late.tone).toBe("overdue");
    expect(late.label).toBe("Overdue by 3 min");
    expect(late.label).not.toMatch(/-/);
    expect(timeLeft(due, due)!.label).toBe("Overdue");
  });

  it("formats hours and days, rounds down so it never promises more time", () => {
    const due = START;
    expect(timeLeft(due, due - (65 * MIN + 59_000))!.label).toBe("1 h 05 min left");
    expect(timeLeft(due, due - 30_000)!.label).toBe("Under 1 min left");
    expect(timeLeft(due, due - 26 * 60 * MIN)!.label).toBe("1 d 2 h left");
  });

  it("returns null for an unparseable due time", () => {
    expect(timeLeft("soon", START)).toBeNull();
    expect(windowUsed(iso(START), "soon", START)).toBeNull();
    expect(windowUsed(iso(START), iso(START), START)).toBeNull();
  });

  it("11 of 60 minutes is 18 percent and clamps outside the window", () => {
    const s = iso(START);
    const d = iso(START + 60 * MIN);
    expect(windowUsed(s, d, START + 11 * MIN)).toEqual({ usedMin: 11, windowMin: 60, percent: 18 });
    expect(windowUsed(s, d, START - 5 * MIN)!.percent).toBe(0);
    expect(windowUsed(s, d, START + 90 * MIN)).toEqual({ usedMin: 60, windowMin: 60, percent: 100 });
  });

  it("clockTime renders HH:MM", () => {
    expect(clockTime(iso(START))).toMatch(/^\d{2}:\d{2}$/);
    expect(clockTime("nope")).toBe("");
  });
});

describe("DecisionRail", () => {
  it("11 of 60 min renders 18% with an aria-label stating it in words", () => {
    render(rail());
    const bar = screen.getByRole("img", { name: /Level 2 timeout: 11 of 60 minutes used, 18 percent/ });
    expect((bar.firstElementChild as HTMLElement).style.getPropertyValue("--fill")).toBe("18%");
    expect(screen.getByText("49 min left")).toBeInTheDocument();
  });

  it("time left and window are computed from one value (change due_at, both change)", () => {
    const { rerender } = render(rail());
    expect(screen.getByText("49 min left")).toBeInTheDocument();
    expect(screen.getByText(/11 of 60 min used/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`due ${clockTime(iso(START + 60 * MIN))}`))).toBeInTheDocument();

    const clock = { ...base.clock!, dueAt: iso(START + 120 * MIN) };
    rerender(rail({ clock }));
    expect(screen.getByText("1 h 49 min left")).toBeInTheDocument();
    expect(screen.getByText(/11 of 120 min used/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /11 of 120 minutes used, 9 percent/ })).toBeInTheDocument();
    expect(screen.queryByText("49 min left")).not.toBeInTheDocument();
  });

  it("under 15 minutes shows warning text, overdue says overdue", () => {
    const { rerender } = render(rail({ clock: { ...base.clock!, now: START + 50 * MIN } }));
    expect(screen.getByText("10 min left")).toHaveAttribute("data-tone", "warning");
    expect(screen.getByText("Due soon")).toBeInTheDocument();
    rerender(rail({ clock: { ...base.clock!, now: START + 63 * MIN } }));
    expect(screen.getByText("Overdue by 3 min")).toHaveAttribute("data-tone", "overdue");
    expect(screen.getByRole("img", { name: /60 of 60 minutes used, 100 percent/ })).toBeInTheDocument();
  });

  it("an unparseable due time says so instead of printing NaN", () => {
    render(rail({ clock: { ...base.clock!, dueAt: "soon" } }));
    expect(screen.getByText("Due time unavailable")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/NaN/);
  });

  it("ticks the time left with the clock when no fixed now is given", () => {
    vi.useFakeTimers({ now: START + 11 * MIN });
    try {
      render(rail({ clock: { startedAt: iso(START), dueAt: iso(START + 60 * MIN) } }));
      expect(screen.getByText("49 min left")).toBeInTheDocument();
      act(() => void vi.advanceTimersByTime(2 * MIN));
      expect(screen.getByText("47 min left")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejected ends the rail with 'No receipt is issued'", () => {
    render(
      rail({
        outcome: "rejected",
        clock: undefined,
        levels: [
          { level: 1, role: "Reviewer", state: "done", note: "Ana, 14:10" },
          { level: 2, role: "Team lead", state: "rejected", note: "You rejected at 14:33" },
          { level: 3, role: "Org admin", state: "open" },
        ],
      }),
    );
    const items = screen.getAllByRole("listitem");
    expect(within(items[items.length - 1]).getByText("No receipt is issued")).toBeInTheDocument();
    expect(screen.queryByText(/Signed when approved/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Level 3/)).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("approved shows the signed receipt with its hash", () => {
    render(rail({ outcome: "approved", clock: undefined, receipt: { hash: "7f3a91d2…c4e2", signedAt: "14:33" } }));
    const items = screen.getAllByRole("listitem");
    const last = items[items.length - 1];
    expect(last).toHaveTextContent("Receipt");
    expect(last).toHaveTextContent("7f3a91d2…c4e2");
    expect(last).toHaveAttribute("data-state", "signed");
  });

  it("pending shows an unsigned receipt as the open end", () => {
    render(rail());
    const items = screen.getAllByRole("listitem");
    expect(items[items.length - 1]).toHaveAttribute("data-state", "unsigned");
    expect(items[items.length - 1]).toHaveTextContent("Signed when approved");
  });

  it("blind round shows 'vote hidden until round closes', no verdict marks", () => {
    render(rail({ quorum: { need: 3, approved: 2, voted: 2, blind: true } }));
    const quorum = screen.getAllByRole("listitem")[0];
    expect(quorum).toHaveTextContent("Vote hidden until round closes");
    expect(quorum).toHaveTextContent("2 of 3 voted");
    expect(quorum).not.toHaveTextContent(/approved/i);
    expect(quorum).toHaveAttribute("data-state", "open");
    expect(quorum.querySelector("svg")).toBeNull();
  });

  it("quorum short of its target is the current step, not done", () => {
    render(
      rail({
        quorum: { need: 3, approved: 1, revealedAt: "14:02" },
        levels: [{ level: 1, role: "Reviewer", state: "open" }],
      }),
    );
    const quorum = screen.getAllByRole("listitem")[0];
    expect(quorum).toHaveTextContent("Quorum 1 of 3");
    expect(quorum).toHaveAttribute("data-state", "current");
  });

  it("pill and nodes carry text or glyph, not colour alone", () => {
    render(rail());
    expect(screen.getByText("Medium risk")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    for (const item of items) {
      // Every node names its state in text; the dot's glyph is decoration.
      expect(item.querySelector(".sr-only")?.textContent).toMatch(/Done|Current|Not started|Rejected|Signed|Not signed/);
    }
    for (const d of items.filter((i) => i.getAttribute("data-state") === "done")) {
      expect(d.querySelector("svg")).not.toBeNull();
    }
  });

  it("states the policy and the quorum in words for assistive tech", () => {
    render(rail());
    const region = screen.getByRole("region", { name: "Decision progress" });
    expect(within(region).getByText("Policy: 3 levels")).toBeInTheDocument();
    expect(within(region).getByText("Quorum 3 of 3")).toBeInTheDocument();
    expect(within(region).getByText(/Level 2/)).toBeInTheDocument();
  });
});
