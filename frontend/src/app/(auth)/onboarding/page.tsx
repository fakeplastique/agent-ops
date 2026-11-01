import type { Metadata } from "next";

import { Onboarding } from "./Onboarding";

export const metadata: Metadata = { title: "Set up · Agent Ops" };

export default function OnboardingPage() {
  return <Onboarding />;
}
