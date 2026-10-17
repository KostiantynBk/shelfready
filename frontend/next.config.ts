import type { NextConfig } from "next";

// The browser talks to the FastAPI backend through /backend/* on the same origin (no CORS, no
// backend URL in client code). API_URL is server-side only.
// On Vercel the API is the separate project "shelfready-api" (FastAPI, repo-root index.py).
const API_URL =
  process.env.API_URL ?? (process.env.VERCEL ? "https://shelfready-api.vercel.app" : "http://localhost:8000");

const nextConfig: NextConfig = {
  cacheComponents: true,
  devIndicators: false,
  partialPrefetching: true,
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
