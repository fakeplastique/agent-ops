// Helpers for the journeys that run against the REAL backend in demo mode (S35): seeded users sign in through the login
// screen, seeded agents act over REST with tokens that global-setup rotated once per run.
export const WEB = `http://localhost:${process.env.WEB_PORT ?? "3100"}`;
export const API = `http://localhost:${process.env.API_PORT ?? "8000"}/api/v1`;

export const PEOPLE = {
  admin: "Ada Okafor",
  manager: "Marcus Lind",
  lead: "Tess Varga",
  member: "Mia Chen",
  birchAdmin: "Boris Held",
  birchManager: "Mina Ortiz",
} as const;
export const EMAILS = {
  acmeAdmin: "admin@acme.demo",
  birchAdmin: "admin@birch.demo",
} as const;

type Json = Record<string, unknown>;
export type Created = { id: string; version: number; title: string };
const origin = { Origin: WEB, "Content-Type": "application/json" };

async function send(path: string, init: { method?: string; headers: Record<string, string>; body?: unknown }) {
  const res = await fetch(`${API}${path}`, {
    method: init.method ?? "GET",
    headers: init.headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    redirect: "manual",
  });
  const text = await res.text();
  return { status: res.status, body: text ? (JSON.parse(text) as Json) : {}, res };
}

/** A cookie session for a seeded user, for setup that is not the journey itself (rotating tokens, reading another tenant's id). */
export async function sessionCookie(email: string) {
  const r = await send("/auth/demo/login", { method: "POST", headers: origin, body: { email } });
  if (r.status !== 200) throw new Error(`demo sign-in as ${email} answered ${r.status}: is the API running with DEMO_MODE=true?`);
  return (r.res.headers.getSetCookie()[0] ?? "").split(";")[0];
}

export async function asUser<T = Json>(cookie: string, path: string, method = "GET", body?: unknown) {
  const r = await send(path, { method, headers: { ...origin, Cookie: cookie }, body });
  if (r.status >= 300) throw new Error(`${method} ${path} answered ${r.status}: ${JSON.stringify(r.body)}`);
  return r.body as T;
}

export type AgentTokens = { requester: string; voters: string[] };
function tokens(): AgentTokens {
  const raw = process.env.E2E_AGENTS;
  if (!raw) throw new Error("E2E_AGENTS is not set: global-setup did not run");
  return JSON.parse(raw) as AgentTokens;
}

async function asAgent(token: string, path: string, method: string, body?: unknown) {
  const r = await send(path, { method, headers: { ...origin, Authorization: `Bearer ${token}` }, body });
  if (r.status >= 300) throw new Error(`${method} ${path} answered ${r.status}: ${JSON.stringify(r.body)}`);
  return r.body;
}

export type RequestSpec = { title: string; description?: string; payload?: Json; risk?: "low" | "medium" | "high" };

/** The seeded Acme agents: Refund Bot asks, three reviewers vote. */
export function agentsFor(opened: string[]) {
  const t = tokens();
  const create = async (s: RequestSpec): Promise<Created> => {
    const r = await asAgent(t.requester, "/decision-requests", "POST", {
      title: s.title,
      description: s.description ?? "",
      action_type: "refund",
      payload: s.payload ?? { order: s.title.length, amount_cents: 4250 },
      risk_level: s.risk ?? "medium",
    });
    opened.push(r.id as string);
    return { id: r.id as string, version: r.version as number, title: s.title };
  };
  const vote = (r: Created, reviewer: number, verdict: "approve" | "reject" = "approve") =>
    asAgent(t.voters[reviewer], `/decision-requests/${r.id}/votes`, "POST", {
      request_version: r.version,
      verdict,
      confidence: 0.9,
      rationale: `Reviewer ${reviewer + 1}: ${verdict} per policy`,
    });
  const voteAll = async (r: Created, from = 0, to = 3, verdict: "approve" | "reject" = "approve") => {
    for (let i = from; i < to; i++) await vote(r, i, verdict);
  };
  const revise = async (r: Created, description: string) => {
    const out = await asAgent(t.requester, `/decision-requests/${r.id}/revise`, "POST", { description });
    r.version = out.version as number;
  };
  return { create, vote, voteAll, revise, withdraw: (id: string) => asAgent(t.requester, `/decision-requests/${id}/withdraw`, "POST") };
}
