import type { Metadata } from "next";

import { Landing } from "@/components/landing/Landing";
import { issueReceipt } from "@/components/landing/receipt";

export const metadata: Metadata = {
  title: "Agent Ops: agents ask, humans sign off",
  description: "Agents vote blind, people sign off level by level, and every approval ships a receipt that breaks if the request is edited.",
};

// Signed at build time with the public demo key, so the receipt reads correctly without scripts.
export default async function Page() {
  return <Landing receipt={await issueReceipt()} />;
}
