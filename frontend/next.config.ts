import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  // Browser talks to same-origin paths; Next forwards them to the API (no CORS).
  rewrites: async () => [{ source: "/readyz", destination: `${API_ORIGIN}/readyz` }],
};

export default config;
