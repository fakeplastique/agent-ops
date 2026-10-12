"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Read } from "@/components/states/States";
import { api } from "@/lib/api/client";

/** The API's list envelope. */
export type Page<T> = { items: T[]; total: number; skip: number; limit: number; nextLink?: string | null };

/** Lists load their first 100 rows; a longer list says so rather than looking complete. */
export const LIST = "limit=100";

/**
 * GET as a Read for ReadView. A refetch keeps the old data until it settles, but a failed one shows the error, not the stale rows.
 * A null path reads nothing (stays pending), for reads that must not be made yet.
 */
export function useRead<T>(path: string | null): Read<T> {
  const [read, setRead] = useState<Omit<Read<T>, "refetch">>({ status: "pending", isFetching: path !== null });
  const latest = useRef(0);

  const load = useCallback(() => {
    if (path === null) return;
    const mine = ++latest.current;
    api<T>(path).then(
      (data) => mine === latest.current && setRead({ status: "success", data, isFetching: false }),
      (error: unknown) => mine === latest.current && setRead({ status: "error", error, isFetching: false }),
    );
  }, [path]);

  // Only the promise sets state, so the first read can start from an effect.
  useEffect(() => {
    load();
    // Drops an answer that lands after unmount or after the path changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => void latest.current++;
  }, [load]);

  const refetch = () => {
    setRead((r) => ({ ...r, isFetching: true }));
    load();
  };
  return { ...read, refetch };
}
