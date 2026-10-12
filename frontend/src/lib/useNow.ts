"use client";

import { useEffect, useState } from "react";

/** The clock in ms, re-read every `everyMs` (default 30 s) and when the tab becomes visible again. */
export function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const id = setInterval(tick, everyMs);
    const wake = () => document.visibilityState === "visible" && tick();
    document.addEventListener("visibilitychange", wake);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [everyMs]);
  return now;
}
