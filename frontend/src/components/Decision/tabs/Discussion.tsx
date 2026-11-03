"use client";

import { useId, useRef, useState, type FormEvent } from "react";

import { Notice, retryNote } from "@/app/(auth)/AuthFrame";
import { EmptyState, ReadView } from "@/components/states/States";
import { ApiError, api } from "@/lib/api/client";
import type { Decision, Page } from "@/lib/api/types";
import { useAgentNames } from "@/lib/api/useAgents";
import { useRead } from "@/lib/api/useRead";
import { clockTime } from "@/lib/time";
import { useNow } from "@/lib/useNow";

type Message = { id: string; author_type: "agent" | "user" | "system"; author_id: string | null; body: string; request_version: number; created_at: string };

const OPEN = new Set(["pending_discussion", "pending_approval"]);

/** Why the box is off, or null when posting is open (the API accepts messages only in these statuses). */
function closedReason(d: Decision): string | null {
  if (OPEN.has(d.status)) return null;
  return d.status === "pending_votes"
    ? "Reviewers vote privately first. The thread is system-only until round 1 closes."
    : "This request is already decided, so the thread is read-only.";
}

function postFailure(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) return "The discussion is closed for this request. Nothing was posted.";
  if (e instanceof ApiError && e.status === 403) return "You can't post here. Nothing was posted.";
  return retryNote(e);
}

/** Agent and person messages as plain text (never parsed), and a box to post while the discussion is open. */
export function Discussion({ decision }: { decision: Decision }) {
  const path = `/decision-requests/${encodeURIComponent(decision.id)}/messages`;
  const read = useRead<Page<Message>>(`${path}?limit=100`);
  const agents = useAgentNames();
  const now = useNow();
  const id = useId();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const inFlight = useRef(false);
  const why = closedReason(decision);

  async function post(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current || why || !text.trim()) return;
    inFlight.current = true;
    setSending(true);
    setProblem(null);
    try {
      await api(path, { method: "POST", body: { body: text } });
      setText("");
      void read.refetch();
    } catch (err) {
      setProblem(postFailure(err)); // the text stays, so nothing typed is lost
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  const who = (m: Message) => (m.author_type === "system" ? "System" : m.author_type === "agent" ? agents.name(m.author_id) : "Team member");

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <ReadView read={read} label="Discussion" isEmpty={(p) => p.items.length === 0} empty={<EmptyState message="No messages yet. Reviewers and approvers can talk here once the discussion opens." />}>
        {(p) => (
          <>
            <ol aria-label="Messages" className="m-0 list-none p-0">
              {p.items.map((m) => (
                <li key={m.id} className="border-b border-rule py-3 last:border-b-0">
                  <p className="m-0 flex flex-wrap items-baseline gap-x-2 text-small text-muted">
                    <span className={`font-medium text-text ${m.author_type === "agent" ? "font-mono" : ""}`}>{who(m)}</span>
                    <span className="tabular-nums">{clockTime(m.created_at, now)}</span>
                    <span>Version {m.request_version}</span>
                  </p>
                  {/* Agent-authored: a text node, so markup, markdown and links stay inert. */}
                  <p className="m-0 mt-1 whitespace-pre-wrap wrap-anywhere">{m.body}</p>
                </li>
              ))}
            </ol>
            {p.total > p.items.length && <p className="m-0 text-small text-muted">Showing the first {p.items.length} of {p.total} messages.</p>}
          </>
        )}
      </ReadView>

      <form onSubmit={post} className="flex flex-col gap-2" aria-label="Post a message">
        <label htmlFor={`${id}-m`} className="font-medium">
          Message
        </label>
        <textarea
          id={`${id}-m`}
          value={text}
          rows={3}
          maxLength={4096}
          disabled={why !== null}
          aria-describedby={why ? `${id}-why` : undefined}
          onChange={(e) => setText(e.target.value)}
          className="w-full resize-y rounded-control border border-field bg-surface px-2.5 py-2 text-text disabled:bg-raised disabled:text-muted"
        />
        {why && (
          <p id={`${id}-why`} className="m-0 text-small text-muted">
            {why}
          </p>
        )}
        {problem && <Notice title="Could not post">{problem}</Notice>}
        <div>
          <button
            type="submit"
            disabled={why !== null || !text.trim()}
            aria-disabled={sending}
            className="inline-flex h-control items-center rounded-control bg-text px-4 font-medium text-surface transition-opacity hover:opacity-85 max-lg:h-touch coarse:h-touch disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending ? "Posting…" : "Post"}
          </button>
        </div>
      </form>
    </div>
  );
}
