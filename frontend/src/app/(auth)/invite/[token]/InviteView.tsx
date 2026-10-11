"use client";

import Link from "next/link";
import { useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import { enterOrganization, signOut } from "@/lib/session";

import { AuthFrame, Notice, primaryButton, retryNote, secondaryButton } from "../../AuthFrame";

type State =
  | { kind: "idle" | "busy" | "unusable" | "member" }
  | { kind: "failed"; note: string }
  | { kind: "mismatch"; email: string };

// The API gives one answer for unknown, expired, used and revoked; a malformed token (422) and any 404 read the same.
function outcome(e: unknown): State {
  if (!(e instanceof ApiError)) return { kind: "failed", note: retryNote(e) };
  if (e.code === "invitation_email_mismatch" && typeof e.details.invited_email === "string")
    return { kind: "mismatch", email: e.details.invited_email };
  if (e.code === "already_member") return { kind: "member" };
  if (e.status === 404 || e.status === 422) return { kind: "unusable" };
  return { kind: "failed", note: retryNote(e) };
}

export function InviteView({ token }: { token: string }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const busy = state.kind === "busy";

  // Joining needs a press: a link opened by a mail scanner must not use the invitation.
  const accept = async () => {
    if (busy) return;
    setState({ kind: "busy" });
    try {
      await api("/invitations/accept", { method: "POST", body: { token } });
    } catch (e) {
      return setState(outcome(e));
    }
    enterOrganization(); // the server already switched the session; stay "busy" until the page goes
  };

  return (
    <AuthFrame title="Join an organization" lead="You were invited to Agent Ops. Accept to join with the role your admin chose.">
      {state.kind === "unusable" && (
        <Notice title="This invitation can't be used">Ask the person who invited you for a new link.</Notice>
      )}
      {state.kind === "mismatch" && (
        <>
          <Notice title={`This invitation is for ${state.email}`}>
            You are signed in with a different Google account. Sign out, then sign in with {state.email}.
          </Notice>
          <button type="button" onClick={() => void signOut(window.location.pathname)} className={`self-start ${secondaryButton}`}>
            Sign out
          </button>
        </>
      )}
      {state.kind === "member" && (
        <>
          <Notice title="You are already a member of this organization" />
          <Link href="/inbox" className={`self-start ${primaryButton}`}>
            Open Agent Ops
          </Link>
        </>
      )}
      {state.kind === "failed" && (
        <Notice title="The invitation was not accepted">{state.note}</Notice>
      )}
      {(state.kind === "idle" || state.kind === "busy" || state.kind === "failed") && (
        <button type="button" aria-disabled={busy} onClick={() => void accept()} className={`self-start ${primaryButton}`}>
          {busy ? "Accepting…" : "Accept invitation"}
        </button>
      )}
    </AuthFrame>
  );
}
