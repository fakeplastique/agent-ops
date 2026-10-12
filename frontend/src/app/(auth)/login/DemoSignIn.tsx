"use client";

import { useState } from "react";

import { api } from "@/lib/api/client";
import { goTo } from "@/lib/api/navigate";
import { roleLabel } from "@/lib/api/types";
import { useRead } from "@/lib/api/useRead";
import { resetClient } from "@/lib/session";

import { Notice, secondaryButton } from "../AuthFrame";

type DemoUser = { email: string; name: string; role: string; organization: string };

/** "Sign in as" buttons for the seeded users. The API serves the roster only with DEMO_MODE (404 otherwise): then nothing shows. */
export function DemoSignIn() {
  const roster = useRead<{ users: DemoUser[] }>("/auth/demo/users");
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const users = roster.data?.users ?? [];
  if (roster.status !== "success" || users.length === 0) return null;

  async function signIn(u: DemoUser) {
    if (busy) return;
    setBusy(u.email);
    setFailed(null);
    try {
      await api("/auth/demo/login", { method: "POST", body: { email: u.email } });
      resetClient(); // a different identity: nothing of the previous one may survive the full load
      goTo("/inbox");
    } catch {
      setFailed(u.name);
      setBusy(null);
    }
  }

  return (
    <section aria-label="Demo sign-in" className="flex flex-col gap-2 border-t border-border pt-4">
      <h2 className="m-0 text-body font-semibold">Sign in as</h2>
      <p className="m-0 text-small text-muted">Demo only: pick a seeded person to see the app from their role.</p>
      {failed && <Notice title={`Could not sign in as ${failed}`}>Nothing changed. Try again.</Notice>}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {users.map((u) => (
          <li key={u.email}>
            <button
              type="button"
              aria-disabled={busy !== null}
              onClick={() => void signIn(u)}
              className={`${secondaryButton} h-auto w-full flex-col items-start gap-0 py-2 text-left max-lg:h-auto max-lg:min-h-touch`}
            >
              <span>{u.name}</span>
              <span className="text-small font-normal text-muted">
                {roleLabel(u.role)} · {u.organization}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
