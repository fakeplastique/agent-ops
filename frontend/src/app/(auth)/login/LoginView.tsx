"use client";

import { rememberNext, resetClient } from "@/lib/session";

import { AuthFrame, Notice, primaryButton } from "../AuthFrame";

// Keyed by the callback's error code; the parameter's own text never reaches the page. No copy says whether an account exists.
const ERRORS: Record<string, { title: string; note: string }> = {
  access_denied: { title: "Sign-in was cancelled", note: "Google did not share your account with Agent Ops. Continue to try again." },
  login_unavailable: { title: "Sign-in is not available right now", note: "Try again in a few minutes." },
};
const FAILED = { title: "Sign-in did not complete", note: "Use a Google account with a verified email address, then try again." };

export function LoginView({ error, next }: { error: string | null; next: string | null }) {
  // Own keys only: `constructor` or `__proto__` would otherwise come off the prototype.
  const problem = error ? (Object.hasOwn(ERRORS, error) ? ERRORS[error] : FAILED) : null;

  // The next identity starts clean: nothing the previous user left in this browser survives the round trip.
  const begin = () => {
    resetClient();
    rememberNext(next);
  };

  return (
    <AuthFrame title="Sign in to Agent Ops" lead="Review and sign off what your agents ask to do.">
      {problem && <Notice title={problem.title}>{problem.note}</Notice>}
      {/* A plain link: the API answers with the redirect to Google, so it works before scripts load. */}
      <a href="/api/v1/auth/login" onClick={begin} className={primaryButton}>
        Continue with Google
      </a>
    </AuthFrame>
  );
}
