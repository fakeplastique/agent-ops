"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DecisionsList } from "@/components/Decision/DecisionsList";

/** Filters, sort and page live in the URL, so a filtered list can be linked and survives paging and the back button. */
export function DecisionsScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  return <DecisionsList params={new URLSearchParams(params.toString())} onParams={(p) => router.replace(p.size ? `${path}?${p}` : path)} />;
}
