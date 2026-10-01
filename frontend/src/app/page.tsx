"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Checks = { postgres: boolean; redis: boolean };
type State =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready" | "unavailable"; checks: Checks };

const LABELS: Record<keyof Checks, string> = { postgres: "Postgres", redis: "Redis" };

// Ready needs 200 and ok:true; a 503 body names the failed dependency; anything else is an error.
async function readApi(): Promise<State> {
  try {
    const res = await fetch("/readyz", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (res.status !== 200 && res.status !== 503) return { kind: "error" };
    const body = await res.json();
    const checks: Checks = { postgres: body.checks.postgres, redis: body.checks.redis };
    return { kind: res.status === 200 && body.status === "ok" ? "ready" : "unavailable", checks };
  } catch {
    return { kind: "error" };
  }
}

export default function Page() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(true); // a read is in flight; the button stays mounted so focus stays
  const status = useRef<HTMLDivElement>(null);
  const retried = useRef(false);

  const refresh = useCallback(
    () =>
      readApi().then((next) => {
        setState(next);
        setBusy(false);
      }),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The Retry button unmounts once the read succeeds; hand focus to the status region.
  useEffect(() => {
    if (retried.current && !busy && document.activeElement === document.body) status.current?.focus();
  }, [state, busy]);

  const retry = () => {
    if (busy) return;
    retried.current = true;
    setBusy(true);
    void refresh();
  };

  return (
    <main className="frame">
      <div className="sheet">
        <h1>Agent Ops</h1>
        <div role="status" ref={status} tabIndex={-1}>
          {state.kind === "loading" && <p className="muted">Checking the API…</p>}
          {state.kind === "error" && (
            <>
              <h2>Cannot reach the API</h2>
              <p className="muted">The server did not answer. Check your connection and try again.</p>
            </>
          )}
          {(state.kind === "ready" || state.kind === "unavailable") && (
            <>
              <h2>{state.kind === "ready" ? "API ready" : "API unavailable"}</h2>
              <dl className="checks">
                {(Object.keys(LABELS) as (keyof Checks)[]).map((key) => (
                  <div key={key}>
                    <dt>{LABELS[key]}</dt>
                    <dd>
                      <span className="pill" data-ok={state.checks[key]}>
                        {state.checks[key] ? "Ready" : "Not responding"}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>
        {(state.kind === "error" || state.kind === "unavailable") && (
          <button type="button" aria-disabled={busy} onClick={retry}>
            {busy ? "Retrying…" : "Retry"}
          </button>
        )}
      </div>
    </main>
  );
}
