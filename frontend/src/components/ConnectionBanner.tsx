"use client";

import { useConnection } from "@/lib/realtime/react";

const box = "m-0 flex min-h-control items-center rounded-control bg-pill-warning-bg px-3 text-small font-medium text-pill-warning-ink";

/** Shown only while live updates are down: paused (still retrying) or stopped (signed out, no access). The region stays mounted so it is announced. */
export function ConnectionBanner() {
  const { status, reason } = useConnection();
  return (
    <div role="status" aria-live="polite">
      {status === "paused" && <p className={box}>Live updates paused, reconnecting</p>}
      {status === "stopped" && (
        <p className={box}>
          {reason === "forbidden"
            ? "Live updates stopped: you no longer have access"
            : "Live updates stopped: you are signed out. Sign in again to resume"}
        </p>
      )}
    </div>
  );
}
