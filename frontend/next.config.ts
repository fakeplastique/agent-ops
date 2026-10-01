import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  // Rewrites are baked in at `next build`: S3 must pass API_ORIGIN as a build arg, not a runtime env.
  // Browser talks to same-origin paths; Next forwards them to the API (no CORS).
  rewrites: async () => [{ source: "/readyz", destination: `${API_ORIGIN}/readyz` }],
};

export default config;
