// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Committed guarantees about what the frontend source may and may not do.
 *
 * EACH PHASE NARROWS RULES RATHER THAN DROPPING THEM. Phase 1 forbade
 * `fetch` and audio APIs outright, because a frontend-only prototype had no
 * business with either. Phase 3 genuinely needed both — one HTTP call and one
 * `<audio>` element — so the ban became a per-file allowlist: `fetch` may
 * appear only in `orion-api.ts`, media playback only in `orion-audio.ts`.
 * Phase 4 does the same for the two capabilities it adds: `getUserMedia` and
 * `MediaRecorder` may appear only in `orion-microphone.ts`, and
 * `AudioContext`/`AnalyserNode` only in `orion-analyser.ts`. Four capabilities,
 * four files, each with an inverse assertion proving the carve-out is used.
 * That is stronger than deleting the rules, and it mirrors the Python side's
 * `_ALLOWED_EXTRA_IMPORTS_BY_FILE` exactly: a component that starts calling
 * the backend directly, a hook that starts constructing `Audio` objects, or a
 * stylesheet-adjacent helper that quietly opens a microphone, still fails the
 * build.
 *
 * Everything Phase 1 banned that no later phase needs — storage, cookies,
 * WebSockets, credentials, randomness, HTML injection sinks, permission
 * probing — remains banned everywhere with no exceptions. Phase 4 adds bans
 * of its own: no camera, no frequency-domain analysis (an RMS envelope is
 * the whole signal-processing budget, see `audio-envelope.ts`), and no path
 * by which a recording could be given a URL, written to disk or replayed.
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
/** The single file allowed to claim a capture device. */
const MIC_FILE = "orion-microphone.ts";
/** The single file allowed to build a Web Audio graph. */
const ANALYSER_FILE = "orion-analyser.ts";

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

  it("claims a capture device from exactly one file", () => {
    forbid(/getUserMedia/, "only the microphone boundary may claim a device", [MIC_FILE]);
    forbid(/MediaRecorder/, "only the microphone boundary may record", [MIC_FILE]);
    requireIn(MIC_FILE, /getUserMedia/, "the microphone boundary should own the request");
    requireIn(MIC_FILE, /MediaRecorder/, "the microphone boundary should own the recorder");
  });

  it("never probes permission state behind the user's back", () => {
    forbid(/navigator\.permissions/, "permission is asked for by gesture, never queried");
  });

  it("asks for audio only, never a camera", () => {
    forbid(/video:\s*true/, "this interface has no use for a camera");
    forbid(/getDisplayMedia/, "no screen capture");
    forbid(/enumerateDevices/, "no device enumeration, which is a fingerprinting surface");
    requireIn(MIC_FILE, /video:\s*false/, "the capture request should refuse video explicitly");
  });

  it("releases the microphone and bounds the recording, in the boundary itself", () => {
    requireIn(MIC_FILE, /getTracks\(\)/, "every track must be reachable to be stopped");
    requireIn(MIC_FILE, /track\.stop\(\)/, "tracks must actually be stopped");
    requireIn(
      MIC_FILE,
      /MAX_RECORDING_SECONDS = 30/,
      "the ceiling must match Mission 9's MAX_RECORDING_SECONDS",
    );
    requireIn(MIC_FILE, /clearTimeout\(limitTimer\)/, "the ceiling timer must be cleared");
  });

  it("analyses audio from exactly one file, and releases what it builds", () => {
    forbid(/AudioContext/, "only the analyser may build a Web Audio graph", [ANALYSER_FILE]);
    forbid(/createAnalyser/, "only the analyser may build a Web Audio graph", [ANALYSER_FILE]);
    forbid(/createMediaElementSource/, "only the analyser may reroute playback", [ANALYSER_FILE]);
    forbid(/createMediaStreamSource/, "only the analyser may tap a stream", [ANALYSER_FILE]);
    requireIn(ANALYSER_FILE, /createAnalyser/, "the analyser should own the node");
    requireIn(ANALYSER_FILE, /context\.close\(\)/, "the context must be closed");
    requireIn(ANALYSER_FILE, /node\.disconnect\(\)/, "every node must be disconnected");
    requireIn(ANALYSER_FILE, /cancelAnimationFrame/, "the frame loop must be cancellable");
  });

  it("keeps the analysis to a time-domain envelope, not an FFT", () => {
    forbid(/getFloatTimeDomainData|getByteTimeDomainData/, "time-domain reads belong to the analyser", [
      ANALYSER_FILE,
    ]);
    forbid(/getByteFrequencyData/, "frequency analysis is deliberately not done");
    forbid(/getFloatFrequencyData/, "frequency analysis is deliberately not done");
    forbid(/frequencyBinCount/, "frequency analysis is deliberately not done");
    forbid(/createScriptProcessor|audioWorklet/i, "no custom DSP nodes");
  });

  it("never turns a recording into something that outlives the turn", () => {
    // `createObjectURL` is already confined to the playback boundary, which
    // is what stops a captured recording from being given a URL at all.
    forbid(/createObjectURL/, "a recording must never be given a URL", [AUDIO_FILE]);
    forbid(/FileReader/, "a recording is never read into anything that can be kept");
    forbid(/\.srcObject/, "the user's own microphone is never attached to a media element");
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
