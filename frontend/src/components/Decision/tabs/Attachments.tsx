"use client";

import { useState, type ChangeEvent } from "react";

import { Notice, retryNote } from "@/app/(auth)/AuthFrame";
import { ReadView } from "@/components/states/States";
import { ApiError, api } from "@/lib/api/client";
import type { Decision, Page } from "@/lib/api/types";
import { useRead } from "@/lib/api/useRead";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

type Attachment = { id: string; filename: string; content_type: string; size_bytes: number; uploaded_by_type: string; created_at: string };

const MAX = 10 * 1024 * 1024;
const TOO_BIG = "That file is larger than 10 MB. Attach a smaller one.";
const BAD_TYPE = "That file type is not allowed. Allowed: PDF, PNG, JPEG, GIF, WebP, TXT, CSV, JSON, DOCX and XLSX.";

function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  return n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

// The rule, in our words: the server's own text is never shown.
function uploadFailure(e: unknown): string {
  if (e instanceof ApiError && e.status === 413) return TOO_BIG;
  if (e instanceof ApiError && e.status === 415) return BAD_TYPE;
  if (e instanceof ApiError && e.status === 402) return "Attachments need the Pro plan. An org admin can change the plan.";
  return retryNote(e);
}

/** Files on the request (Pro): download links go through the API, which checks access and redirects to a short-lived URL. */
export function Attachments({ decision }: { decision: Decision }) {
  const base = `/decision-requests/${encodeURIComponent(decision.id)}/attachments`;
  const read = useRead<Page<Attachment>>(`${base}?limit=100`);
  const now = useNow();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function upload(e: ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const file = input.files?.[0];
    input.value = ""; // the same file can be chosen again after a refusal
    if (!file || busy) return;
    if (file.size > MAX) return setProblem(TOO_BIG);
    setBusy(true);
    setProblem(null);
    try {
      // The body is the file itself; its own content type is the declared type.
      await api(`${base}?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file, timeoutMs: 120_000 });
      void read.refetch();
    } catch (err) {
      setProblem(uploadFailure(err));
    } finally {
      setBusy(false);
    }
  }

  const picker = (
    <div className="flex flex-col gap-2">
      <label className="inline-flex h-control w-fit cursor-pointer items-center rounded-control border border-field bg-surface px-4 font-medium focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent hover:bg-raised max-lg:h-touch coarse:h-touch">
        {busy ? "Uploading…" : "Add a file"}
        <input type="file" aria-label="Add a file" disabled={busy} onChange={(e) => void upload(e)} className="sr-only" />
      </label>
      <p className="m-0 text-small text-muted">Up to 10 MB. PDF, images, text, CSV, JSON, Word and Excel.</p>
      {problem && <Notice title="Could not attach the file">{problem}</Notice>}
    </div>
  );

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <ReadView
        read={read}
        label="Attachments"
        gate={{ feature: "Attachments", description: "Agents and people can attach files to a request, such as screenshots, exports or evidence for the approver." }}
        isEmpty={(p) => p.items.length === 0}
        empty={
          <div className="flex flex-col gap-3 py-2">
            <p className="m-0">No files attached.</p>
            {picker}
          </div>
        }
      >
        {(p) => (
          <>
            <ul aria-label="Files" className="m-0 list-none p-0">
              {p.items.map((a) => (
                <li key={a.id} className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-rule py-2.5 last:border-b-0">
                  {/* The name is agent-controlled: a text node inside a link to our own download route. */}
                  <a href={`/api/v1${base}/${encodeURIComponent(a.id)}/download`} download className="min-w-0 font-medium wrap-anywhere text-text underline underline-offset-2">
                    {a.filename}
                  </a>
                  <span className="text-small text-muted tabular-nums">
                    {bytes(a.size_bytes)} · {a.uploaded_by_type === "agent" ? "agent" : "person"} · {clockTime(a.created_at, now)}
                  </span>
                </li>
              ))}
            </ul>
            {picker}
          </>
        )}
      </ReadView>
    </div>
  );
}
