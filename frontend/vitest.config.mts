import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * The smallest test setup that can actually assert on rendered React output:
 * Vitest + jsdom + Testing Library. No Playwright, no Jest, no snapshot
 * infrastructure -- Phase 1 only needs to prove the four visual states are
 * representable and render distinguishably.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
