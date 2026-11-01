"use client";

import { useEffect, useState } from "react";

import { Field, ActionError, inputClass, primaryButton, roleLabel, secondaryButton, useAction } from "@/components/admin/ui";
import { confirmDiscard } from "@/components/admin/useUnsavedGuard";
import { ApiError, api } from "@/lib/api/client";

export type Policy = {
  id: string;
  team_id: string | null;
  name: string;
  quorum_size: number;
  require_distinct_agents: boolean;
  rounds: number;
  confidence_threshold: number;
  levels: { role: string; timeout_minutes: number }[];
  voting_timeout_minutes: number;
  risk_floors: Record<string, string>;
  updated_at: string;
};
export type Plan = {
  plan: string;
  limits: { requests_per_month: number | null; max_quorum: number | null; max_rounds: number; max_levels: number };
  usage: { requests: { used: number; limit: number | null; period: string } };
  flags: string[];
};
export type TeamOption = { id: string; name: string };

type Draft = {
  name: string;
  quorum: string;
  distinct: boolean;
  rounds: string;
  confidence: string;
  voting: string;
  levels: { role: string; minutes: string }[];
  floors: { action: string; risk: string }[];
  teamId: string;
};

const ACTION_TYPE = /^[a-z][a-z0-9_.-]{0,63}$/;
const LEVEL_ROLES = ["team_lead", "manager", "org_admin"];
const RISKS = ["low", "medium", "high"];

const toDraft = (p?: Policy): Draft => ({
  name: p?.name ?? "default",
  quorum: String(p?.quorum_size ?? 3),
  distinct: p?.require_distinct_agents ?? true,
  rounds: String(p?.rounds ?? 1),
  confidence: String(p?.confidence_threshold ?? 0.8),
  voting: String(p?.voting_timeout_minutes ?? 60),
  levels: (p?.levels ?? [{ role: "manager", timeout_minutes: 60 }]).map((l) => ({ role: l.role, minutes: String(l.timeout_minutes) })),
  floors: Object.entries(p?.risk_floors ?? {}).map(([action, risk]) => ({ action, risk })),
  teamId: p?.team_id ?? "",
});

const whole = (s: string) => (/^\d+$/.test(s.trim()) ? Number(s) : NaN);
const planName = (p: string) => p.charAt(0).toUpperCase() + p.slice(1);

/** "Free allows up to 3 reviewers": the plan cap named in the user's words. */
export function limitText(plan: string, limit: string, max: number): string {
  const unit =
    limit === "quorum_size" ? (max === 1 ? "reviewer" : "reviewers") : limit === "rounds" ? (max === 1 ? "voting round" : "voting rounds") : max === 1 ? "approval level" : "approval levels";
  return `${planName(plan)} allows up to ${max} ${unit}`;
}

type Errors = {
  name?: string;
  quorum?: string;
  rounds?: string;
  confidence?: string;
  voting?: string;
  levels?: string;
  level: (string | undefined)[];
  floor: (string | undefined)[];
};

function validate(d: Draft, plan?: Plan): Errors {
  const e: Errors = { level: [], floor: [] };
  const lim = plan?.limits;
  const quorum = whole(d.quorum);
  if (!(quorum >= 1 && quorum <= 10_000)) e.quorum = "Enter a whole number from 1";
  else if (plan && lim?.max_quorum != null && quorum > lim.max_quorum) e.quorum = limitText(plan.plan, "quorum_size", lim.max_quorum);
  const rounds = whole(d.rounds);
  if (!(rounds === 1 || rounds === 2)) e.rounds = "Choose 1 or 2";
  else if (plan && lim && rounds > lim.max_rounds) e.rounds = limitText(plan.plan, "rounds", lim.max_rounds);
  const conf = Number(d.confidence);
  if (d.confidence.trim() === "" || !(conf >= 0 && conf <= 1)) e.confidence = "Enter a number from 0 to 1";
  const voting = whole(d.voting);
  if (!(voting >= 1 && voting <= 43_200)) e.voting = "Enter minutes from 1 to 43200";
  if (!d.name.trim()) e.name = "Give the policy a name";
  if (d.levels.length === 0) e.levels = "At least one approval level is needed";
  else if (plan && lim && d.levels.length > lim.max_levels) e.levels = limitText(plan.plan, "levels", lim.max_levels);
  d.levels.forEach((l, i) => {
    const m = whole(l.minutes);
    if (!(m >= 1 && m <= 43_200)) e.level[i] = "Enter minutes from 1 to 43200";
  });
  d.floors.forEach((f, i) => {
    if (!ACTION_TYPE.test(f.action)) e.floor[i] = "Use lowercase letters, digits and . _ - (for example refund.issue)";
    else if (d.floors.findIndex((o) => o.action === f.action) !== i) e.floor[i] = "This action type is listed twice";
  });
  return e;
}
const hasErrors = (e: Errors) =>
  Object.entries(e).some(([k, v]) => (Array.isArray(v) ? v.some(Boolean) : k !== "level" && k !== "floor" && Boolean(v)));

const toBody = (d: Draft) => ({
  name: d.name.trim(),
  quorum_size: Number(d.quorum),
  require_distinct_agents: d.distinct,
  rounds: Number(d.rounds),
  confidence_threshold: Number(d.confidence),
  voting_timeout_minutes: Number(d.voting),
  levels: d.levels.map((l) => ({ role: l.role, timeout_minutes: Number(l.minutes) })),
  risk_floors: Object.fromEntries(d.floors.map((f) => [f.action, f.risk])),
});

const small = "inline-flex h-control items-center rounded-control px-3 text-small font-medium text-muted hover:bg-raised hover:text-text max-lg:h-touch";

/**
 * One policy's form. A save sends only the changed fields and the updated_at it started from; the API answers 409
 * stale_version when the policy has moved on, and the current copy is then offered (load it, or keep editing on top of it).
 */
export function PolicyEditor({
  policy,
  plan,
  teams,
  teamScopes,
  onSaved,
  onClose,
  onDirty,
}: {
  policy?: Policy;
  plan?: Plan;
  teams: TeamOption[];
  teamScopes: boolean;
  onSaved: () => void;
  onClose: () => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [base, setBase] = useState(policy);
  const [initial, setInitial] = useState(() => JSON.stringify(toDraft(policy)));
  const [d, setD] = useState<Draft>(() => toDraft(policy));
  const [shown, setShown] = useState(false); // errors appear on the first save attempt, limits from the plan always
  const [conflict, setConflict] = useState<Policy | null>(null);
  const [seenError, setSeenError] = useState<unknown>(null); // the failure the user already edited past
  const act = useAction();

  const dirty = JSON.stringify(d) !== initial;
  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  const errors = validate(d, plan);
  // A limit the plan names is shown as soon as it is crossed; format errors wait for the first save.
  const planOnly = (k: "quorum" | "rounds" | "levels") => (errors[k] && /allows up to/.test(errors[k]!) ? errors[k] : undefined);
  const field = (k: "quorum" | "rounds" | "levels") => (shown ? errors[k] : planOnly(k)) ?? server[k];
  const set = (patch: Partial<Draft>) => {
    setD((x) => ({ ...x, ...patch }));
    setSeenError(act.error);
  };

  const save = async () => {
    setShown(true);
    if (hasErrors(errors) || (plan && (planOnly("quorum") || planOnly("rounds") || planOnly("levels")))) return;
    const r = await act.run(async () => {
      if (base) {
        // Only what this edit changed, against the version it started from: another admin's other fields stay theirs.
        const now = toBody(d);
        const was = toBody(JSON.parse(initial) as Draft);
        const changed = Object.fromEntries(Object.entries(now).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(was[k as keyof typeof was])));
        try {
          await api(`/escalation-policies/${base.id}`, { method: "PATCH", body: { ...changed, if_updated_at: base.updated_at } });
          return null;
        } catch (e) {
          if (e instanceof ApiError && e.code === "stale_version") return api<Policy>(`/escalation-policies/${base.id}`);
          throw e;
        }
      }
      await api("/escalation-policies", { method: "POST", body: { ...toBody(d), team_id: d.teamId || null } });
      return null;
    });
    if (r.ok && r.value) setConflict(r.value);
    else if (r.ok) onSaved();
  };

  // The API names the limit it refused; show it on that field like the plan's own validation would, until the next edit.
  const apiErr = act.error instanceof ApiError && act.error.code === "policy_above_plan" && act.error !== seenError ? act.error : null;
  const server: Partial<Record<"quorum" | "rounds" | "levels", string>> = {};
  if (apiErr) {
    const { limit, max, plan: p } = apiErr.details as { limit?: string; max?: number; plan?: string };
    if (typeof limit === "string" && typeof max === "number" && typeof p === "string")
      server[limit === "quorum_size" ? "quorum" : limit === "rounds" ? "rounds" : "levels"] = limitText(p, limit, max);
  }

  const reload = () => {
    if (!conflict) return;
    const next = toDraft(conflict);
    setBase(conflict);
    setInitial(JSON.stringify(next));
    setD(next);
    setConflict(null);
    setShown(false);
  };

  const close = () => {
    if (dirty && !confirmDiscard()) return;
    onClose();
  };

  const move = (i: number, by: -1 | 1) => {
    const levels = [...d.levels];
    [levels[i], levels[i + by]] = [levels[i + by], levels[i]];
    set({ levels });
  };

  return (
    <form
      aria-label={base ? `Edit policy ${base.name}` : "New policy"}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      className="flex max-w-2xl flex-col gap-5 rounded-card bg-raised p-4"
    >
      {!base && (
        <Field label="Applies to" hint={teamScopes ? undefined : "Per-team policies need the Enterprise plan."}>
          {(p) => (
            <select {...p} value={d.teamId} onChange={(e) => set({ teamId: e.target.value })} className={inputClass}>
              <option value="">Organization default</option>
              {teamScopes && teams.map((t) => <option key={t.id} value={t.id}>{`Team ${t.name}`}</option>)}
            </select>
          )}
        </Field>
      )}
      <Field label="Name" error={shown ? errors.name : undefined}>
        {(p) => <input {...p} value={d.name} maxLength={80} onChange={(e) => set({ name: e.target.value })} className={inputClass} />}
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Reviewers needed" hint="Agents that must vote before approvers are asked." error={field("quorum")}>
          {(p) => <input {...p} type="number" inputMode="numeric" min={1} value={d.quorum} onChange={(e) => set({ quorum: e.target.value })} className={inputClass} />}
        </Field>
        <Field label="Rounds" hint="Round 2 reveals votes first." error={field("rounds")}>
          {(p) => (
            <select {...p} value={d.rounds} onChange={(e) => set({ rounds: e.target.value })} className={inputClass}>
              <option value="1">1</option>
              <option value="2">2</option>
            </select>
          )}
        </Field>
        <Field label="Confidence threshold" hint="0 to 1: votes below it count as unsure." error={shown ? errors.confidence : undefined}>
          {(p) => <input {...p} type="number" step="0.05" min={0} max={1} value={d.confidence} onChange={(e) => set({ confidence: e.target.value })} className={inputClass} />}
        </Field>
        <Field label="Minutes to vote" error={shown ? errors.voting : undefined}>
          {(p) => <input {...p} type="number" min={1} value={d.voting} onChange={(e) => set({ voting: e.target.value })} className={inputClass} />}
        </Field>
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={d.distinct} onChange={(e) => set({ distinct: e.target.checked })} className="size-4 accent-accent" />
        Reviewers must be different agents
      </label>

      <fieldset aria-describedby="levels-note" className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 font-medium">Approval levels</legend>
        <p id="levels-note" className={`m-0 text-small ${field("levels") ? "text-negative" : "text-muted"}`}>
          {field("levels") ?? "Asked from the top; each level has its own time before it escalates to the next."}
        </p>
        {d.levels.map((l, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <Field label={`Level ${i + 1} role`}>
              {(p) => (
                <select {...p} value={l.role} onChange={(e) => set({ levels: d.levels.map((x, j) => (j === i ? { ...x, role: e.target.value } : x)) })} className={`${inputClass} w-40`}>
                  {LEVEL_ROLES.map((r) => (
                    <option key={r} value={r}>{roleLabel(r)}</option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={`Level ${i + 1} minutes`} error={shown ? errors.level[i] : undefined}>
              {(p) => <input {...p} type="number" min={1} value={l.minutes} onChange={(e) => set({ levels: d.levels.map((x, j) => (j === i ? { ...x, minutes: e.target.value } : x)) })} className={`${inputClass} w-28`} />}
            </Field>
            <div className="flex gap-1">
              <button type="button" aria-label={`Move level ${i + 1} up`} aria-disabled={i === 0} onClick={() => i > 0 && move(i, -1)} className={small}>Up</button>
              <button type="button" aria-label={`Move level ${i + 1} down`} aria-disabled={i === d.levels.length - 1} onClick={() => i < d.levels.length - 1 && move(i, 1)} className={small}>Down</button>
              <button type="button" aria-label={`Remove level ${i + 1}`} onClick={() => set({ levels: d.levels.filter((_, j) => j !== i) })} className={small}>Remove</button>
            </div>
          </div>
        ))}
        <div>
          <button type="button" className={secondaryButton} onClick={() => set({ levels: [...d.levels, { role: "org_admin", minutes: "60" }] })}>
            Add level
          </button>
        </div>
      </fieldset>

      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 font-medium">Risk floors</legend>
        <p className="m-0 text-small text-muted">An action type is never rated below its floor, whatever the agent says.</p>
        {d.floors.map((f, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <Field label={`Action type ${i + 1}`} error={shown ? errors.floor[i] : undefined}>
              {(p) => <input {...p} value={f.action} maxLength={64} onChange={(e) => set({ floors: d.floors.map((x, j) => (j === i ? { ...x, action: e.target.value } : x)) })} className={`${inputClass} w-56`} />}
            </Field>
            <Field label={`Minimum risk ${i + 1}`}>
              {(p) => (
                <select {...p} value={f.risk} onChange={(e) => set({ floors: d.floors.map((x, j) => (j === i ? { ...x, risk: e.target.value } : x)) })} className={`${inputClass} w-32`}>
                  {RISKS.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              )}
            </Field>
            <button type="button" aria-label={`Remove floor ${i + 1}`} onClick={() => set({ floors: d.floors.filter((_, j) => j !== i) })} className={small}>Remove</button>
          </div>
        ))}
        <div>
          <button type="button" className={secondaryButton} onClick={() => set({ floors: [...d.floors, { action: "", risk: "high" }] })}>
            Add floor
          </button>
        </div>
      </fieldset>

      {conflict && (
        <div role="alert" className="flex flex-col gap-2 rounded-card bg-surface px-4 py-3">
          <p className="m-0 font-medium">This policy changed since you opened it</p>
          <p className="m-0 text-muted">Someone saved it after you started. Your edits were not saved, so nothing was overwritten.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={primaryButton} onClick={reload}>Load the current version</button>
            <button type="button" className={secondaryButton} onClick={() => { setBase(conflict); setConflict(null); }}>Keep editing</button>
          </div>
        </div>
      )}
      {!(act.error instanceof ApiError && act.error.code === "policy_above_plan") && <ActionError error={act.error} />}
      <div className="flex gap-2">
        <button type="submit" aria-disabled={act.busy} className={primaryButton}>
          {act.busy ? "Saving…" : "Save policy"}
        </button>
        <button type="button" className={secondaryButton} onClick={close}>
          Cancel
        </button>
      </div>
    </form>
  );
}
