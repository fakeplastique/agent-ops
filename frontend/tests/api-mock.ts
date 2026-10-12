import { vi } from "vitest";

type Reply = { status?: number; body?: unknown };
type Handler = object | ((req: { body: any; method: string; path: string }) => Reply | Promise<Reply>);

export const err = (status: number, code: string, message: string, details: Record<string, unknown> = {}): Reply => ({
  status,
  body: { error: { code, message, details } },
});
export const page = <T>(items: T[], total = items.length) => ({ items, total, skip: 0, limit: 100, nextLink: null });

/** fetch stand-in keyed by "METHOD /path?query" (the path after /api/v1). Unmatched calls answer 500 and are listed in `unmatched`. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { key: string; body: any }[] = [];
  const unmatched: string[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = String(url).replace(/^\/api\/v1/, "");
    const key = `${method} ${path}`;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ key, body });
    const h = routes[key];
    if (!h) {
      unmatched.push(key);
      return new Response(JSON.stringify({ error: { code: "unmocked", message: key, details: {} } }), { status: 500 });
    }
    const r = typeof h === "function" ? await h({ body, method, path }) : h;
    const reply: Reply = "status" in r || "body" in r ? (r as Reply) : { body: r };
    if (reply.status === 204 || (reply.body === undefined && !reply.status)) return new Response(null, { status: reply.status ?? 204 });
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls, unmatched, count: (key: string) => calls.filter((c) => c.key === key).length };
}

export const session = (role = "org_admin") => ({ body: { user_id: "u-me", organization_id: "org-1", role } });
