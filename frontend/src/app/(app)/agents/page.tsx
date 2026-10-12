import type { Metadata } from "next";

import { AgentsView } from "./AgentsView";

export const metadata: Metadata = { title: "Agents · Agent Ops" };

export default function AgentsPage() {
  return <AgentsView />;
}
