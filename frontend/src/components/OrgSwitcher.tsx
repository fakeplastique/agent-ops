"use client";

import { useId, useState } from "react";

import { switchOrganization, useIdentitySync } from "@/lib/session";

export type OrgOption = { id: string; name: string };

/**
 * Sidebar organization switcher. The Shell renders it twice (sidebar and menu sheet): each copy owns its state and ids.
 * A switch ends in a full page load, so no row, form or cache from the previous organization survives.
 */
export function OrgSwitcher({ organizations, currentId }: { organizations: OrgOption[]; currentId: string | null }) {
  const id = useId();
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  useIdentitySync();

  const current = organizations.find((o) => o.id === currentId);
  if (organizations.length < 2)
    return current ? (
      <p title={current.name} className="m-0 truncate px-1 font-medium">
        {current.name}
      </p>
    ) : null;

  // Not `disabled` while busy: disabling the focused control would drop keyboard focus if the switch fails.
  const change = async (next: string) => {
    if (state === "busy") return;
    setState("busy");
    try {
      await switchOrganization(next); // navigates away on success; stays busy until then
    } catch {
      setState("failed");
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="sr-only">
        Organization
      </label>
      {/* Controlled by the server's answer: the value moves only when the page reloads into the new organization. */}
      <select
        id={id}
        value={currentId ?? ""}
        aria-busy={state === "busy"}
        title={current?.name}
        aria-describedby={state === "failed" ? `${id}-error` : undefined}
        onChange={(e) => void change(e.target.value)}
        className="h-control w-full truncate rounded-control border border-field bg-surface px-2 font-medium text-text aria-busy:opacity-60 max-lg:h-touch coarse:h-touch"
      >
        {organizations.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
      {state === "busy" && <p className="m-0 px-1 text-small text-muted">Switching…</p>}
      {state === "failed" && (
        <p id={`${id}-error`} role="alert" className="m-0 px-1 text-small text-negative">
          Could not switch. You are still in {current?.name ?? "this organization"}.
        </p>
      )}
    </div>
  );
}
