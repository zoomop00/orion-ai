import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FULL_DBFS, SILENCE_DBFS } from "@/lib/audio-envelope";
import { ANALYSER_FFT_SIZE, monitorAmplitude } from "@/lib/orion-analyser";

import { installMediaHarness, levelAt, type MediaHarness } from "./support/media-harness";

/**
 * The Web Audio boundary, with the graph mocked and the frame clock in the
 * test's hands.
 *
 * Two properties are load-bearing and are asserted directly rather than
 * inferred. First, the microphone chain never reaches the speakers at
 * anything but zero gain — connecting a live microphone to the output is an
 * instant feedback howl, and it is the kind of thing that works fine in
 * jsdom. Second, every failure mode leaves *playback* alone: if the analyser
 * cannot be built, ORION still speaks, just without a reactive core.
 */

let harness: MediaHarness;

const stream = () => ({ getTracks: () => [] }) as unknown as MediaStream;
const element = () => ({}) as unknown as HTMLAudioElement;

beforeEach(() => {
  harness = installMediaHarness();
});

afterEach(() => {
  harness.restore();
  vi.restoreAllMocks();
});

describe("building the graph", () => {
  it("uses a window long enough to be stable and short enough to be live", () => {
    expect(ANALYSER_FFT_SIZE).toBe(1024);
  });

  it("taps the microphone without ever routing it to the speakers", () => {
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});

    expect(monitor.active).toBe(true);
    const context = harness.contexts[0];
    expect(context).toBeDefined();

    // The only node with a gain is the sink, and its gain is exactly zero.
    const gains = (context?.nodes ?? []).filter(
      (node): node is typeof node & { gain: { value: number } } => "gain" in node,
    );
    expect(gains).toHaveLength(1);
    expect(gains[0]?.gain.value).toBe(0);

    monitor.stop();
  });

  it("forwards playback to the speakers, because rerouting it would mute ORION", () => {
    const monitor = monitorAmplitude({ kind: "playback", element: element() }, () => {});

    expect(monitor.active).toBe(true);
    const context = harness.contexts[0];
    // Source -> analyser -> destination: two connections, no gain node.
    expect((context?.nodes ?? []).every((node) => !("gain" in node))).toBe(true);
    expect((context?.nodes ?? []).reduce((total, node) => total + node.connectCount, 0)).toBe(2);

    monitor.stop();
  });

  it("resumes a context that a browser handed back suspended", () => {
    const monitor = monitorAmplitude({ kind: "playback", element: element() }, () => {});
    expect(harness.contexts[0]?.resumeCount).toBe(1);
    monitor.stop();
  });
});

describe("reporting the envelope", () => {
  it("reports nothing until a frame runs, then tracks the real level", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    expect(seen).toEqual([]);

    // The first frame establishes the clock; no time has passed, so the
    // envelope has not moved yet. That is correct, not a bug.
    harness.level = levelAt(FULL_DBFS);
    harness.tick(0);
    expect(seen).toEqual([0]);

    for (let frame = 1; frame <= 30; frame += 1) harness.tick(frame * 16);
    expect(seen.at(-1)).toBeGreaterThan(0.99);

    monitor.stop();
  });

  it("stays closed for a genuinely silent microphone", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    harness.level = 0;
    for (let frame = 0; frame <= 30; frame += 1) harness.tick(frame * 16);

    expect(seen.every((value) => value === 0)).toBe(true);
    monitor.stop();
  });

  it("stays closed for room noise below the silence floor", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    harness.level = levelAt(SILENCE_DBFS - 6);
    for (let frame = 0; frame <= 30; frame += 1) harness.tick(frame * 16);

    expect(seen.every((value) => value === 0)).toBe(true);
    monitor.stop();
  });

  it("opens and closes again as someone speaks and stops", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "playback", element: element() }, (value) =>
      seen.push(value),
    );

    let now = 0;
    const run = (frames: number) => {
      for (let frame = 0; frame < frames; frame += 1) {
        now += 16;
        harness.tick(now);
      }
    };

    harness.level = levelAt(-20);
    run(30);
    const speaking = seen.at(-1) ?? 0;
    expect(speaking).toBeGreaterThan(0.5);

    harness.level = 0;
    run(120);
    expect(seen.at(-1) ?? 1).toBeLessThan(0.01);

    monitor.stop();
  });

  it("keeps requesting frames while it runs, and stops requesting once stopped", () => {
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});
    expect(harness.pendingFrames()).toBe(1);

    harness.tick(16);
    expect(harness.pendingFrames()).toBe(1);

    monitor.stop();
    expect(harness.pendingFrames()).toBe(0);
  });
});

describe("releasing everything", () => {
  it("cancels the frame loop, disconnects every node, and closes the context", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    harness.level = levelAt(FULL_DBFS);
    for (let frame = 0; frame <= 20; frame += 1) harness.tick(frame * 16);
    expect(seen.at(-1)).toBeGreaterThan(0);

    monitor.stop();

    const context = harness.contexts[0];
    expect(context?.closed).toBe(true);
    expect((context?.nodes ?? []).every((node) => node.disconnectCount >= 1)).toBe(true);
    expect(harness.pendingFrames()).toBe(0);
    // A final zero, so the core settles instead of freezing mid-syllable.
    expect(seen.at(-1)).toBe(0);
  });

  it("is idempotent, so a React cleanup may run twice", () => {
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});
    monitor.stop();
    monitor.stop();
    monitor.stop();

    const context = harness.contexts[0];
    expect((context?.nodes ?? []).every((node) => node.disconnectCount === 1)).toBe(true);
    expect(harness.contexts).toHaveLength(1);
  });

  it("runs no further frames after being stopped", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    harness.tick(16);
    monitor.stop();
    const after = seen.length;

    harness.tick(32);
    harness.tick(48);
    expect(seen).toHaveLength(after);
  });
});

describe("when the browser cannot help", () => {
  const withoutWebAudio = (run: () => void) => {
    const scope = globalThis as unknown as Record<string, unknown>;
    const original = scope["AudioContext"];
    delete scope["AudioContext"];
    delete scope["webkitAudioContext"];
    try {
      run();
    } finally {
      scope["AudioContext"] = original;
    }
  };

  it("returns an inert monitor rather than throwing", () => {
    withoutWebAudio(() => {
      const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});
      expect(monitor.active).toBe(false);
      expect(harness.contexts).toHaveLength(0);
      expect(() => monitor.stop()).not.toThrow();
    });
  });

  it("gives up cleanly when the element is already routed through another graph", () => {
    harness.failElementSource = true;
    const seen: number[] = [];

    const monitor = monitorAmplitude({ kind: "playback", element: element() }, (value) =>
      seen.push(value),
    );

    expect(monitor.active).toBe(false);
    // Crucially the context is closed, so a failed analyser cannot leave a
    // half-built graph sitting on top of playback.
    expect(harness.contexts[0]?.closed).toBe(true);
    expect(harness.pendingFrames()).toBe(0);
    expect(seen).toEqual([0]);
  });

  it("gives up when the analyser cannot be read from at all", () => {
    harness.omitTimeDomainRead = true;

    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});

    expect(monitor.active).toBe(false);
    expect(harness.contexts[0]?.closed).toBe(true);
    expect(harness.pendingFrames()).toBe(0);
  });

  it("releases itself if the read starts failing part-way through", () => {
    const seen: number[] = [];
    const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, (value) =>
      seen.push(value),
    );

    harness.level = levelAt(FULL_DBFS);
    harness.tick(0);
    harness.tick(16);
    expect(monitor.active).toBe(true);

    harness.failTimeDomainRead = true;
    harness.tick(32);

    expect(harness.contexts[0]?.closed).toBe(true);
    expect(harness.pendingFrames()).toBe(0);
    expect(seen.at(-1)).toBe(0);
  });

  it("returns an inert monitor when there is no animation-frame clock", () => {
    const scope = globalThis as unknown as Record<string, unknown>;
    const original = scope["requestAnimationFrame"];
    scope["requestAnimationFrame"] = undefined;
    try {
      const monitor = monitorAmplitude({ kind: "microphone", stream: stream() }, () => {});
      expect(monitor.active).toBe(false);
      expect(harness.contexts).toHaveLength(0);
    } finally {
      scope["requestAnimationFrame"] = original;
    }
  });
});
