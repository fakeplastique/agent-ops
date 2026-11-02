"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { ConnectionBanner } from "@/components/ConnectionBanner";
import { OrgSwitcher } from "@/components/OrgSwitcher";
import { ReadView } from "@/components/states/States";
import { goTo } from "@/lib/api/navigate";
import type { Page } from "@/lib/api/types";
import { roleLabel } from "@/lib/api/types";
import { useRead } from "@/lib/api/useRead";
import { isApprovalEvent, RealtimeProvider, useRealtimeEvents } from "@/lib/realtime/react";
import { signOut, useIdentitySync, useSession, type Session } from "@/lib/session";

import { Shell } from "./Shell";

type Organization = { id: string; name: string; role: string };
type App = {
  session: Session & { organization_id: string };
  /** Re-reads the inbox count after a decision. */
  refreshInbox: () => void;
};

export const AppContext = createContext<App | null>(null);

/** The signed-in session inside the shell (organization and role); only rendered once both are known. */
export function useApp(): App {
  const app = useContext(AppContext);
  if (!app) throw new Error("useApp outside AppShell");
  return app;
}

function OrganizationSlot({ session }: { session: Session }) {
  const orgs = useRead<{ items: Organization[] }>("/me/organizations");
  if (orgs.status === "error")
    return (
      <div className="flex flex-col items-start gap-1 px-1 text-small text-muted">
        <span>Organizations did not load.</span>
        <button type="button" onClick={() => void orgs.refetch()} className="text-text underline underline-offset-2">
          Retry organizations
        </button>
      </div>
    );
  if (orgs.status === "pending") return <span aria-hidden="true" className="mx-1 h-control w-2/3 rounded-pill bg-active" />;
  return <OrgSwitcher organizations={orgs.data?.items ?? []} currentId={session.organization_id} />;
}

function UserRow({ role }: { role: string | null }) {
  return (
    <div className="flex items-center justify-between gap-2 px-1">
      <span className="truncate font-medium">{role ? roleLabel(role) : ""}</span>
      <button
        type="button"
        onClick={() => void signOut()}
        className="inline-flex h-control items-center rounded-control px-3 text-muted hover:bg-surface hover:text-text max-lg:h-touch"
      >
        Sign out
      </button>
    </div>
  );
}

/** The app frame for every screen behind sign-in: the S36 Shell fed by the session, the organizations and the inbox count. */
export function AppShell({ children }: { children: ReactNode }) {
  const session = useSession();
  useIdentitySync();
  const pending = useRead<Page<unknown>>(session.status === "success" ? "/me/pending-approvals?limit=1" : null);
  const refetchPending = pending.refetch;
  const noOrg = session.status === "success" && !session.data?.organization_id;

  // Signed in but in no organization: nothing here can load; onboarding creates or joins one.
  useEffect(() => {
    if (noOrg) goTo("/onboarding");
  }, [noOrg]);

  const data = session.data;
  const app = useMemo<App | null>(
    () =>
      data?.organization_id
        ? { session: { ...data, organization_id: data.organization_id }, refreshInbox: () => void refetchPending() }
        : null,
    [data, refetchPending],
  );

  if (session.status !== "success" || !app) {
    return (
      <div className="min-h-dvh bg-ground p-inset">
        <div className="min-h-[calc(100dvh-2*var(--spacing-inset))] rounded-sheet border border-border bg-surface p-6">
          {noOrg ? <p className="m-0 text-muted">Taking you to set up your organization…</p> : <ReadView read={session} label="Session">{() => null}</ReadView>}
        </div>
      </div>
    );
  }

  return (
    <RealtimeProvider>
      <AppContext.Provider value={app}>
        <LiveCount refetch={refetchPending} />
        <Shell
          org={<OrganizationSlot session={app.session} />}
          user={<UserRow role={app.session.role} />}
          inboxCount={pending.status === "success" ? (pending.data?.total ?? 0) : 0}
        >
          <ConnectionBanner />
          {children}
        </Shell>
      </AppContext.Provider>
    </RealtimeProvider>
  );
}

/** The sidebar's inbox count follows the feed (the Inbox and Decision screens re-read their own lists). */
function LiveCount({ refetch }: { refetch: () => unknown }) {
  useRealtimeEvents((events) => {
    if (events.some(isApprovalEvent)) void refetch();
  });
  return null;
}
