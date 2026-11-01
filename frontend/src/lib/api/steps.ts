import { api } from "./client";

export type DecideResult = { step_id: string; status: string; request_status: string; next_step_id: string | null };

/** A fresh Idempotency-Key for one decision attempt; the caller reuses it while retrying the same attempt. */
export const newKey = () => crypto.randomUUID();

/** Approve or reject one step. The key makes a retry after a lost answer a replay, never a second decision. */
export function decideStep(
  stepId: string,
  body: { request_version: number; decision: "approve" | "reject"; comment: string },
  key: string,
) {
  return api<DecideResult>(`/approval-steps/${encodeURIComponent(stepId)}/decide`, {
    method: "POST",
    body,
    headers: { "Idempotency-Key": key },
  });
}
