"use client";

import { useAction } from "@/components/admin/ui";
import { useRead } from "@/components/admin/useRead";
import { api } from "@/lib/api/client";

/**
 * Demo-only plan switching. The seeded-users route exists only when DEMO_MODE is on (404 otherwise), so a successful
 * probe is the "this is a demo" signal; until it answers, and on any failure, there is no switch. Only an org admin gets one.
 */
export function useDemoSwitch(isAdmin: boolean, onChanged: () => void) {
  const probe = useRead<unknown>(isAdmin ? "/auth/demo/users" : null);
  const act = useAction();
  const switchPlan =
    isAdmin && probe.status === "success"
      ? async (plan: string) => {
          const r = await act.run(() => api("/organizations/current/plan", { method: "PUT", body: { plan } }));
          if (r.ok) onChanged();
        }
      : undefined;
  return { switchPlan, error: act.error, busy: act.busy };
}
