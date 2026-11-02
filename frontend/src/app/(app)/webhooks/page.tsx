import type { Metadata } from "next";

import { WebhooksView } from "./WebhooksView";

export const metadata: Metadata = { title: "Webhooks · Agent Ops" };

export default function WebhooksPage() {
  return <WebhooksView />;
}
