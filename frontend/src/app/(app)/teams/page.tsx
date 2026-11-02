import type { Metadata } from "next";

import { TeamsView } from "./TeamsView";

export const metadata: Metadata = { title: "Teams · Agent Ops" };

export default function TeamsPage() {
  return <TeamsView />;
}
