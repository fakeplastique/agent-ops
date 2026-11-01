import type { Metadata } from "next";

import { SettingsView } from "./SettingsView";

export const metadata: Metadata = { title: "Settings · Agent Ops" };

export default function SettingsPage() {
  return <SettingsView />;
}
