import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { MAX_AUDIO_RESPONSE_BYTES } from "@/lib/orion-api";
import {
  OrionAudioError,
  decodeTurnAudio,
  messageForAudioFailure,
  playTurnAudio,
  type OrionAudioFailure,
} from "@/lib/orion-audio";

import {
  WAV,
  installAudioHarness,
  toBase64,
  turnFixture,
  wavBytes,
  type AudioHarness,
} from "./support/audio-harness";

let harness: AudioHarness;

beforeEach(() => {
  harness = installAudioHarness();
});

afterEach(() => {
  harness.restore();
});

describe("decodeTurnAudio — the response is not trusted", () => {
  it("accepts the WAV Phase 2 actually produces", () => {
    const bytes = decodeTurnAudio(turnFixture());

    expect(bytes).toEqual(WAV);
    expect(bytes.length).toBe(WAV.length);
  });

  it.each(["audio/wav", "audio/x-wav", "audio/wave"])("accepts the %s label", (mediaType) => {
    expect(() => decodeTurnAudio(turnFixture({ audioMediaType: mediaType }))).not.toThrow();
  });

  it.each(["mp3", "ogg", "flac", "webm", "", "WAV", "wav "])(
    "refuses the unsupported format %o",
    (format) => {
      expect(() => decodeTurnAudio(turnFixture({ audioFormat: format }))).toThrow(OrionAudioError);
    },
  );

  it.each([
    "audio/mpeg",
    "audio/ogg",
    "text/html",
    "application/javascript",
    "image/svg+xml",
    "",
    "audio/wav; codecs=1",
  ])("refuses the media type %o for a wav payload", (mediaType) => {
    const thrown = decodeTurnAudio.bind(null, turnFixture({ audioMediaType: mediaType }));
    expect(thrown).toThrow(OrionAudioError);
    try {
      thrown();
    } catch (cause) {
      expect((cause as OrionAudioError).failure).toBe("unsupported");
    }
  });

  it.each([
    ["invalid characters", "!!!!"],
    ["a length that is not a multiple of four", "AAAAA"],
    ["whitespace", "AAAA AAAA"],
    ["a data URL prefix", "data:audio/wav;base64,AAAA"],
    ["an empty string", ""],
  ])("refuses base64 with %s", (_label, base64) => {
    expect(() => decodeTurnAudio(turnFixture({ audioBase64: base64, audioBytes: 4 }))).toThrow(
      OrionAudioError,
    );
  });

  it("refuses audio whose decoded length disagrees with the stated length", () => {
    expect(() => decodeTurnAudio(turnFixture({ audioBytes: WAV.length + 4 }))).toThrow(
      OrionAudioError,
    );
  });

  it("refuses bytes that are not a RIFF/WAVE container", () => {
    const notWav = new Uint8Array(64).fill(0x41);
    expect(() =>
      decodeTurnAudio(
        turnFixture({ audioBase64: toBase64(notWav), audioBytes: notWav.length }),
      ),
    ).toThrow(OrionAudioError);
  });

  it("refuses a truncated container", () => {
    const stub = WAV.slice(0, 6);
    expect(() =>
      decodeTurnAudio(turnFixture({ audioBase64: toBase64(stub), audioBytes: stub.length })),
    ).toThrow(OrionAudioError);
  });

  it("refuses audio beyond the browser memory ceiling", () => {
    expect(() =>
      decodeTurnAudio(turnFixture({ audioBytes: MAX_AUDIO_RESPONSE_BYTES + 1 })),
    ).toThrow(OrionAudioError);
  });

  it("has a calm sentence for every audio failure", () => {
    const failures: OrionAudioFailure[] = [
      "unsupported",
      "malformed",
      "blocked",
      "failed",
      "cancelled",
    ];
    for (const failure of failures) {
      const message = messageForAudioFailure(failure);
      expect(message).toMatch(/\.$/);
      expect(message).not.toMatch(/error|exception|traceback|NotAllowed|\d{3}/i);
    }
  });
});

describe("playTurnAudio — real playback lifecycle", () => {
  it("builds the Blob with our own media type, never the server's string", async () => {
    const pending = playTurnAudio(turnFixture({ audioMediaType: "audio/x-wav" }));
    await Promise.resolve();
    harness.endPlayback();
    await pending;

    expect(harness.minted).toHaveLength(1);
    // The harness encodes the Blob's type into the fake URL.
    expect(harness.minted[0]).toContain("audio/wav");
    expect(harness.minted[0]).not.toContain("x-wav");
  });

  it("reports playback start only once the browser has begun playing", async () => {
    harness.mode = "manual";
    const started: HTMLAudioElement[] = [];

    const pending = playTurnAudio(turnFixture(), {
      onStart: (element) => started.push(element),
    });
    await Promise.resolve();

    expect(harness.played).toHaveLength(1);
    expect(started).toHaveLength(0);

    harness.startPlayback();
    await Promise.resolve();
    await Promise.resolve();
    expect(started).toHaveLength(1);
    // The live element is handed over -- the seam Phase 4 attaches analysis to.
    expect(started[0]).toBeInstanceOf(HTMLAudioElement);

    harness.endPlayback();
    await pending;
  });

  it("resolves when the audio genuinely ends", async () => {
    let ended = false;
    const pending = playTurnAudio(turnFixture()).then(() => {
      ended = true;
    });
    await Promise.resolve();

    expect(ended).toBe(false);
    harness.endPlayback();
    await pending;
    expect(ended).toBe(true);
  });

  it("rejects with `blocked` when autoplay policy refuses", async () => {
    harness.mode = "block";

    await expect(playTurnAudio(turnFixture())).rejects.toMatchObject({ failure: "blocked" });
  });

  it("rejects with `failed` on any other play rejection", async () => {
    harness.mode = "fail";

    await expect(playTurnAudio(turnFixture())).rejects.toMatchObject({ failure: "failed" });
  });

  it("rejects with `failed` when the element reports a media error", async () => {
    const pending = playTurnAudio(turnFixture());
    await Promise.resolve();
    harness.failPlayback();

    await expect(pending).rejects.toMatchObject({ failure: "failed" });
  });

  it("rejects with `cancelled` when the signal aborts mid-playback", async () => {
    const controller = new AbortController();
    const pending = playTurnAudio(turnFixture(), { signal: controller.signal });
    await Promise.resolve();
    controller.abort();

    await expect(pending).rejects.toMatchObject({ failure: "cancelled" });
  });

  it("refuses before minting anything when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      playTurnAudio(turnFixture(), { signal: controller.signal }),
    ).rejects.toMatchObject({ failure: "cancelled" });
    expect(harness.minted).toHaveLength(0);
  });

  it.each([
    ["success", async (h: AudioHarness) => h.endPlayback()],
    ["a media error", async (h: AudioHarness) => h.failPlayback()],
  ])("revokes the object URL after %s", async (_label, act) => {
    const pending = playTurnAudio(turnFixture()).catch(() => undefined);
    await Promise.resolve();
    await act(harness);
    await pending;

    expect(harness.minted).toHaveLength(1);
    expect(harness.revoked).toEqual(harness.minted);
  });

  it("revokes the object URL when playback is blocked", async () => {
    harness.mode = "block";

    await playTurnAudio(turnFixture()).catch(() => undefined);

    expect(harness.revoked).toEqual(harness.minted);
    expect(harness.revoked).toHaveLength(1);
  });

  it("mints no object URL at all when validation fails", async () => {
    await playTurnAudio(turnFixture({ audioFormat: "mp3" })).catch(() => undefined);

    expect(harness.minted).toHaveLength(0);
    expect(harness.revoked).toHaveLength(0);
  });

  it("leaves no src behind on the element it used", async () => {
    const pending = playTurnAudio(turnFixture());
    await Promise.resolve();
    harness.endPlayback();
    await pending;

    expect(harness.played[0]?.getAttribute("src")).toBeNull();
  });

  it("plays a larger, still-valid WAV without complaint", async () => {
    const big = wavBytes(64_000);
    const pending = playTurnAudio(
      turnFixture({ audioBase64: toBase64(big), audioBytes: big.length }),
    );
    await Promise.resolve();
    harness.endPlayback();

    await expect(pending).resolves.toBeUndefined();
  });
});
