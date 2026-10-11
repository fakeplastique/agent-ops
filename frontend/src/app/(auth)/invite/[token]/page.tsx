import type { Metadata } from "next";

import { InviteView } from "./InviteView";

export const metadata: Metadata = { title: "Invitation · Agent Ops" };

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <InviteView token={token} />;
}
