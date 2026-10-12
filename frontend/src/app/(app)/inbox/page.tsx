import type { Metadata } from "next";

import { Inbox } from "@/components/Inbox/Inbox";

export const metadata: Metadata = { title: "Inbox · Agent Ops" };

export default function InboxPage() {
  return <Inbox />;
}
