import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_RECORDING_SECONDS,
  OrionMicrophoneError,
  RECORDING_MEDIA_TYPES,
  messageForMicrophoneFailure,
  openMicrophone,
} from "@/lib/orion-microphone";

import { allTracksReleased, installMediaHarness, type MediaHarness } from "./support/media-harness";

/**
 * The microphone boundary, with the browser mocked.
 *
 * The assertions that matter are about *release*: after every path this file
 * exercises — a normal turn, a refusal, a hardware failure, a recorder error,
 * a duration ceiling, a cancellation — every track handed out must be
 * stopped. A microphone left open is the one bug in this module a user would
 * discover by noticing their laptop's recording light, hours later.
 */

let harness: MediaHarness;

beforeEach(() => {
  harness = installMediaHarness();
});

afterEach(() => {
  harness.restore();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("asking for the microphone", () => {
  it("requests audio only, never video", async () => {
    const session = await openMicrophone();

    expect(harness.constraints).toHaveLength(1);
    const request = harness.constraints[0];
    expect(request?.video).toBe(false);
    expect(request?.audio).toBeTruthy();

    session.cancel();
  });

  it("picks a container the Phase 2 backend already accepts", async () => {
    const session = await openMicrophone();
    expect(RECORDING_MEDIA_TYPES).toContain(session.mediaType);
    session.cancel();
  });

  it("falls back down the container list when the browser is fussier", async () => {
    harness.supportedTypes = ["audio/mp4"];
    const session = await openMicrophone();
    expect(session.mediaType).toBe("audio/mp4");
    session.cancel();
  });

  it("refuses rather than guessing when no container is supported", async () => {
    harness.supportedTypes = [];
    await expect(openMicrophone()).rejects.toMatchObject({ failure: "unsupported" });
    // Nothing was claimed, so there is nothing to release.
    expect(harness.constraints).toHaveLength(0);
    expect(harness.tracks).toHaveLength(0);
  });

  it("reports an unsupported browser instead of throwing a TypeError", async () => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
    await expect(openMicrophone()).rejects.toMatchObject({ failure: "unsupported" });
  });
});

describe("permission and hardware failures", () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["NotAllowedError", "denied"],
    ["PermissionDeniedError", "denied"],
    ["SecurityError", "denied"],
    ["NotFoundError", "unavailable"],
    ["NotReadableError", "unavailable"],
    ["OverconstrainedError", "unavailable"],
    ["AbortError", "failed"],
    ["SomethingNobodyHasSeen", "failed"],
  ];

  it.each(cases)("maps %s onto %s", async (errorName, failure) => {
    harness.outcome = { errorName };
    await expect(openMicrophone()).rejects.toMatchObject({ failure });
  });

  it("says nothing about the device, the driver or the exception", async () => {
    harness.outcome = { errorName: "NotReadableError" };

    let caught: unknown;
    try {
      await openMicrophone();
    } catch (cause) {
      caught = cause;
    }

    expect(caught).toBeInstanceOf(OrionMicrophoneError);
    const failure = (caught as OrionMicrophoneError).failure;
    const message = messageForMicrophoneFailure(failure);
    expect(message).toMatch(/no microphone is available/i);
    expect(message).not.toMatch(/error|exception|device id|\/dev\/|hw:|\.dll/i);
  });

  it("has a calm sentence for every failure it can produce", () => {
    for (const failure of ["unsupported", "denied", "unavailable", "failed", "silent"] as const) {
      const message = messageForMicrophoneFailure(failure);
      expect(message.length).toBeGreaterThan(0);
      expect(message).not.toMatch(/undefined|null|\[object/i);
    }
  });
});

describe("recording a turn", () => {
  it("returns the captured audio labelled with our own media type", async () => {
    const session = await openMicrophone();
    const audio = await session.stop();

    expect(audio.size).toBe(harness.chunkBytes);
    // Never `recorder.mimeType`, which a browser may decorate with codecs.
    expect(audio.type).toBe(session.mediaType);
    expect(RECORDING_MEDIA_TYPES).toContain(audio.type);
  });

  it("releases every track once the recording is collected", async () => {
    const session = await openMicrophone();
    expect(allTracksReleased(harness)).toBe(false);

    await session.stop();

    expect(allTracksReleased(harness)).toBe(true);
    expect(harness.tracks.every((track) => track.stopCount === 1)).toBe(true);
  });

  it("treats a recording with nothing in it as silence, and still releases", async () => {
    harness.chunkBytes = 0;
    const session = await openMicrophone();

    await expect(session.stop()).rejects.toMatchObject({ failure: "silent" });
    expect(allTracksReleased(harness)).toBe(true);
  });

  it("surfaces a recorder error as a failure, and still releases", async () => {
    harness.failRecording = true;
    const session = await openMicrophone();

    await expect(session.stop()).rejects.toMatchObject({ failure: "failed" });
    expect(allTracksReleased(harness)).toBe(true);
  });

  it("releases the device when the recording is abandoned", async () => {
    const session = await openMicrophone();
    session.cancel();

    expect(allTracksReleased(harness)).toBe(true);
    expect(harness.recorders[0]?.state).toBe("inactive");
  });

  it("does not stop the same track twice when cancelled repeatedly", async () => {
    const session = await openMicrophone();
    session.cancel();
    session.cancel();
    session.cancel();

    expect(harness.tracks.every((track) => track.stopCount === 1)).toBe(true);
  });
});

describe("the duration ceiling", () => {
  it("matches Mission 9's MAX_RECORDING_SECONDS", () => {
    expect(MAX_RECORDING_SECONDS).toBe(30);
  });

  it("stops the recorder itself when the ceiling is reached", async () => {
    vi.useFakeTimers();
    const onLimitReached = vi.fn();
    const session = await openMicrophone({ onLimitReached });

    expect(harness.recorders[0]?.state).toBe("recording");

    vi.advanceTimersByTime(MAX_RECORDING_SECONDS * 1000);

    // The ceiling is enforced by the boundary, not by the caller remembering.
    expect(harness.recorders[0]?.state).toBe("inactive");
    expect(onLimitReached).toHaveBeenCalledTimes(1);

    vi.useRealTimers();
    const audio = await session.stop();
    expect(audio.size).toBe(harness.chunkBytes);
    expect(allTracksReleased(harness)).toBe(true);
  });

  it("never lets a caller ask for longer than the ceiling", async () => {
    vi.useFakeTimers();
    const onLimitReached = vi.fn();
    const session = await openMicrophone({ maxSeconds: 600, onLimitReached });

    vi.advanceTimersByTime(MAX_RECORDING_SECONDS * 1000);
    expect(onLimitReached).toHaveBeenCalledTimes(1);

    vi.useRealTimers();
    session.cancel();
  });

  it("honours a shorter ceiling when one is asked for", async () => {
    vi.useFakeTimers();
    const onLimitReached = vi.fn();
    const session = await openMicrophone({ maxSeconds: 2, onLimitReached });

    vi.advanceTimersByTime(1_900);
    expect(onLimitReached).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(onLimitReached).toHaveBeenCalledTimes(1);

    vi.useRealTimers();
    session.cancel();
  });

  it("does not fire the ceiling after a normal stop", async () => {
    vi.useFakeTimers();
    const onLimitReached = vi.fn();
    const session = await openMicrophone({ onLimitReached });

    const collected = session.stop();
    vi.advanceTimersByTime(MAX_RECORDING_SECONDS * 2 * 1000);
    vi.useRealTimers();

    await collected;
    expect(onLimitReached).not.toHaveBeenCalled();
  });
});
