"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { RiskPill } from "@/components/Decision/Pills";
import { EmptyState, ReadView } from "@/components/states/States";
import { useApp } from "@/components/shell/AppShell";
import type { Page, PendingStep } from "@/lib/api/types";
import { roleLabel } from "@/lib/api/types";
import { useAgentNames } from "@/lib/api/useAgents";
import { useRead } from "@/lib/api/useRead";
import { timeLeft } from "@/lib/time";
import { useNow } from "@/lib/useNow";

import { Pane } from "./Pane";

const SHORTCUTS = "aop.shortcuts";
const TONE = { normal: "text-muted", warning: "font-semibold text-warning", overdue: "font-semibold text-negative" } as const;

// A preference only; a blocked store just means the default (on).
const readPref = () => {
  try {
    return localStorage.getItem(SHORTCUTS) !== "off";
  } catch {
    return true;
  }
};

function Row({ step, current, agent, now, onOpen }: { step: PendingStep; current: boolean; agent: string; now: number; onOpen: () => void }) {
  const left = timeLeft(step.due_at, now);
  return (
    <li>
      <button
        type="button"
        data-row
        aria-current={current}
        onClick={onOpen}
        className="block w-full border-0 border-b border-rule bg-transparent px-4 py-3 text-left hover:bg-raised aria-[current=true]:bg-active max-lg:min-h-16 lg:pl-3"
      >
        <span className="flex items-baseline justify-between gap-3">
          <span title={step.title} className="min-w-0 truncate font-semibold">
            {step.title}
          </span>
          {left && (
            <span className={`flex-none text-small whitespace-nowrap tabular-nums ${TONE[left.tone]}`}>
              {left.label}
              {left.note && <span className="sr-only">. {left.note}</span>}
            </span>
          )}
        </span>
        <span className="mt-1.5 flex flex-wrap items-center gap-2 text-small text-muted">
          <span className="font-mono">{agent}</span>
          <RiskPill risk={step.effective_risk_level} />
          <span>
            Level {step.level} · {roleLabel(step.required_role)}
          </span>
        </span>
      </button>
    </li>
  );
}

/** The approval inbox: pending steps soonest first on the left, the open one with its rail and decision bar on the right. */
export function Inbox() {
  const { refreshInbox } = useApp();
  const list = useRead<Page<PendingStep>>("/me/pending-approvals?limit=100");
  const agents = useAgentNames();
  const now = useNow();
  const [chosen, setChosen] = useState<PendingStep | null>(null);
  const [view, setView] = useState<"list" | "pane">("list");
  const [shortcuts, setShortcuts] = useState(readPref);
  const root = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);

  const items = list.data?.items ?? [];
  // The first step opens by default (wide screens show list and pane together) and then stays: a decided step must not
  // be swapped for the next one under the user's eyes.
  if (chosen === null && items[0]) setChosen(items[0]);
  const open = chosen;

  useEffect(() => {
    if (view === "pane") back.current?.focus();
  }, [view]);

  const toggleShortcuts = () => {
    const next = !shortcuts;
    setShortcuts(next);
    try {
      localStorage.setItem(SHORTCUTS, next ? "on" : "off");
    } catch {}
  };

  // Arrow keys move focus between rows; A and R press the pane's own buttons, so they obey the same disabled rules.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const rows = [...(root.current?.querySelectorAll<HTMLElement>("[data-row]") ?? [])];
      const i = rows.indexOf(t);
      if (i < 0) return;
      e.preventDefault();
      rows[Math.min(Math.max(i + (e.key === "ArrowDown" ? 1 : -1), 0), rows.length - 1)]?.focus();
      return;
    }
    if (!shortcuts || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
    const key = e.key.toLowerCase();
    if (key !== "a" && key !== "r") return;
    const action = root.current?.querySelector<HTMLButtonElement>(`[data-action=${key === "a" ? "approve" : "reject"}]`);
    if (!action) return;
    e.preventDefault();
    // Reject without a reason cannot be sent: take the user to the comment instead.
    if (key === "r" && action.disabled) root.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    else action.click();
  };

  const waiting = list.status === "success" ? items.length : 0;
  const soonest = items[0] ? timeLeft(items[0].due_at, now) : null;
  const headline =
    list.status !== "success"
      ? ""
      : waiting === 0
        ? "Nothing is waiting on you."
        : `${waiting} ${waiting === 1 ? "step waits" : "steps wait"} on you.${soonest && soonest.ms > 0 ? ` The soonest times out in ${soonest.label.replace(" left", "")}.` : " The soonest is overdue."}`;

  return (
    // The key handler only moves focus and presses buttons that are themselves keyboard-operable.
    <div ref={root} onKeyDown={onKeyDown} className="flex flex-col">
      <div className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h1 className="m-0">Inbox</h1>
          {list.status === "success" && list.isFetching && (
            <span role="status" className="text-small text-muted">
              Updating
            </span>
          )}
        </div>
        {headline && <p className="m-0 mt-1 text-muted">{headline}</p>}
      </div>

      <div className="grid min-h-0 gap-0 lg:grid-cols-[22rem_minmax(0,1fr)] lg:rounded-card lg:border lg:border-border">
        <div className={`${view === "pane" ? "max-lg:hidden" : ""} min-w-0 lg:border-r lg:border-border`}>
          <ReadView
            read={list}
            label="Waiting on me"
            isEmpty={(p) => p.items.length === 0}
            empty={
              <EmptyState
                message="Nothing is waiting on you"
                action={
                  <p className="m-0 text-muted">
                    A step appears here when an agent&apos;s request reaches your level.{" "}
                    <Link href="/decisions" className="text-text underline underline-offset-2">
                      See all decisions
                    </Link>
                  </p>
                }
              />
            }
          >
            {(p) => (
              <ul aria-label="Steps waiting on me" className="m-0 list-none p-0">
                {p.items.map((s) => (
                  <Row
                    key={s.id}
                    step={s}
                    current={open?.id === s.id}
                    agent={agents.name(s.requested_by_agent_id)}
                    now={now}
                    onOpen={() => {
                      setChosen(s);
                      setView("pane");
                    }}
                  />
                ))}
              </ul>
            )}
          </ReadView>
        </div>

        <div className={`${view === "list" ? "max-lg:hidden" : ""} flex min-w-0 flex-col p-4 lg:p-6`}>
          <button
            ref={back}
            type="button"
            onClick={() => setView("list")}
            className="mb-3 inline-flex h-touch w-fit items-center bg-transparent pr-3 font-medium text-muted lg:hidden"
          >
            ← Inbox
          </button>
          {open ? (
            <Pane
              key={open.id}
              step={open}
              agentName={agents.name(open.requested_by_agent_id)}
              shortcuts={shortcuts}
              onToggleShortcuts={toggleShortcuts}
              onDecided={() => {
                refreshInbox();
                void list.refetch();
              }}
            />
          ) : (
            list.status === "success" && <p className="m-0 text-muted">Select a request to see its votes and where it stands.</p>
          )}
        </div>
      </div>
    </div>
  );
}
