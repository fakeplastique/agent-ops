"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Shell } from "@/components/shell/Shell";

// Minimal: the S36 Shell around every screen of the group except onboarding, which has its own frame (spec 8.1).
export default function AppLayout({ children }: { children: ReactNode }) {
  const path = usePathname() ?? "";
  if (path === "/onboarding" || path.startsWith("/onboarding/")) return children;
  return <Shell>{children}</Shell>;
}
