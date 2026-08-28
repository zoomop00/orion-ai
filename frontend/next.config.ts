import type { NextConfig } from "next";

/**
 * Mission 10 Phase 1 is deliberately a frontend-only prototype: no rewrites,
 * no proxying, no image domains, no environment variables. Anything that would
 * reach a backend belongs to a later phase, so the only settings here are the
 * two that harden the default output.
 *
 * MISSION 11.1 ADDS ONE SETTING, AND IT REMOVES A SURFACE RATHER THAN ADDING
 * ONE. Next.js 16 ships its own MCP server for editor agents, and
 * `experimental.mcpServer` defaults to `true` (see `defaultConfig.experimental`
 * in `next/dist/server/config-shared.js`). With it on, `next dev` mounts an
 * unauthenticated JSON-RPC endpoint at `/_next/mcp` whose transport is
 * constructed without `enableDnsRebindingProtection`, `allowedHosts` or
 * `allowedOrigins` — so it validates neither `Origin` nor `Host` — and whose
 * tools disclose the absolute project path, the dev log file path, the route
 * table, compilation errors and server-action source filenames.
 *
 * It is development-only and cannot reach ORION's backend, credentials,
 * engines or risk system. It is switched off anyway, because ORION's connector
 * rule is an *explicit allowlist* (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §19,
 * and `src/orion/connectors/`), and an MCP server nobody in this project chose,
 * evaluated or allowlisted is precisely what that rule exists to prevent.
 * Turning it on again should be a deliberate edit with a Decision Log entry
 * behind it, not a framework default.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    mcpServer: false,
  },
};

export default nextConfig;
