import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  // Rewrites are baked in at `next build`: S3 must pass API_ORIGIN as a build arg, not a runtime env.
  // Browser talks to same-origin paths; Next forwards them to the API (no CORS).
  // The whole /api tree goes to the API unchanged: the login cookie, the Origin check and the OAuth redirect URI
  // all live on this origin, so the browser must never need the API's own host.
  rewrites: async () => [
    { source: "/readyz", destination: `${API_ORIGIN}/readyz` },
    { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
  ],
};

export default config;
