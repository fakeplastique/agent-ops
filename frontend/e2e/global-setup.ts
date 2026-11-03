import { execFileSync } from "node:child_process";

import { asUser, EMAILS, sessionCookie } from "./real";

// The journeys need a seeded, demo-mode backend. Seeding is idempotent (S35); the agents' tokens are shown once, on the
// run that created them, so each run rotates them and hands the fresh ones to the workers through the environment.
export default async function globalSetup() {
  execFileSync("uv", ["run", "--project", "../backend", "python", "../scripts/seed.py"], { stdio: "pipe", timeout: 120_000 });
  const cookie = await sessionCookie(EMAILS.fernwoodAdmin);
  const { items } = await asUser<{ items: { id: string; name: string }[] }>(cookie, "/agents?limit=100");
  const rotate = async (name: string) => {
    const agent = items.find((a) => a.name === name);
    if (!agent) throw new Error(`seeded agent ${name} is missing`);
    return (await asUser<{ token: string }>(cookie, `/agents/${agent.id}/rotate`, "POST")).token;
  };
  process.env.E2E_AGENTS = JSON.stringify({
    requester: await rotate("Refund Bot"),
    voters: [await rotate("Risk Reviewer"), await rotate("Policy Checker"), await rotate("Fraud Screen")],
  });
}
