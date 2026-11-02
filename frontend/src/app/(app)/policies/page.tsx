import type { Metadata } from "next";

import { PoliciesView } from "./PoliciesView";

export const metadata: Metadata = { title: "Policies · Agent Ops" };

export default function PoliciesPage() {
  return <PoliciesView />;
}
