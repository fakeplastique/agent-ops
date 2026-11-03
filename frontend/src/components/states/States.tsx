"use client";

import Link from "next/link";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { ApiError } from "@/lib/api/client";

/** The shape of a read; a TanStack Query result fits it as is. isFetching drives Retry's busy state. */
export type Read<T> = {
  status: "pending" | "error" | "success";
  data?: T;
  error?: unknown;
  isFetching?: boolean;
  refetch: () => unknown;
};

type Gate = { feature: string; description: string; onSwitchPlan?: (plan: string) => void };

const primary =
  "inline-flex h-control items-center rounded-control bg-text px-4 font-medium text-surface transition-opacity hover:opacity-85 max-lg:h-touch coarse:h-touch";

function Panel({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex max-w-(--measure) flex-col items-start gap-2 py-6">
      <h2 id={id} className="m-0 text-section font-medium">
        {title}
      </h2>
      {children}
    </section>
  );
}

const Note = ({ children }: { children: ReactNode }) => <p className="m-0 text-muted">{children}</p>;

/** Skeleton rows in the list layout; no controls until the data is real. ReadView's live region says "Loading". */
export function Loading({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-busy="true">
      <ul aria-hidden="true" className="m-0 list-none p-0">
        {Array.from({ length: rows }, (_, i) => (
          <li key={i} className="flex min-h-row items-center gap-4 border-b border-rule">
            <span className={`h-3 rounded-pill bg-active ${i % 2 ? "w-1/3" : "w-2/5"}`} />
            <span className="ml-auto h-3 w-16 rounded-pill bg-raised" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function errorCopy(error: unknown): { title: string; note: string } {
  if (!(error instanceof ApiError)) return { title: "This did not load", note: "Something went wrong in the page. Retry." };
  if (error.code === "network") return { title: "No response from the server", note: "Check your connection, then retry." };
  if (error.code === "timeout") return { title: "The server took too long to answer", note: "Retry in a moment." };
  if (error.status === 429) {
    const wait = error.retryAfter ? `in ${error.retryAfter} seconds` : "in a moment";
    return { title: "Too many requests", note: `Retry ${wait}.` };
  }
  if (error.status >= 500) return { title: "Something went wrong on our side", note: "Retry. If it keeps failing, quote the request id." };
  return { title: "This did not load", note: "Retry, or reload the page." };
}

/** Inline read failure: what happened, the request id when the server gave one, and Retry. */
export function ErrorState({ error, onRetry, busy = false }: { error: unknown; onRetry: () => unknown; busy?: boolean }) {
  const { title, note } = errorCopy(error);
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <Panel title={title}>
      <Note>{note}</Note>
      {requestId && (
        <p className="m-0 text-small text-muted">
          Request id <code className="font-mono break-all text-text select-all">{requestId}</code>
        </p>
      )}
      {/* Stays mounted while busy so keyboard focus is not dropped. */}
      <button
        type="button"
        aria-disabled={busy}
        onClick={() => !busy && void onRetry()}
        className={`mt-2 ${primary} aria-disabled:cursor-default aria-disabled:opacity-60`}
      >
        {busy ? "Retrying…" : "Retry"}
      </button>
    </Panel>
  );
}

/** One sentence in the trade's words and one action. */
export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="flex max-w-(--measure) flex-col items-start gap-3 py-6">
      <p className="m-0">{message}</p>
      {action}
    </div>
  );
}

const FORBIDDEN = "You do not have access to this";
const NOT_FOUND = "Not found";
const SESSION_ENDED = "Your session ended. Taking you to sign in…";

export function Forbidden({ role }: { role?: string }) {
  return (
    <Panel title={FORBIDDEN}>
      <Note>{role ? `It needs the ${role} role. An org admin can change your role.` : "An org admin can change your role."}</Note>
    </Panel>
  );
}

/** Same words for a missing id and another tenant's id: the answer must not tell them apart. */
export function NotFound() {
  return (
    <Panel title={NOT_FOUND}>
      <Note>It may have been removed, or the link is wrong.</Note>
    </Panel>
  );
}

const planName = (plan?: string) => (plan ? plan.charAt(0).toUpperCase() + plan.slice(1) : "");
const planTitle = (feature: string, plan?: string) =>
  plan ? `${feature} is on the ${planName(plan)} plan` : `${feature} needs a higher plan`;

export function PlanGated({ feature, description, onSwitchPlan, plan }: Gate & { plan?: string }) {
  return (
    <Panel title={planTitle(feature, plan)}>
      <Note>{description}</Note>
      <p className="m-0">
        An org admin can change the plan on{" "}
        <Link href="/plan" className="text-text underline underline-offset-2">
          Plan and usage
        </Link>
        .
      </p>
      {onSwitchPlan && plan && (
        <button type="button" onClick={() => onSwitchPlan(plan)} className={`mt-2 ${primary}`}>
          Switch to {planName(plan)}
        </button>
      )}
    </Panel>
  );
}

type Props<T> = {
  read: Read<T>;
  children: (data: T) => ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  loading?: ReactNode;
  /** Role a 403 names, e.g. "org admin". */
  role?: string;
  /** What a 402 explains. */
  gate?: Omit<Gate, "feature"> & { feature?: string };
  /** Names the focus region, e.g. the screen title. */
  label?: string;
};

const requiredPlan = (e: ApiError) => (typeof e.details.required_plan === "string" ? e.details.required_plan : undefined);

// One line for the live region, built from the same strings the states render.
function announce<T>({ read, gate }: Props<T>): string {
  if (read.status === "pending") return "Loading";
  if (read.status === "success") return "Loaded";
  const e = read.error;
  if (e instanceof ApiError && e.status === 401) return SESSION_ENDED;
  if (e instanceof ApiError && e.status === 402) return planTitle(gate?.feature ?? "This feature", requiredPlan(e));
  if (e instanceof ApiError && e.status === 403) return FORBIDDEN;
  if (e instanceof ApiError && e.status === 404) return NOT_FOUND;
  return errorCopy(e).title;
}

function Outcome<T>({ read, children, isEmpty, empty, loading, role, gate, onRetry }: Props<T> & { onRetry: () => void }) {
  if (read.status === "pending") return loading ?? <Loading />;
  if (read.status === "error") {
    const e = read.error;
    if (e instanceof ApiError && e.status === 401) return <p className="m-0 py-6 text-muted">{SESSION_ENDED}</p>;
    if (e instanceof ApiError && e.status === 402)
      return (
        <PlanGated
          feature={gate?.feature ?? "This feature"}
          description={gate?.description ?? ""}
          onSwitchPlan={gate?.onSwitchPlan}
          plan={requiredPlan(e)}
        />
      );
    if (e instanceof ApiError && e.status === 403) return <Forbidden role={role} />;
    if (e instanceof ApiError && e.status === 404) return <NotFound />;
    return <ErrorState error={e} onRetry={onRetry} busy={read.isFetching} />;
  }
  const data = read.data as T;
  if (isEmpty?.(data) && empty) return empty;
  return children(data);
}

/** Renders children only for data that loaded; every other outcome gets its own state, never the empty one. */
export function ReadView<T>(props: Props<T>) {
  const region = useRef<HTMLDivElement>(null);
  const retried = useRef(false);
  const { status, isFetching } = props.read;

  // Once a Retry settles: success hands focus to the region (Retry has unmounted); any outcome clears the flag,
  // so a later background poll cannot move focus.
  useEffect(() => {
    if (!retried.current || status === "pending" || isFetching) return;
    retried.current = false;
    const lost = document.activeElement === document.body || !document.activeElement;
    if (status !== "error" && lost) region.current?.focus();
  }, [status, isFetching]);

  const retry = () => {
    retried.current = true;
    void props.read.refetch();
  };

  return (
    <>
      {/* Persistent and outside aria-busy so each change is announced (spec 8.2). Polite: no read failure needs to interrupt. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announce(props)}
      </p>
      <div ref={region} tabIndex={-1} role="group" aria-label={props.label ?? "Content"} className="outline-none">
        <Outcome {...props} onRetry={retry} />
      </div>
    </>
  );
}
