"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";

import { Nav } from "./Nav";

type Props = {
  children: ReactNode;
  /** Organization switcher (S37) shown above the nav. */
  org?: ReactNode;
  /** Right side of the top bar: search when the flag allows, account. */
  topbar?: ReactNode;
  /** Pending steps for me; shown on Inbox only when above zero. */
  inboxCount?: number;
};

function Brand() {
  return (
    <span className="flex items-center gap-2 px-1 text-section font-semibold">
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5 flex-none text-accent">
        <g fill="currentColor">
          <circle cx="4" cy="5" r="2.2" />
          <circle cx="4" cy="12" r="2.2" />
          <circle cx="4" cy="19" r="2.2" />
          <circle cx="19" cy="12" r="3.2" />
        </g>
        <path d="M6 5l10 7M6 12h10M6 19l10-7" fill="none" stroke="currentColor" strokeWidth="1.4" />
      </svg>
      Agent Ops
    </span>
  );
}

// Framed sheet: tinted frame (sidebar, top bar) around one white sheet. Below lg the sidebar folds into a menu sheet.
export function Shell({ children, org, topbar, inboxCount }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <div className="grid min-h-dvh grid-rows-[var(--spacing-topbar)_1fr] bg-ground text-text lg:grid-cols-[var(--spacing-sidebar)_minmax(0,1fr)]">
      <aside aria-label="Sidebar" className="row-span-2 hidden flex-col gap-4 px-3 py-4 lg:flex">
        <Brand />
        {org}
        <Nav inboxCount={inboxCount} />
      </aside>

      <header className="flex h-topbar items-center justify-between gap-3 px-2 lg:col-start-2 lg:pr-5">
        <Dialog.Root open={open} onOpenChange={setOpen}>
          <Dialog.Trigger className="inline-flex h-control items-center rounded-control border border-control bg-surface px-3 font-medium max-lg:h-touch lg:hidden">
            Menu
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-text/20 lg:hidden" />
            <Dialog.Content
              aria-describedby={undefined}
              className="fixed inset-y-0 left-0 flex w-sidebar flex-col gap-4 overflow-y-auto bg-ground px-3 py-4 shadow-float lg:hidden"
            >
              <div className="flex items-center justify-between">
                <Dialog.Title asChild>
                  <h2 className="m-0">
                    <Brand />
                  </h2>
                </Dialog.Title>
                <Dialog.Close className="inline-flex h-touch items-center rounded-control px-3 text-muted hover:bg-surface hover:text-text">
                  Close
                </Dialog.Close>
              </div>
              {org}
              <Nav inboxCount={inboxCount} onNavigate={() => setOpen(false)} />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
        <div className="ml-auto flex items-center gap-3 text-muted">{topbar}</div>
      </header>

      <main
        id="main"
        className="mx-inset mb-inset min-w-0 rounded-sheet border border-border bg-surface p-4 sm:p-6 lg:col-start-2 lg:ml-0"
      >
        {children}
      </main>
    </div>
  );
}
