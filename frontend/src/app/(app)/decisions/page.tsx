import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/states/States";

import { DecisionsScreen } from "./DecisionsScreen";

export const metadata: Metadata = { title: "Decisions · Agent Ops" };

export default function DecisionsPage() {
  // useSearchParams needs a boundary: the list reads its filters from the URL on the client.
  return (
    <Suspense fallback={<Loading />}>
      <DecisionsScreen />
    </Suspense>
  );
}
