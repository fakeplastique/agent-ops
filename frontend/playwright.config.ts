import { defineConfig } from "@playwright/test";

// Ports come from the gate (GATE_LANE gives each worktree its own); defaults match a plain run.
const API_PORT = process.env.API_PORT ?? "8000";
const WEB = `http://localhost:${process.env.WEB_PORT ?? "3100"}`;

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  use: { baseURL: WEB },
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
    },
    {
      command: `pnpm build && pnpm start -p ${process.env.WEB_PORT ?? "3100"}`,
      url: WEB,
      reuseExistingServer: false, // a stale web server would hide a broken build
      timeout: 180_000,
    },
  ],
});
