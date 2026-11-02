import type { Metadata } from "next";

import { PlanView } from "./PlanView";

export const metadata: Metadata = { title: "Plan and usage · Agent Ops" };

export default function PlanPage() {
  return <PlanView />;
}
