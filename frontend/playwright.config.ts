import { defineConfig } from "@playwright/test";

// Ports come from the gate (GATE_LANE gives each worktree its own); defaults match a plain run.
const API_PORT = process.env.API_PORT ?? "8000";
const WEB = `http://localhost:${process.env.WEB_PORT ?? "3100"}`;

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  globalSetup: "./e2e/global-setup.ts", // seeds the demo and rotates the agents' tokens for the real-backend journeys
  use: { baseURL: WEB },
  expect: { timeout: 10_000 }, // the first request after a cold start is slow
  projects: [
    { name: "narrow-375", use: { viewport: { width: 375, height: 812 } } },
    { name: "wide-1280", use: { viewport: { width: 1280, height: 800 } } },
  ],
  // reuseExistingServer lets gate.sh share one API with newman.
  webServer: [
    {
      command: `uv run --project ../backend uvicorn app.main:app --app-dir ../backend --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/healthz`,
      reuseExistingServer: true,
      // Demo sign-in is how the journeys become each seeded role; the Origin check needs the web origin it will see.
      // Every journey signs in from one IP and the agents write in bursts: the per-minute write limits (60, 300) would refuse them.
      env: { DEMO_MODE: "true", WEB_ORIGIN: WEB, WRITE_LIMIT_PER_PRINCIPAL: "100000", WRITE_LIMIT_PER_ORG: "100000" },
    },
    {
      command: `pnpm build && pnpm start -p ${process.env.WEB_PORT ?? "3100"}`,
      url: WEB,
      reuseExistingServer: false, // a stale web server would hide a broken build
      timeout: 180_000,
    },
  ],
});
