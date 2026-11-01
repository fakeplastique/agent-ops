"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export type TabDef = { id: string; label: string };

/** Tabs with roving focus: arrows, Home and End move and select; the list scrolls sideways on a phone. */
export function Tabs({ tabs, selected, onSelect, children }: { tabs: TabDef[]; selected: string; onSelect: (id: string) => void; children: ReactNode }) {
  const base = useId();
  const list = useRef<HTMLDivElement>(null);
  const current = tabs.some((t) => t.id === selected) ? selected : tabs[0].id;

  const onKeyDown = (e: KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.id === current);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length];
    onSelect(next.id);
    list.current?.querySelector<HTMLElement>(`[data-tab="${next.id}"]`)?.focus();
  };

  return (
    <div>
      <div ref={list} role="tablist" aria-label="Request details" onKeyDown={onKeyDown} className="-mx-4 flex gap-5 overflow-x-auto border-b border-border px-4 sm:-mx-6 sm:px-6">
        {tabs.map((t) => (
          <button
            key={t.id}
            id={`${base}-${t.id}`}
            data-tab={t.id}
            role="tab"
            type="button"
            aria-selected={t.id === current}
            aria-controls={`${base}-panel`}
            tabIndex={t.id === current ? 0 : -1}
            onClick={() => onSelect(t.id)}
            className="-mb-px flex-none border-0 border-b-2 border-transparent bg-transparent py-2 font-medium whitespace-nowrap text-muted hover:text-text aria-selected:border-text aria-selected:text-text max-lg:min-h-touch"
          >
            {t.label}
          </button>
        ))}
      </div>
      <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-${current}`} tabIndex={0} className="pt-5">
        {children}
      </div>
    </div>
  );
}
