"use client";

import { Screen, Section, WithSession, roleLabel } from "@/components/admin/ui";
import { useDemoSwitch } from "@/components/admin/useDemoSwitch";
import { type Page, LIST, useRead } from "@/components/admin/useRead";
import { PlanGated, ReadView } from "@/components/states/States";

import type { Plan } from "../plan/PlanView";
import { TELEGRAM_DESCRIPTION, TELEGRAM_FEATURE, TelegramBlock } from "./TelegramBlock";
import { TokensBlock } from "./TokensBlock";

type Member = { user_id: string; email: string; full_name: string; role: string };

export function SettingsView() {
  return <WithSession title="Settings">{(s) => <Settings me={s.user_id} role={s.role} isAdmin={s.role === "org_admin"} />}</WithSession>;
}

function Settings({ me, role, isAdmin }: { me: string; role: string | null; isAdmin: boolean }) {
  const members = useRead<Page<Member>>(`/members?${LIST}`);
  const plan = useRead<Plan>("/organizations/current/plan");
  const demo = useDemoSwitch(isAdmin, () => plan.refetch());

  return (
    <Screen title="Settings" lead="Your profile, Telegram and the tokens that act as you.">
      <div className="flex max-w-2xl flex-col gap-8">
        <Section title="Profile">
          <ReadView read={members} label="Profile">
            {(d) => {
              const mine = d.items.find((m) => m.user_id === me);
              return (
                <dl className="m-0 border-t border-rule">
                  {(
                    [
                      ["Name", mine?.full_name],
                      ["Email", mine?.email],
                      ["Role", role ? roleLabel(role) : undefined], // from the session: always the role in use
                    ] as const
                  ).map(([k, v]) => (
                    <div key={k} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule py-3">
                      <dt className="text-muted">{k}</dt>
                      <dd className="m-0 min-w-0 truncate font-medium" title={v}>
                        {v || "—"}
                      </dd>
                    </div>
                  ))}
                </dl>
              );
            }}
          </ReadView>
        </Section>

        <Section title="Telegram">
          <ReadView read={plan} label="Telegram">
            {(p) =>
              p.flags.includes("telegram") ? (
                <TelegramBlock />
              ) : (
                <PlanGated feature={TELEGRAM_FEATURE} description={TELEGRAM_DESCRIPTION} plan="pro" onSwitchPlan={demo.switchPlan} />
              )
            }
          </ReadView>
        </Section>

        <Section title="Personal access tokens">
          <TokensBlock />
        </Section>
      </div>
    </Screen>
  );
}
