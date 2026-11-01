"use client";

import { useEffect, useState } from "react";

import { ActionError, Confirm, fmtDateTime, primaryButton, secondaryButton, useAction } from "@/components/admin/ui";
import { useRead } from "@/components/admin/useRead";
import { PlanGated, ReadView } from "@/components/states/States";
import { ApiError, api } from "@/lib/api/client";

type LinkCode = { code: string; link: string | null; expires_at: string };

const FEATURE = "Telegram approvals";
const DESCRIPTION = "Get approval requests in Telegram and approve or reject from the chat.";

/** The code and, once its time is up, the way out: a new one. The timer is the only thing that flips it. */
function Code({ code, onNew, busy }: { code: LinkCode; onNew: () => void; busy: boolean }) {
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const wait = Math.min(Math.max(Date.parse(code.expires_at) - Date.now(), 0), 2 ** 31 - 1);
    const id = setTimeout(() => setExpired(true), Number.isNaN(wait) ? 0 : wait);
    return () => clearTimeout(id);
  }, [code.expires_at]);

  if (expired)
    return (
      <div className="flex flex-col items-start gap-2 rounded-card bg-raised p-4">
        <p className="m-0 font-medium">This code expired</p>
        <p className="m-0 text-muted">Codes work for a few minutes and once. Get a new one to continue.</p>
        <button type="button" aria-disabled={busy} className={primaryButton} onClick={() => !busy && onNew()}>
          Get a new code
        </button>
      </div>
    );
  return (
    <div className="flex flex-col items-start gap-2 rounded-card bg-raised p-4">
      <p className="m-0 text-muted">Open the bot in Telegram and send it this code. It works once and expires {fmtDateTime(code.expires_at)}.</p>
      <p className="m-0 font-mono text-section font-medium select-all">{code.code}</p>
      {code.link && (
        <a href={code.link} target="_blank" rel="noopener noreferrer" className={`${secondaryButton}`}>
          Open Telegram
        </a>
      )}
    </div>
  );
}

export function TelegramBlock() {
  const state = useRead<{ linked: boolean }>("/me/telegram");
  const [code, setCode] = useState<LinkCode | null>(null);
  const act = useAction();

  const get = async () => {
    const r = await act.run(() => api<LinkCode>("/me/telegram/link-code", { method: "POST" }));
    if (r.ok) setCode(r.value);
  };
  const unlink = async () => {
    const r = await act.run(() => api("/me/telegram", { method: "DELETE" }));
    if (r.ok) {
      setCode(null);
      state.refetch();
    }
  };

  // The flag was read as on but the API says otherwise (plan changed): the same gate the Free screen shows.
  if (act.error instanceof ApiError && act.error.status === 402) {
    const need = act.error.details.required_plan;
    return <PlanGated feature={FEATURE} description={DESCRIPTION} plan={typeof need === "string" ? need : "pro"} />;
  }

  return (
    <ReadView read={state} label="Telegram">
      {(s) =>
        s.linked ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="m-0 font-medium">Telegram is linked</p>
            <Confirm label="Unlink" question="Stop approving from Telegram?" confirmLabel="Unlink Telegram" busy={act.busy} onConfirm={unlink} />
            <ActionError error={act.error} />
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3">
            <p className="m-0 text-muted">{DESCRIPTION}</p>
            {code ? (
              <>
                <Code key={code.code} code={code} onNew={get} busy={act.busy} />
                <button type="button" className={secondaryButton} onClick={() => state.refetch()}>
                  Check again
                </button>
              </>
            ) : (
              <button type="button" aria-disabled={act.busy} className={primaryButton} onClick={() => !act.busy && void get()}>
                Link Telegram
              </button>
            )}
            <ActionError error={act.error} />
          </div>
        )
      }
    </ReadView>
  );
}

export { DESCRIPTION as TELEGRAM_DESCRIPTION, FEATURE as TELEGRAM_FEATURE };
