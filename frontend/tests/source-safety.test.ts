// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Committed guarantees about what the frontend source may and may not do.
 *
 * PHASE 3 NARROWED TWO RULES RATHER THAN DROPPING THEM. Phase 1 forbade
 * `fetch` and audio APIs outright, because a frontend-only prototype had no
 * business with either. Phase 3 genuinely needs both — one HTTP call and one
 * `<audio>` element — so the ban becomes a per-file allowlist: `fetch` may
 * appear only in `orion-api.ts`, media playback only in `orion-audio.ts`, and
 * nowhere else in the tree. That is stronger than deleting the rules, and it
 * mirrors the Python side's `_ALLOWED_EXTRA_IMPORTS_BY_FILE` exactly: a
 * component that starts calling the backend directly, or a hook that starts
 * constructing `Audio` objects, still fails the build.
 *
 * Everything Phase 1 banned that Phase 3 does not need — storage, cookies,
 * WebSockets, credentials, randomness, HTML injection sinks, microphone
 * access — remains banned everywhere with no exceptions. Phase 4's
 * `AudioContext`/`AnalyserNode` are banned too, so "no audio-reactive
 * visualization yet" is a property of the source rather than a promise.
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

const CODE = FILES.filter(({ path }) => path.endsWith(".ts") || path.endsWith(".tsx")).map(
  ({ path, text }) => ({ path, name: basename(path), text: stripComments(text) }),
);

/** The single file allowed to reach the network. */
const API_FILE = "orion-api.ts";
/** The single file allowed to touch media playback. */
const AUDIO_FILE = "orion-audio.ts";

const forbid = (pattern: RegExp, why: string, allowIn: readonly string[] = []): void => {
  for (const { path, name, text } of CODE) {
    if (allowIn.includes(name)) continue;
    expect(text, `${path}: ${why}`).not.toMatch(pattern);
  }
};

/** The inverse of `forbid`: prove the carve-out file really does use it. */
const requireIn = (fileName: string, pattern: RegExp, why: string): void => {
  const file = CODE.find(({ name }) => name === fileName);
  expect(file, `${fileName} is missing`).toBeDefined();
  expect(file?.text, `${fileName}: ${why}`).toMatch(pattern);
};

describe("frontend source safety", () => {
  it("finds source files to check at all", () => {
    expect(CODE.length).toBeGreaterThan(6);
  });

  it("never renders unsanitised HTML", () => {
    forbid(/dangerouslySetInnerHTML/, "raw HTML injection sink");
    forbid(/\.innerHTML\s*(=|\+=)/, "raw HTML injection sink");
    forbid(/\.outerHTML\s*(=|\+=)/, "raw HTML injection sink");
    forbid(/insertAdjacentHTML/, "raw HTML injection sink");
    forbid(/document\.write/, "raw HTML injection sink");
    forbid(/createContextualFragment/, "raw HTML injection sink");
  });

  it("reaches the network from exactly one file", () => {
    forbid(/\bfetch\s*\(/, "only the API client may call the backend", [API_FILE]);
    requireIn(API_FILE, /\bfetch\s*\(/, "the API client should be the file that calls fetch");
  });

  it("uses no transport other than that one fetch", () => {
    forbid(/XMLHttpRequest/, "the only transport is fetch");
    forbid(/\bWebSocket\b/, "streaming transports are deferred");
    forbid(/EventSource/, "streaming transports are deferred");
    forbid(/navigator\.sendBeacon/, "the frontend sends no analytics");
    forbid(/\bimport\s*\(\s*["'`]https?:/, "no remote code loading");
  });

  it("plays audio from exactly one file, and only through a media element", () => {
    forbid(/new\s+Audio\s*\(/, "only the audio boundary may create players", [AUDIO_FILE]);
    forbid(/createObjectURL/, "only the audio boundary may mint object URLs", [AUDIO_FILE]);
    forbid(/\.play\s*\(\s*\)/, "only the audio boundary may start playback", [AUDIO_FILE]);
    requireIn(AUDIO_FILE, /new\s+Audio\s*\(/, "the audio boundary should own the element");
    requireIn(AUDIO_FILE, /revokeObjectURL/, "every object URL must be revoked");
  });

  it("persists nothing on the user's device", () => {
    forbid(/localStorage/, "the frontend stores nothing");
    forbid(/sessionStorage/, "the frontend stores nothing");
    forbid(/indexedDB/i, "the frontend stores nothing");
    forbid(/document\.cookie/, "the frontend stores nothing");
    forbid(/\bcaches\b/, "the frontend caches nothing");
    forbid(/showSaveFilePicker|msSaveBlob/, "audio is never written to disk");
  });

  it("touches no credentials or environment configuration", () => {
    forbid(/process\.env/, "the frontend reads no environment");
    forbid(/api[_-]?key/i, "no credential material in the frontend");
    forbid(/deepgram/i, "no voice-provider access from the frontend");
    forbid(/anthropic/i, "no model-provider access from the frontend");
    forbid(/Authorization/i, "no auth headers in the frontend");
    forbid(/Bearer\s/i, "no bearer tokens in the frontend");
  });

  it("requests no device permissions, in this phase or by accident", () => {
    forbid(/getUserMedia/, "microphone capture belongs to Phase 4");
    forbid(/MediaRecorder/, "microphone capture belongs to Phase 4");
    forbid(/navigator\.permissions/, "no permission prompts in this phase");
  });

  it("performs no audio analysis, so nothing can claim to be audio-reactive", () => {
    forbid(/AudioContext/, "audio-reactive visualization belongs to Phase 4");
    forbid(/AnalyserNode/, "audio-reactive visualization belongs to Phase 4");
    forbid(/createMediaElementSource/, "audio-reactive visualization belongs to Phase 4");
    forbid(/getByteFrequencyData|getFloatTimeDomainData/, "no audio analysis in this phase");
  });

  it("uses no randomness, so nothing simulates data and hydration stays stable", () => {
    forbid(/Math\.random/, "would break hydration and would fake audio data");
    forbid(/crypto\.getRandomValues/, "would break hydration and would fake audio data");
  });

  it("evaluates no dynamic code", () => {
    forbid(/\beval\s*\(/, "dynamic code execution");
    forbid(/new\s+Function\s*\(/, "dynamic code execution");
    forbid(/setTimeout\s*\(\s*["'`]/, "string-bodied timers are eval in disguise");
  });

  it("writes nothing to the console, where transcripts would be readable", () => {
    forbid(/console\.(log|info|warn|error|debug|trace)\s*\(/, "no logging of turn content");
  });
});

describe("api boundary", () => {
  const api = CODE.find(({ name }) => name === API_FILE);

  it("targets the loopback backend and the documented text endpoint", () => {
    expect(api?.text).toMatch(/http:\/\/127\.0\.0\.1:8000/);
    expect(api?.text).toMatch(/\/v1\/voice\/turn\/text/);
  });

  it("sends JSON, omits credentials, and bounds the wait", () => {
    expect(api?.text).toMatch(/"Content-Type":\s*"application\/json"/);
    expect(api?.text).toMatch(/credentials:\s*"omit"/);
    expect(api?.text).toMatch(/AbortController/);
  });

  it("hardcodes no other origin", () => {
    const origins = api?.text.match(/https?:\/\/[^"'`\s)]+/g) ?? [];
    expect(origins).toEqual(["http://127.0.0.1:8000"]);
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

  it("keeps the new command surface fluid too", () => {
    const layout = stylesheets.find(({ path }) => path.endsWith("OrionInterface.module.css"));
    expect(layout?.text).toMatch(/\.commandRow\s*\{[^}]*max-width/);
    expect(layout?.text).toMatch(/\.commandInput\s*\{[^}]*min-width:\s*0/);
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
