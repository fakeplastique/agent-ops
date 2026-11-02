"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Notice, primaryButton, retryNote, secondaryButton } from "@/app/(auth)/AuthFrame";
import { Forbidden, ReadView } from "@/components/states/States";
import { ApiError } from "@/lib/api/client";
import { useSession, type Session } from "@/lib/session";

export { Notice, primaryButton, secondaryButton };

export const dangerButton =
  "inline-flex h-control items-center justify-center rounded-control border border-negative bg-surface px-4 font-medium text-negative transition-colors hover:bg-pill-negative-bg max-lg:h-touch aria-disabled:cursor-default aria-disabled:opacity-60";
export const inputClass =
  "h-control w-full rounded-control border border-control bg-surface px-3 text-text max-lg:h-touch aria-invalid:border-negative";

const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
/** "3 Nov 2026" in UTC, so the same instant reads the same for everyone; empty for a bad value. */
export const fmtDate = (iso: string) => (Number.isNaN(Date.parse(iso)) ? "" : dateFmt.format(new Date(iso)));
const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});
export const fmtDateTime = (iso: string) => (Number.isNaN(Date.parse(iso)) ? "" : `${dateTimeFmt.format(new Date(iso))} UTC`);

export const ROLES = ["member", "team_lead", "manager", "org_admin"] as const;
export const roleLabel = (r: string) => r.replace("_", " ");

/** Screen frame: title, the line under it, and "Updating" while a refetch runs (the content stays). */
export function Screen({
  title,
  lead,
  updating,
  actions,
  children,
}: {
  title: string;
  lead?: ReactNode;
  updating?: boolean;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="m-0 mb-1">{title}</h1>
          {lead && <p className="m-0 max-w-prose text-muted">{lead}</p>}
          {updating && (
            <p role="status" className="m-0 text-small text-muted">
              Updating
            </p>
          )}
        </div>
        {actions}
      </header>
      {children}
    </div>
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <h2 id={id} className="m-0 text-section font-medium">
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Two-line row: primary line, then secondary; actions drop under the text on a phone. */
export function Row({ children, actions, muted = false }: { children: ReactNode; actions?: ReactNode; muted?: boolean }) {
  return (
    <li className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-rule py-3 ${muted ? "text-muted" : ""}`}>
      <div className="min-w-0 flex-1 basis-60">{children}</div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </li>
  );
}

export const List = ({ children, label }: { children: ReactNode; label: string }) => (
  <ul aria-label={label} className="m-0 list-none border-t border-rule p-0">
    {children}
  </ul>
);

/** One line that truncates; the full text is on hover and focus (title). */
export const Trunc = ({ children, className = "" }: { children: string; className?: string }) => (
  <p title={children} className={`m-0 truncate ${className}`}>
    {children}
  </p>
);

const PILL = {
  positive: "bg-pill-positive-bg text-pill-positive-ink",
  negative: "bg-pill-negative-bg text-pill-negative-ink",
  warning: "bg-pill-warning-bg text-pill-warning-ink",
  neutral: "bg-pill-neutral-bg text-pill-neutral-ink",
};
/** Status is always text, never colour alone. */
export function Pill({ tone = "neutral", children }: { tone?: keyof typeof PILL; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-pill px-2 text-small font-medium ${PILL[tone]}`}>{children}</span>;
}

/** A list that was cut at the first 100 rows says so. */
export const MoreNote = ({ shown, total }: { shown: number; total: number }) =>
  total > shown ? (
    <p className="m-0 text-small text-muted">
      Showing the first {shown} of {total}.
    </p>
  ) : null;

type FieldProps = { id: string; "aria-invalid"?: true; "aria-describedby"?: string };
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: (p: FieldProps) => ReactNode;
}) {
  const id = useId();
  const note = error ?? hint;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": note ? `${id}-note` : undefined })}
      {note && (
        <p id={`${id}-note`} className={`m-0 text-small ${error ? "text-negative" : "text-muted"}`}>
          {note}
        </p>
      )}
    </div>
  );
}

/** What a failed write tells the user: a named reason for a known refusal, the API's own words for others, a connection note otherwise. */
export function writeError(e: unknown, reasons: Record<string, string> = {}): { title: string; body?: string } {
  if (e instanceof ApiError) {
    if (reasons[e.code]) return { title: reasons[e.code] };
    if (e.status === 403) return { title: "You do not have access to this", body: "It needs the org admin role." };
    if (e.status === 404) return { title: "Not found", body: "It may have been removed. Reload the page." };
    if (e.status === 0 || e.status >= 500) return { title: "Could not save", body: retryNote(e) };
    return { title: e.message };
  }
  return { title: "Could not save", body: retryNote(e) };
}

/** One write at a time; busy lasts until it settles, and the outcome stays for the form to show. */
export function useAction() {
  const [state, setState] = useState<{ busy: boolean; error: unknown }>({ busy: false, error: null });
  const busy = useRef(false);
  const run = async <R,>(fn: () => Promise<R>): Promise<{ ok: true; value: R } | { ok: false }> => {
    if (busy.current) return { ok: false };
    busy.current = true;
    setState({ busy: true, error: null });
    try {
      const value = await fn();
      setState({ busy: false, error: null });
      return { ok: true, value };
    } catch (error) {
      setState({ busy: false, error });
      return { ok: false };
    } finally {
      busy.current = false;
    }
  };
  return { ...state, run, clear: () => setState((s) => ({ ...s, error: null })) };
}

export function ActionError({ error, reasons }: { error: unknown; reasons?: Record<string, string> }) {
  if (!error) return null;
  const { title, body } = writeError(error, reasons);
  return <Notice title={title}>{body}</Notice>;
}

/** Two-step destructive action in place: no dialog, the choice stays next to what it affects. */
export function Confirm({
  label,
  question,
  confirmLabel,
  onConfirm,
  busy,
  tone = "danger",
}: {
  label: string;
  question: string;
  confirmLabel: string;
  onConfirm: () => unknown;
  busy?: boolean;
  tone?: "danger" | "plain";
}) {
  const [asking, setAsking] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const restore = useRef(false);
  const cls = tone === "danger" ? dangerButton : secondaryButton;
  // Back to the trigger after Cancel or a finished action, so keyboard focus is not dropped to the page.
  useEffect(() => {
    if (!asking && restore.current) {
      restore.current = false;
      trigger.current?.focus();
    }
  }, [asking]);
  const done = () => {
    restore.current = true;
    setAsking(false);
  };
  if (!asking)
    return (
      <button
        ref={trigger}
        type="button"
        className={cls}
        onClick={() => {
          setAsking(true);
          queueMicrotask(() => first.current?.focus());
        }}
      >
        {label}
      </button>
    );
  return (
    <span role="group" aria-label={question} className="flex flex-wrap items-center gap-2">
      <span>{question}</span>
      <button ref={first} type="button" aria-disabled={busy} className={cls} onClick={() => !busy && void Promise.resolve(onConfirm()).finally(done)}>
        {busy ? "Working…" : confirmLabel}
      </button>
      <button type="button" className={secondaryButton} onClick={done}>
        Cancel
      </button>
    </span>
  );
}

/**
 * A secret the API returns once (agent token, webhook secret, invitation link, personal access token).
 * The value lives only in the caller's state: closing clears it and nothing can reopen it.
 */
export function SecretDialog(props: {
  title: string;
  intro: string;
  label: string;
  secret: string | null;
  onClose: () => void;
}) {
  // Keyed on the secret: a second secret starts from a clean dialog (no "Copied." carried over).
  return props.secret === null ? null : <SecretBody key={props.secret} {...props} secret={props.secret} />;
}

function SecretBody({ title, intro, label, secret, onClose }: { title: string; intro: string; label: string; secret: string; onClose: () => void }) {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const box = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const copyIt = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopy("copied");
    } catch {
      box.current?.select(); // no clipboard (insecure context, denied): leave it selected to copy by hand
      setCopy("failed");
    }
  };

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-text/20" />
        <Dialog.Content
          aria-describedby={undefined}
          // Outside click and Escape must not throw away a value that can never be shown again.
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
          className="fixed top-1/2 left-1/2 flex max-h-[calc(100dvh-2rem)] w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 overflow-y-auto rounded-card bg-surface p-6 shadow-float"
        >
          <Dialog.Title className="m-0 text-section font-medium">{title}</Dialog.Title>
          <p className="m-0 text-muted">{intro}</p>
          <div className="flex flex-col gap-1">
            <label htmlFor={id} className="font-medium">
              {label}
            </label>
            <textarea
              id={id}
              ref={box}
              readOnly
              rows={3}
              value={secret}
              onFocus={(e) => e.currentTarget.select()}
              className="w-full resize-none rounded-control border border-control bg-raised p-3 font-mono text-small break-all"
            />
          </div>
          <p role="status" className="m-0 min-h-5 text-small text-muted">
            {copy === "copied" && "Copied."}
            {copy === "failed" && "Copy was blocked. The text is selected: copy it by hand."}
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={() => void copyIt()} className={secondaryButton}>
              Copy
            </button>
            <button type="button" onClick={onClose} className={primaryButton}>
              I have saved it
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * Reads the session. Children (which make the screen's own reads) mount only for a role that may see them; until then, and for a member
 * on an admin screen, the title stays and the body is the loading, error or forbidden state.
 */
export function WithSession({
  title,
  children,
  adminOnly = false,
}: {
  title: string;
  children: (s: Session) => ReactNode;
  adminOnly?: boolean;
}) {
  const read = useSession();
  if (read.status === "success" && (!adminOnly || read.data?.role === "org_admin")) return <>{children(read.data!)}</>;
  return (
    <Screen title={title}>
      <ReadView read={read} role="org admin" label={title}>
        {() => <Forbidden role="org admin" />}
      </ReadView>
    </Screen>
  );
}
