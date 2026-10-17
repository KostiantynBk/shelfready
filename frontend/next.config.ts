import type { NextConfig } from "next";

// The browser talks to the FastAPI backend through /backend/* on the same origin (no CORS, no
// backend URL in client code). API_URL is server-side only: http://localhost:8000 in development.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  devIndicators: false,
  partialPrefetching: true,
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
