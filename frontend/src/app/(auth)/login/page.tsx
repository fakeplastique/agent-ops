import type { Metadata } from "next";

import { LoginView } from "./LoginView";

export const metadata: Metadata = { title: "Sign in · Agent Ops" };

type Params = Promise<Record<string, string | string[] | undefined>>;
const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : null);

export default async function LoginPage({ searchParams }: { searchParams: Params }) {
  const q = await searchParams;
  return <LoginView error={one(q.error)} next={one(q.next)} />;
}
