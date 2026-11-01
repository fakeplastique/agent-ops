"use client";

import type { Read } from "@/components/states/States";
import { useRead } from "@/lib/api/useRead";
import type { Decision, StepsPage, VotesPage } from "@/lib/api/types";

import type { Bundle } from "./model";

/** The request, its votes and its approval ladder as one Read: pending until all three arrive, an error if any fails. */
export function useDecisionBundle(id: string | null): Read<Bundle> {
  const base = id ? `/decision-requests/${encodeURIComponent(id)}` : null;
  const d = useRead<Decision>(base);
  const v = useRead<VotesPage>(base && `${base}/votes?limit=100`);
  const s = useRead<StepsPage>(base && `${base}/steps?limit=100`);
  const all = [d, v, s];
  const refetch = () => all.forEach((r) => void r.refetch());
  const isFetching = all.some((r) => r.isFetching);
  const failed = all.find((r) => r.status === "error");
  if (failed) return { status: "error", error: failed.error, isFetching, refetch };
  if (all.some((r) => r.status === "pending")) return { status: "pending", isFetching, refetch };
  return { status: "success", data: { decision: d.data!, votes: v.data!, steps: s.data! }, isFetching, refetch };
}
