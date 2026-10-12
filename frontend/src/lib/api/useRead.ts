"use client";

import { useCallback, useEffect, useState } from "react";

import type { Read } from "@/components/states/States";

import { api } from "./client";

type Settled<T> = { path: string; tick: number } & ({ status: "success"; data: T } | { status: "error"; error: unknown });

/**
 * GET a path as a Read for ReadView. A new path is pending again (the old data never shows under it); a refetch keeps
 * the data while in flight (isFetching) and a failed one shows the error, not the stale rows. A null path reads nothing.
 */
export function useRead<T>(path: string | null): Read<T> {
  const [tick, setTick] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);

  useEffect(() => {
    if (path === null) return;
    const ctl = new AbortController();
    // Only the promise sets state, so a stale answer (aborted, or for an older tick) can never land.
    api<T>(path, { signal: ctl.signal }).then(
      (data) => !ctl.signal.aborted && setSettled({ path, tick, status: "success", data }),
      (error: unknown) => !ctl.signal.aborted && setSettled({ path, tick, status: "error", error }),
    );
    return () => ctl.abort();
  }, [path, tick]);

  const refetch = useCallback(() => setTick((t) => t + 1), []);
  const mine = settled && settled.path === path ? settled : null;
  if (!mine) return { status: "pending", isFetching: path !== null, refetch };
  const isFetching = mine.tick !== tick;
  return mine.status === "success"
    ? { status: "success", data: mine.data, isFetching, refetch }
    : { status: "error", error: mine.error, isFetching, refetch };
}
