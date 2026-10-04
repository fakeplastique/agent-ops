import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The browser only ever talks to its own origin (cookie, Origin check and OAuth redirect URI all live there), so
// every API path the web app uses has to be forwarded by Next. A path missing here is a 404 in production.
async function rewritesFor(apiOrigin: string) {
  vi.resetModules();
  vi.stubEnv("API_ORIGIN", apiOrigin);
  const { default: config } = await import("../next.config");
  const rules = await config.rewrites?.();
  return Array.isArray(rules) ? rules : [...(rules?.beforeFiles ?? []), ...(rules?.afterFiles ?? []), ...(rules?.fallback ?? [])];
}

describe("next.config rewrites", () => {
  beforeEach(() => vi.unstubAllEnvs());
  afterEach(() => vi.unstubAllEnvs());

  it("forwards the whole /api tree to the API origin, path preserved", async () => {
    const rules = await rewritesFor("http://api.test:8000");
    expect(rules).toContainEqual({ source: "/api/:path*", destination: "http://api.test:8000/api/:path*" });
  });

  it("keeps /readyz for the page's readiness probe", async () => {
    const rules = await rewritesFor("http://api.test:8000");
    expect(rules).toContainEqual({ source: "/readyz", destination: "http://api.test:8000/readyz" });
  });

  it("forwards nothing that is not the API (no open proxy)", async () => {
    const rules = await rewritesFor("http://api.test:8000");
    for (const r of rules) {
      expect(r.destination.startsWith("http://api.test:8000/")).toBe(true);
      expect(["/readyz", "/api/:path*"]).toContain(r.source);
    }
  });
});
