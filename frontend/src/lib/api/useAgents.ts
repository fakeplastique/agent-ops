"use client";

import type { Page } from "./types";
import { useRead } from "./useRead";

export type AgentSummary = { id: string; name: string; status: string };

/** Agent names by id for rows that only carry the id; an unknown or unloaded id falls back to a short id. */
export function useAgentNames() {
  const read = useRead<Page<AgentSummary>>("/agents?limit=100");
  const names = new Map((read.data?.items ?? []).map((a) => [a.id, a.name]));
  const name = (id: string | null) => (id ? (names.get(id) ?? `agent ${id.slice(0, 8)}`) : "A person");
  return { read, agents: read.data?.items ?? [], name };
}
