import { describe, expect, it } from "vitest";

import { findingLine, railProps, type Bundle } from "@/components/Decision/model";
import type { Decision, Step, Vote } from "@/lib/api/types";

const NOW = Date.parse("2026-11-03T14:32:00Z");
const iso = (min: number) => new Date(NOW + min * 60_000).toISOString();

const decision = (o: Partial<Decision> = {}): Decision => ({
  id: "d1", title: "Refund order 7", description: "", action_type: "payments.refund", payload: {}, risk_level: "high",
  effective_risk_level: "high", status: "pending_approval", version: 1, content_hash: "7f3a91d2aaaaaaaaaaaaaaaaaaaac4e2",
  requested_by_agent_id: "a1", requested_by_user_id: null, voting_deadline_at: iso(-20), escalation_reason: null,
  decided_at: null, created_at: iso(-30), updated_at: iso(-1), ...o,
});
const vote = (verdict: Vote["verdict"], o: Partial<Vote> = {}): Vote => ({
  session_id: `s-${verdict}-${Math.random()}`, round: 1, verdict, confidence: 0.9, rationale: "because", request_version: 1,
  created_at: iso(-25), revealed_at: iso(-20), ...o,
});
const step = (level: number, status: Step["status"], o: Partial<Step> = {}): Step => ({
  id: `st${level}`, request_version: 1, level, required_role: level === 1 ? "manager" : "org_admin", status,
  due_at: iso(49), decided_at: null, decided_by: null, stands_in_for_level: null, created_at: iso(-11), ...o,
});
const LEVELS = [
  { level: 1, role: "manager", timeout_minutes: 60 },
  { level: 2, role: "org_admin", timeout_minutes: 60 },
];
const bundle = (d: Decision, votes: Vote[], steps: Step[], cast = votes.length): Bundle => ({
  decision: d,
  votes: { items: votes, total: votes.length, skip: 0, limit: 100, nextLink: null, cast, quorum: 3 },
  steps: { items: steps, total: steps.length, skip: 0, limit: 100, nextLink: null, levels: LEVELS, quorum: 3 },
});

describe("railProps", () => {
  it("a pending step becomes the current level; time left and window come from that one step", () => {
    const b = bundle(decision(), [vote("approve"), vote("approve"), vote("reject")], [step(1, "pending")]);
    const p = railProps(b, NOW)!;
    expect(p.levels.map((l) => l.state)).toEqual(["current", "open"]);
    expect(p.clock).toEqual({ startedAt: iso(-11), dueAt: iso(49) });
    expect(p.quorum).toMatchObject({ need: 3, approved: 2, blind: false });
    expect(p.outcome).toBe("pending");
  });

  // catches: a verdict count reaching the rail while round 1 is still blind (I5)
  it("a blind round passes only the voted count, never approvals", () => {
    const b = bundle(decision({ status: "pending_votes" }), [], [], 2);
    const p = railProps(b, NOW)!;
    expect(p.quorum.blind).toBe(true);
    expect(p.quorum).toMatchObject({ voted: 2, reviewers: 3 });
    expect(findingLine(b, NOW)).toBe("2 of 3 reviewers have voted. Votes stay hidden until the round closes.");
    expect(findingLine(b, NOW)).not.toMatch(/approved|rejected/);
  });

  it("decided levels carry who and when; a rejection ends the rail", () => {
    const b = bundle(
      decision({ status: "rejected", decided_at: iso(-1) }),
      [vote("approve"), vote("approve"), vote("approve")],
      [step(1, "rejected", { decided_by: "Maya Okafor", decided_at: iso(-1) })],
    );
    const p = railProps(b, NOW)!;
    expect(p.levels[0]).toMatchObject({ state: "rejected" });
    expect(p.levels[0].note).toContain("Maya Okafor");
    expect(p.outcome).toBe("rejected");
  });

  it("an auto-approved request has no levels and a receipt", () => {
    const b = bundle(decision({ status: "approved", decided_at: iso(-5) }), [vote("approve"), vote("approve"), vote("approve")], []);
    const p = railProps(b, NOW)!;
    expect(p.levels).toEqual([]);
    expect(p.receipt?.hash).toBe("7f3a91d2…c4e2");
  });

  it("steps of an older version are not the current ladder", () => {
    const b = bundle(decision({ version: 2 }), [], [step(1, "superseded"), step(1, "pending", { id: "x", request_version: 2 })]);
    expect(railProps(b, NOW)!.levels.map((l) => l.state)).toEqual(["current", "open"]);
  });

  it("cancelled and expired requests have no rail to draw", () => {
    expect(railProps(bundle(decision({ status: "cancelled" }), [], []), NOW)).toBeNull();
    expect(railProps(bundle(decision({ status: "expired" }), [], []), NOW)).toBeNull();
  });
});

describe("findingLine", () => {
  it("names the time, where it goes next and the reviewer split", () => {
    const b = bundle(decision(), [vote("approve"), vote("approve"), vote("reject")], [step(1, "pending")]);
    expect(findingLine(b, NOW)).toBe("Level 1 goes to the org admin if it is not decided in 49 min. 1 of 3 reviewers rejected it.");
  });

  it("the last level says the request expires", () => {
    const b = bundle(decision(), [vote("approve"), vote("approve"), vote("approve")], [step(1, "approved"), step(2, "pending")]);
    expect(findingLine(b, NOW)).toBe("Level 2 expires if it is not decided in 49 min. All 3 reviewers approved.");
  });

  it("an overdue step never says negative minutes", () => {
    const b = bundle(decision(), [], [step(1, "pending", { due_at: iso(-5) })]);
    expect(findingLine(b, NOW)).toContain("if it is not decided now");
  });
});
