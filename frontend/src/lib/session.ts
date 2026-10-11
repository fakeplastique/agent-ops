"use client";

import { useCallback, useEffect, useState } from "react";

import type { Read } from "@/components/states/States";
import { api } from "@/lib/api/client";
import { goTo } from "@/lib/api/navigate";

export type Session = { user_id: string; organization_id: string | null; role: string | null };

const NEXT_KEY = "aop.next";
const CHANNEL = "aop.identity";
const HOME = "/inbox";

/** A same-origin relative path, or null. Rejects `//host`, `/\host`, any backslash, control characters and absolute URLs. */
export function safeNext(raw: string | null | undefined): string | null {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return null;
  // Browsers read `\` as `/` and drop tabs and newlines, so `/\host` and `/\t/host` both become `//host`.
  return /[\\\u0000-\u001f\u007f]/.test(raw) ? null : raw;
}

/** Keeps where to return after the Google round trip; only a safe path is stored. */
export function rememberNext(raw: string | null) {
  const next = safeNext(raw);
  try {
    if (next) sessionStorage.setItem(NEXT_KEY, next);
  } catch {} // storage blocked: the user lands on the default page
}

/** The stored path, once, re-checked on the way out. */
export function takeNext(): string | null {
  try {
    const raw = sessionStorage.getItem(NEXT_KEY);
    sessionStorage.removeItem(NEXT_KEY);
    return safeNext(raw);
  } catch {
    return null;
  }
}

/** Drops everything this browser kept for the previous identity or organization and tells other open tabs. */
export function resetClient() {
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {}
  tabChannel()?.postMessage("identity-changed");
}

// One channel per tab: a message is never delivered to the object that posted it, so a tab ignores its own change.
let channel: BroadcastChannel | null = null;
const tabChannel = () =>
  typeof BroadcastChannel === "undefined" ? null : (channel ??= new BroadcastChannel(CHANNEL));

/** Ends the session (the local copy goes even if the call fails) with a full load; `back` returns the next sign-in there. */
export async function signOut(back?: string) {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {}
  resetClient();
  const next = safeNext(back);
  goTo(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
}

/** Selects another organization; only once the server agreed is the client cleared and reloaded. */
export async function switchOrganization(organizationId: string) {
  await api("/auth/organization", { method: "POST", body: { organization_id: organizationId } });
  enterOrganization();
}

/** After the server switched the session (create, accept, switch): clear and do a full load of the app home. */
export function enterOrganization() {
  resetClient();
  goTo(HOME);
}

/** Another tab changed who is signed in or which organization is selected: reload so this tab shows no stale data. */
export function useIdentitySync() {
  useEffect(() => {
    const ch = tabChannel();
    // Without the hash: assigning a URL that differs only by its hash would scroll, not reload.
    const reload = () => goTo(window.location.pathname + window.location.search);
    // Back after a sign-out can restore this page from the back/forward cache with the old DOM in it.
    const onShow = (e: PageTransitionEvent) => e.persisted && reload();
    ch?.addEventListener("message", reload);
    window.addEventListener("pageshow", onShow);
    return () => {
      ch?.removeEventListener("message", reload);
      window.removeEventListener("pageshow", onShow);
    };
  }, []);
}

/** The signed-in session as a Read for ReadView; a 401 sends the user to sign in. */
export function useSession(): Read<Session> {
  const [read, setRead] = useState<Omit<Read<Session>, "refetch">>({ status: "pending", isFetching: true });
  // Only the promise sets state, so the first read can start from an effect; the initial state is already pending.
  const fetchSession = useCallback(
    () =>
      api<Session>("/auth/session").then(
        (data) => setRead({ status: "success", data, isFetching: false }),
        (error: unknown) => setRead({ status: "error", error, isFetching: false }),
      ),
    [],
  );
  useEffect(() => void fetchSession(), [fetchSession]);
  const load = () => {
    setRead((r) => ({ ...r, isFetching: true }));
    void fetchSession();
  };
  return { ...read, refetch: load };
}
