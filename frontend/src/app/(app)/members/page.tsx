import type { Metadata } from "next";

import { MembersView } from "./MembersView";

export const metadata: Metadata = { title: "Members · Agent Ops" };

export default function MembersPage() {
  return <MembersView />;
}
