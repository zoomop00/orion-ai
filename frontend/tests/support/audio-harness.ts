/**
 * Deterministic stand-ins for the two browser capabilities jsdom does not
 * implement: object URLs and media playback.
 *
 * Nothing here fakes ORION. The bytes are a genuine RIFF/WAVE file built
 * with a real header, the base64 is produced by the platform's own `btoa`,
 * and the harness only decides *when* playback starts and ends — which is
 * exactly what a real browser decides and what the tests need to control in
 * order to assert that SPEAKING tracks actual playback rather than a timer.
 */

import type { OrionTurn } from "@/lib/orion-api";

/** A real, minimal 16-bit mono 24 kHz WAV — same shape Mission 9 produces. */
export function wavBytes(dataBytes = 128): Uint8Array<ArrayBuffer> {
  const total = 44 + dataBytes;
  const bytes = new Uint8Array(total);
  const view = new DataView(bytes.buffer);
  const ascii = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      bytes[offset + index] = text.charCodeAt(index);
    }
  };

  ascii(0, "RIFF");
  view.setUint32(4, total - 8, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24_000, true);
  view.setUint32(28, 48_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);
  for (let index = 0; index < dataBytes; index += 1) bytes[44 + index] = index % 256;

  return bytes;
}

export function toBase64(bytes: Uint8Array<ArrayBuffer>): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export const WAV = wavBytes();
export const WAV_BASE64 = toBase64(WAV);

/** The exact JSON shape Phase 2 returns. */
export function turnPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    transcript: "hello orion",
    response_text: "Hello. I'm ORION.",
    audio_base64: WAV_BASE64,
    audio_format: "wav",
    audio_media_type: "audio/wav",
    audio_bytes: WAV.length,
    state: "idle",
    ...overrides,
  };
}

export function turnFixture(overrides: Partial<OrionTurn> = {}): OrionTurn {
  return {
    transcript: "hello orion",
    responseText: "Hello. I'm ORION.",
    audioBase64: WAV_BASE64,
    audioFormat: "wav",
    audioMediaType: "audio/wav",
    audioBytes: WAV.length,
    state: "idle",
    // Phase 5.3: an ordinary turn carries no analysis, which is what every
    // fixture here represents. A turn that does have one is built by
    // `tests/orion-analysis.test.ts`, where the shape is the subject.
    analysis: null,
    ...overrides,
  };
}

export type PlayMode = "resolve" | "manual" | "block" | "fail";

export interface AudioHarness {
  /** Object URLs minted, in order. */
  readonly minted: string[];
  /** Object URLs revoked, in order. */
  readonly revoked: string[];
  /** Every element `play()` was called on. */
  readonly played: HTMLAudioElement[];
  mode: PlayMode;
  /** Resolve a `manual` play, i.e. "the browser started making sound now". */
  startPlayback(): void;
  /** Fire `ended` on the most recent element. */
  endPlayback(): void;
  /** Fire `error` on the most recent element. */
  failPlayback(): void;
  restore(): void;
}

export function installAudioHarness(): AudioHarness {
  const minted: string[] = [];
  const revoked: string[] = [];
  const played: HTMLAudioElement[] = [];
  let pendingStart: (() => void) | null = null;
  let counter = 0;

  const media = HTMLMediaElement.prototype as unknown as Record<string, unknown>;
  const originalPlay = media["play"];
  const originalPause = media["pause"];
  const originalLoad = media["load"];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  const harness: AudioHarness = {
    minted,
    revoked,
    played,
    mode: "resolve",
    startPlayback() {
      const resolve = pendingStart;
      pendingStart = null;
      resolve?.();
    },
    endPlayback() {
      played.at(-1)?.dispatchEvent(new Event("ended"));
    },
    failPlayback() {
      played.at(-1)?.dispatchEvent(new Event("error"));
    },
    restore() {
      media["play"] = originalPlay;
      media["pause"] = originalPause;
      media["load"] = originalLoad;
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    },
  };

  media["play"] = function playStub(this: HTMLAudioElement): Promise<void> {
    played.push(this);
    if (harness.mode === "block") {
      const error = new Error("autoplay blocked");
      error.name = "NotAllowedError";
      return Promise.reject(error);
    }
    if (harness.mode === "fail") {
      const error = new Error("decode failed");
      error.name = "NotSupportedError";
      return Promise.reject(error);
    }
    if (harness.mode === "manual") {
      return new Promise<void>((resolve) => {
        pendingStart = resolve;
      });
    }
    return Promise.resolve();
  };
  media["pause"] = function pauseStub(): void {};
  media["load"] = function loadStub(): void {};

  URL.createObjectURL = ((blob: Blob) => {
    counter += 1;
    const url = `blob:orion-test/${counter}/${blob.type}/${blob.size}`;
    minted.push(url);
    return url;
  }) as typeof URL.createObjectURL;

  URL.revokeObjectURL = ((url: string) => {
    revoked.push(url);
  }) as typeof URL.revokeObjectURL;

  return harness;
}
