/**
 * A turn ORION answered in writing.
 *
 * The server sends `spoken: false` when it has no text-to-speech credential.
 * Before this phase that deployment could not use ORION at all: answering a
 * typed question insisted on speaking the answer, so the whole product sat
 * behind a speech vendor.
 */
import { describe, expect, it } from "vitest";

import { OrionApiError, parseTurn, type OrionTurn } from "@/lib/orion-api";
import { OrionAudioError, decodeTurnAudio } from "@/lib/orion-audio";

import { turnPayload } from "./support/audio-harness";

const unspokenPayload = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  ...turnPayload(),
  spoken: false,
  audio_base64: null,
  audio_format: null,
  audio_media_type: null,
  audio_bytes: null,
  ...overrides,
});

describe("parsing an unspoken turn", () => {
  it("keeps the transcript and the reply", () => {
    const turn = parseTurn(unspokenPayload());

    expect(turn.spoken).toBe(false);
    expect(turn.transcript).toBe("hello orion");
    expect(turn.responseText.length).toBeGreaterThan(0);
  });

  it("leaves every audio field null rather than empty", () => {
    const turn = parseTurn(unspokenPayload());

    expect(turn.audioBase64).toBeNull();
    expect(turn.audioFormat).toBeNull();
    expect(turn.audioMediaType).toBeNull();
    expect(turn.audioBytes).toBeNull();
  });

  it("still validates an analysis when one rode along", () => {
    // The capability this unlocks: an analysis is reachable without a
    // speech vendor, and it is validated exactly as strictly as before.
    expect(() => parseTurn(unspokenPayload({ analysis: { bogus: true } }))).toThrow(OrionApiError);
  });

  it.each(["audio_base64", "audio_format", "audio_media_type", "audio_bytes"])(
    "refuses a payload carrying %s alongside spoken:false",
    (field) => {
      const contradictory = unspokenPayload();
      contradictory[field] = field === "audio_bytes" ? 12 : "something";

      expect(() => parseTurn(contradictory)).toThrow(OrionApiError);
    },
  );

  it.each([
    ["a missing spoken flag", { spoken: undefined }],
    ["a non-boolean spoken flag", { spoken: "false" }],
  ])("refuses %s", (_label, overrides) => {
    expect(() => parseTurn({ ...unspokenPayload(), ...overrides })).toThrow(OrionApiError);
  });

  it("still refuses an empty reply — an unspoken turn must still say something", () => {
    expect(() => parseTurn(unspokenPayload({ response_text: "" }))).toThrow(OrionApiError);
  });
});

describe("a spoken turn is unchanged", () => {
  it("parses exactly as before", () => {
    const turn = parseTurn(turnPayload());

    expect(turn.spoken).toBe(true);
    expect(turn.audioBase64).not.toBeNull();
    expect(turn.audioBytes).toBeGreaterThan(0);
  });

  it("refuses a spoken turn with no audio", () => {
    expect(() => parseTurn({ ...turnPayload(), audio_base64: null })).toThrow(OrionApiError);
  });
});

describe("audio decoding", () => {
  it("refuses to decode an unspoken turn instead of inventing silence", () => {
    const turn: OrionTurn = parseTurn(unspokenPayload());

    expect(() => decodeTurnAudio(turn)).toThrow(OrionAudioError);
  });
});
