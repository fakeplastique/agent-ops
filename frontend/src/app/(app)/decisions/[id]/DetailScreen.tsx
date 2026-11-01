"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DecisionDetail } from "@/components/Decision/DecisionDetail";

/** The open tab is `?tab=`: a tab can be linked, and the default (overview) keeps the URL clean. */
export function DetailScreen({ id }: { id: string }) {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  return <DecisionDetail id={id} tab={params.get("tab") ?? "overview"} onTab={(t) => router.replace(t === "overview" ? path : `${path}?tab=${t}`)} />;
}
