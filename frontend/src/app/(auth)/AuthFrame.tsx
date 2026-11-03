"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { Brand } from "@/components/shell/Shell";
import { ApiError } from "@/lib/api/client";
import { useIdentitySync } from "@/lib/session";

/** Screens before the app (sign in, onboarding, invitations): the tinted frame with one narrow sheet, no nav. */
export function AuthFrame({ title, lead, children }: { title: string; lead?: ReactNode; children: ReactNode }) {
  useIdentitySync();
  return (
    <main className="grid min-h-dvh place-items-center bg-ground p-inset">
      <div className="flex w-full max-w-(--measure) flex-col gap-6 rounded-sheet border border-border bg-surface p-6">
        <Brand />
        <div className="flex flex-col gap-2">
          <h1 className="m-0">{title}</h1>
          {lead && <p className="m-0 text-muted">{lead}</p>}
        </div>
        {children}
      </div>
    </main>
  );
}

/** A problem the user can act on: what happened, then what to do. Announced once when it appears. */
export function Notice({ title, children }: { title: string; children?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  // The control that caused it may have just unmounted; give focus a place to land instead of the page body.
  useEffect(() => {
    if (!document.activeElement || document.activeElement === document.body) ref.current?.focus();
  }, []);
  return (
    <div ref={ref} tabIndex={-1} role="alert" className="flex flex-col gap-1 rounded-card bg-raised px-4 py-3 wrap-anywhere">
      <p className="m-0 font-medium">{title}</p>
      {children && <div className="m-0 text-muted">{children}</div>}
    </div>
  );
}

/** Recovery line for a write that failed: only an unanswered call (status 0: network, timeout) is about the connection. */
export function retryNote(e: unknown): string {
  return e instanceof ApiError && e.status === 0
    ? "Nothing changed. Check your connection, then try again."
    : "Nothing changed. The server could not finish this. Try again in a moment.";
}

export const primaryButton =
  "inline-flex h-control items-center justify-center rounded-control bg-text px-4 font-medium text-surface no-underline transition-opacity hover:opacity-85 max-lg:h-touch coarse:h-touch aria-disabled:cursor-default aria-disabled:opacity-60";

export const secondaryButton =
  "inline-flex h-control items-center justify-center rounded-control bg-active px-4 font-medium text-text no-underline transition-colors hover:bg-control max-lg:h-touch coarse:h-touch aria-disabled:cursor-default aria-disabled:opacity-60";
