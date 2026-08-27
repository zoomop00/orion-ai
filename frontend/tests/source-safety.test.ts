// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Mission 10 Phase 1 is frontend-only by contract: no network, no persistence,
 * no credentials, no raw HTML injection. Those are easy promises to make in a
 * report and easy to break in a later commit, so they are asserted against the
 * source tree instead of trusted.
 *
 * This mirrors the import-hygiene tests the Python side already uses
 * (the `test_import_hygiene.py` files): a committed guarantee rather than an
 * observation that today's code happens to be clean.
 */

const SRC = fileURLToPath(new URL("../src", import.meta.url));

function collect(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? collect(full) : [full];
  });
}

const FILES = collect(SRC).map((path) => ({ path, text: readFileSync(path, "utf8") }));

/**
 * Comments are stripped before scanning. The source deliberately *discusses*
 * some of the things forbidden below (why there is no `Math.random()`, what
 * Phase 4 will connect); the rule is about what the code does, not what it
 * explains.
 */
const stripComments = (text: string): string =>
  text.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:])\/\/.*$/gm, "$1");

const CODE = FILES.filter(
  ({ path }) => path.endsWith(".ts") || path.endsWith(".tsx"),
).map(({ path, text }) => ({ path, text: stripComments(text) }));

const forbid = (pattern: RegExp, why: string): void => {
  for (const { path, text } of CODE) {
    expect(text, `${path}: ${why}`).not.toMatch(pattern);
  }
};

describe("frontend source safety", () => {
  it("finds source files to check at all", () => {
    expect(CODE.length).toBeGreaterThan(4);
  });

  it("never renders unsanitised HTML", () => {
    forbid(/dangerouslySetInnerHTML/, "raw HTML injection sink");
    forbid(/\.innerHTML\s*=/, "raw HTML injection sink");
    forbid(/\.outerHTML\s*=/, "raw HTML injection sink");
    forbid(/insertAdjacentHTML/, "raw HTML injection sink");
  });

  it("performs no network calls", () => {
    forbid(/\bfetch\s*\(/, "Phase 1 makes no network calls");
    forbid(/XMLHttpRequest/, "Phase 1 makes no network calls");
    forbid(/\bWebSocket\b/, "Phase 1 makes no network calls");
    forbid(/EventSource/, "Phase 1 makes no network calls");
    forbid(/navigator\.sendBeacon/, "Phase 1 sends no analytics");
  });

  it("persists nothing on the user's device", () => {
    forbid(/localStorage/, "Phase 1 stores nothing");
    forbid(/sessionStorage/, "Phase 1 stores nothing");
    forbid(/indexedDB/i, "Phase 1 stores nothing");
    forbid(/document\.cookie/, "Phase 1 stores nothing");
  });

  it("touches no credentials or environment configuration", () => {
    forbid(/process\.env/, "Phase 1 reads no environment");
    forbid(/api[_-]?key/i, "no credential material in the frontend");
    forbid(/deepgram/i, "no voice-provider access from the frontend");
    forbid(/anthropic/i, "no model-provider access from the frontend");
    forbid(/Authorization/i, "no auth headers in the frontend");
  });

  it("requests no device permissions yet", () => {
    // Microphone access belongs to a later phase, behind an explicit prompt.
    forbid(/getUserMedia/, "no microphone access in Phase 1");
    forbid(/AudioContext/, "no audio capture or analysis in Phase 1");
    forbid(/MediaRecorder/, "no microphone access in Phase 1");
  });

  it("uses no randomness, so server and client render identically", () => {
    forbid(/Math\.random/, "would break hydration and would fake audio data");
    forbid(/crypto\.getRandomValues/, "would break hydration and would fake audio data");
  });

  it("evaluates no dynamic code", () => {
    forbid(/\beval\s*\(/, "dynamic code execution");
    forbid(/new\s+Function\s*\(/, "dynamic code execution");
  });
});

describe("responsive structure", () => {
  const stylesheets = FILES.filter(({ path }) => path.endsWith(".css"));

  it("ships stylesheets alongside the components", () => {
    expect(stylesheets.length).toBeGreaterThanOrEqual(3);
  });

  it("sizes everything fluidly instead of at fixed breakpoint widths", () => {
    const layout = stylesheets.find(({ path }) => path.endsWith("OrionInterface.module.css"));
    const core = stylesheets.find(({ path }) => path.endsWith("OrionCore.module.css"));

    expect(layout?.text).toMatch(/clamp\(/);
    expect(core?.text).toMatch(/clamp\(/);
    // vmin keeps the core proportional to the *smaller* viewport axis, which is
    // what stops it overflowing a phone in landscape.
    expect(core?.text).toMatch(/vmin/);
    expect(layout?.text).toMatch(/100dvh/);
    expect(layout?.text).toMatch(/env\(safe-area-inset/);
    expect(layout?.text).toMatch(/overflow:\s*hidden/);
  });

  it("respects reduced-motion preferences in both stylesheets that animate", () => {
    for (const sheet of stylesheets) {
      if (!/@keyframes/.test(sheet.text)) continue;
      expect(sheet.text, `${sheet.path} animates without a reduced-motion fallback`).toMatch(
        /prefers-reduced-motion/,
      );
    }
  });
});
