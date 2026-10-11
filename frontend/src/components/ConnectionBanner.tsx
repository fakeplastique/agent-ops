"use client";

import { useConnection } from "@/lib/realtime/react";

/** Shown only while live updates are down; the list keeps polling. The region stays mounted so the change is announced. */
export function ConnectionBanner() {
  const { status } = useConnection();
  return (
    <div role="status" aria-live="polite">
      {status === "paused" && (
        <p className="m-0 flex min-h-control items-center rounded-control bg-pill-warning-bg px-3 text-small font-medium text-pill-warning-ink">
          Live updates paused, reconnecting
        </p>
      )}
    </div>
  );
}
