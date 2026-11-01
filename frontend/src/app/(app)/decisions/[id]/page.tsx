import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/states/States";

import { DetailScreen } from "./DetailScreen";

export const metadata: Metadata = { title: "Decision · Agent Ops" };

export default async function DecisionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense fallback={<Loading />}>
      <DetailScreen id={id} />
    </Suspense>
  );
}
