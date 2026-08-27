import type { NextConfig } from "next";

/**
 * Mission 10 Phase 1 is deliberately a frontend-only prototype: no rewrites,
 * no proxying, no image domains, no environment variables. Anything that would
 * reach a backend belongs to a later phase, so the only settings here are the
 * two that harden the default output.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
