import { defineConfig } from "@playwright/test";

const WEB = "http://localhost:3100";

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
      command: "uv run --project ../backend uvicorn app.main:app --app-dir ../backend --port 8000",
      url: "http://localhost:8000/healthz",
      reuseExistingServer: true,
    },
    {
      command: "pnpm build && pnpm start -p 3100",
      url: WEB,
      reuseExistingServer: true,
      timeout: 180_000,
    },
  ],
});
