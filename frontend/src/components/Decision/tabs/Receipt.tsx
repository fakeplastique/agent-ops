"use client";

import { useRef, useState } from "react";

import { Notice, retryNote } from "@/app/(auth)/AuthFrame";
import { PayloadBlock } from "@/components/Decision/PayloadBlock";
import { ReadView } from "@/components/states/States";
import { api } from "@/lib/api/client";
import type { Decision } from "@/lib/api/types";
import { useRead } from "@/lib/api/useRead";

type ReceiptObject = { alg: string; kid: string; payload: Record<string, unknown>; signature: string };
type Verdict = { valid: boolean } | { failed: unknown } | null;

function Body({ decision }: { decision: Decision }) {
  const read = useRead<ReceiptObject>(`/decision-requests/${encodeURIComponent(decision.id)}/receipt`);
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [checking, setChecking] = useState(false);
  const inFlight = useRef(false);

  async function verify(receipt: ReceiptObject) {
    if (inFlight.current) return;
    inFlight.current = true;
    setChecking(true);
    setVerdict(null);
    try {
      setVerdict(await api<{ valid: boolean }>("/receipts/verify", { method: "POST", body: receipt }));
    } catch (failed) {
      setVerdict({ failed }); // not "broken": the check itself did not run
    } finally {
      inFlight.current = false;
      setChecking(false);
    }
  }

  return (
    <ReadView read={read} label="Receipt">
      {(r) => (
        <div className="flex max-w-3xl flex-col gap-4">
          <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1.5">
            <dt className="text-muted">Algorithm</dt>
            <dd className="m-0 font-mono text-small">{r.alg}</dd>
            <dt className="text-muted">Key id</dt>
            <dd className="m-0 font-mono text-small wrap-anywhere">{r.kid}</dd>
            <dt className="text-muted">Signature</dt>
            <dd className="m-0 font-mono text-small break-all">{r.signature}</dd>
          </dl>
          <PayloadBlock payload={r.payload} label="Receipt payload" />
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              aria-disabled={checking}
              onClick={() => void verify(r)}
              className="inline-flex h-control items-center rounded-control border border-field bg-surface px-4 font-medium hover:bg-raised max-lg:h-touch coarse:h-touch aria-disabled:opacity-60"
            >
              {checking ? "Verifying…" : "Verify"}
            </button>
            <span role="status" className="text-body">
              {verdict && "valid" in verdict && (verdict.valid ? <span className="text-positive">Valid. It matches the current request, version {decision.version}.</span> : <span className="font-medium text-negative">Broken. The request no longer matches its signed receipt.</span>)}
            </span>
          </div>
          {verdict && "failed" in verdict && <Notice title="Could not verify">{retryNote(verdict.failed)}</Notice>}
        </div>
      )}
    </ReadView>
  );
}

/** Signature block and a Verify button. Keyed by what the request is now, so a result never outlives a change to it. */
export function Receipt({ decision }: { decision: Decision }) {
  if (decision.status === "approved") return <Body key={`${decision.content_hash}|${decision.updated_at}`} decision={decision} />;
  return (
    <p className="m-0 max-w-(--measure) text-muted">
      {decision.status === "pending_votes" || decision.status === "pending_discussion" || decision.status === "pending_approval"
        ? "The receipt is signed when the request is approved."
        : "No receipt is issued for this request."}
    </p>
  );
}
