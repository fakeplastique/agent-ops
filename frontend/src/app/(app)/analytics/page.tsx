import type { Metadata } from "next";

import { AnalyticsView } from "./AnalyticsView";

export const metadata: Metadata = { title: "Analytics · Agent Ops" };

export default function AnalyticsPage() {
  return <AnalyticsView />;
}
