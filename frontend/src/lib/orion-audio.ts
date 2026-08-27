/**
 * The browser's audio boundary: base64 from ORION becomes sound, or it
 * becomes a refusal.
 *
 * TREATED AS UNTRUSTED. Everything here validates the response even though
 * it came from our own backend. "It's our server" is precisely the
 * assumption that turns a compromised, proxied, cached or simply buggy
 * response into an arbitrary blob handed to the platform. Format, media
 * type, base64 alphabet, decoded length against the server's own stated
 * length, and the RIFF/WAVE header are all checked before a single byte
 * reaches an `<audio>` element.
 *
 * NO SERVER STRING EVER BECOMES A URL OR A MIME TYPE. `audio_media_type`
 * from the response is *compared* against an allowlist and then discarded;
 * the `Blob` is constructed with a constant declared in this file, and the
 * only URL ever used is the `blob:` URL the platform mints from that Blob.
 * There is no path by which a server-controlled string is concatenated into
 * a URL or passed to `new Blob({ type })`.
 *
 * NATIVE `HTMLAudioElement`, NOT WEB AUDIO. Phase 3 needs to play a short
 * WAV and know when it stops. An `<audio>` element does exactly that with
 * no `AudioContext`, no user-gesture resume dance, and no decode step that
 * would double the memory held. `AudioContext`/`AnalyserNode` belong to
 * Phase 4's audio-reactive visualization and are deliberately absent — a
 * source-safety test fails the build if either appears.
 *
 * THE PHASE 4 SEAM. `onStart` receives the live `HTMLAudioElement`. Phase 3
 * ignores the argument; Phase 4 can pass it to
 * `AudioContext.createMediaElementSource` and drive a real `AnalyserNode`
 * from it without changing one line of this module's signature or of its
 * callers. Nothing here fabricates amplitude in the meantime.
 *
 * NOTHING IS STORED. The bytes exist as one `Uint8Array` and one `Blob` for
 * the length of one playback. The object URL is revoked in a `finally`, on
 * every path including failure and cancellation. No file, no
 * `localStorage`, no cache, no upload.
 */

import { MAX_AUDIO_RESPONSE_BYTES, type OrionTurn } from "./orion-api";

/**
 * Formats this build can play, and the media types each may legitimately be
 * labelled with. Mission 9 pins Deepgram to `linear16`/`wav`/24 kHz and
 * Phase 2 labels it `audio/wav`; the two synonyms are accepted because they
 * are the same container under other common names, not because anything
 * guesses.
 */
const ACCEPTED_MEDIA_TYPES_BY_FORMAT: Readonly<Record<string, readonly string[]>> = {
  wav: ["audio/wav", "audio/x-wav", "audio/wave"],
};

/** What the Blob is actually built with — ours, never the server's. */
const CANONICAL_MEDIA_TYPE_BY_FORMAT: Readonly<Record<string, string>> = {
  wav: "audio/wav",
};

const BASE64_ALPHABET = /^[A-Za-z0-9+/]*={0,2}$/;

/** RIFF container magic: "RIFF" at 0, "WAVE" at 8. */
const RIFF = [0x52, 0x49, 0x46, 0x46];
const WAVE = [0x57, 0x41, 0x56, 0x45];

export type OrionAudioFailure =
  | "unsupported"
  | "malformed"
  | "blocked"
  | "failed"
  | "cancelled";

const FAILURE_MESSAGES: Readonly<Record<OrionAudioFailure, string>> = {
  unsupported: "ORION returned audio this browser cannot play.",
  malformed: "ORION returned something unexpected.",
  blocked: "Your browser blocked playback. Try again.",
  failed: "ORION's reply could not be played.",
  cancelled: "That request was cancelled.",
};

export class OrionAudioError extends Error {
  readonly failure: OrionAudioFailure;

  constructor(failure: OrionAudioFailure) {
    // No message parameter, for the same reason `OrionApiError` has none:
    // there must be no way to attach a caught exception's text to an error
    // the interface will render.
    super(failure);
    this.name = "OrionAudioError";
    this.failure = failure;
  }
}

export function messageForAudioFailure(failure: OrionAudioFailure): string {
  return FAILURE_MESSAGES[failure];
}

const startsWith = (
  bytes: Uint8Array<ArrayBuffer>,
  offset: number,
  magic: readonly number[],
): boolean =>
  magic.every((byte, index) => bytes[offset + index] === byte);

/**
 * Validate and decode a turn's audio, or throw `OrionAudioError`.
 *
 * Exported separately from playback so the checks can be exercised — and
 * reasoned about — without a media element in the room.
 */
export function decodeTurnAudio(turn: OrionTurn): Uint8Array<ArrayBuffer> {
  const accepted = ACCEPTED_MEDIA_TYPES_BY_FORMAT[turn.audioFormat];
  if (accepted === undefined) throw new OrionAudioError("unsupported");
  if (!accepted.includes(turn.audioMediaType)) throw new OrionAudioError("unsupported");

  if (turn.audioBase64.length === 0) throw new OrionAudioError("malformed");
  if (turn.audioBase64.length % 4 !== 0) throw new OrionAudioError("malformed");
  if (!BASE64_ALPHABET.test(turn.audioBase64)) throw new OrionAudioError("malformed");
  if (turn.audioBytes <= 0 || turn.audioBytes > MAX_AUDIO_RESPONSE_BYTES) {
    throw new OrionAudioError("malformed");
  }

  let binary: string;
  try {
    binary = atob(turn.audioBase64);
  } catch {
    throw new OrionAudioError("malformed");
  }

  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  if (bytes.length === 0) throw new OrionAudioError("malformed");
  // The server states the decoded length; a disagreement means the two
  // fields do not describe the same audio, whatever the reason.
  if (bytes.length !== turn.audioBytes) throw new OrionAudioError("malformed");
  if (bytes.length < 12) throw new OrionAudioError("malformed");
  if (!startsWith(bytes, 0, RIFF) || !startsWith(bytes, 8, WAVE)) {
    throw new OrionAudioError("malformed");
  }

  return bytes;
}

export interface PlayOptions {
  /**
   * Fired when playback has actually begun — never before. The live element
   * is passed so Phase 4 can attach analysis; Phase 3 ignores it.
   */
  readonly onStart?: ((element: HTMLAudioElement) => void) | undefined;
  /**
   * Stops playback and rejects with `cancelled`. This exists so a component
   * unmounting does not leave ORION talking to an empty room; it is not
   * user-facing interruption or barge-in, which are deferred.
   */
  readonly signal?: AbortSignal | undefined;
}

/**
 * Play a turn's audio to completion.
 *
 * Resolves when the audio genuinely finishes (the element's `ended` event).
 * Rejects with `OrionAudioError` on validation failure, an autoplay block,
 * a decode/playback error, or cancellation. The object URL is revoked on
 * every one of those paths.
 */
export async function playTurnAudio(
  turn: OrionTurn,
  { onStart, signal }: PlayOptions = {},
): Promise<void> {
  const bytes = decodeTurnAudio(turn);
  const mediaType = CANONICAL_MEDIA_TYPE_BY_FORMAT[turn.audioFormat];
  if (mediaType === undefined) throw new OrionAudioError("unsupported");
  if (signal?.aborted) throw new OrionAudioError("cancelled");

  const blob = new Blob([bytes], { type: mediaType });
  const url = URL.createObjectURL(blob);
  const audio = new Audio();

  try {
    await new Promise<void>((resolve, reject) => {
      const teardown: Array<() => void> = [];
      let settled = false;

      const finish = (error?: OrionAudioError) => {
        if (settled) return;
        settled = true;
        for (const undo of teardown) undo();
        audio.pause();
        if (error) reject(error);
        else resolve();
      };

      const listen = (type: string, handler: () => void) => {
        audio.addEventListener(type, handler);
        teardown.push(() => audio.removeEventListener(type, handler));
      };

      listen("ended", () => finish());
      listen("error", () => finish(new OrionAudioError("failed")));

      if (signal) {
        const onAbort = () => finish(new OrionAudioError("cancelled"));
        signal.addEventListener("abort", onAbort, { once: true });
        teardown.push(() => signal.removeEventListener("abort", onAbort));
      }

      audio.preload = "auto";
      audio.src = url;

      // `play()` returns a Promise in every browser that matters; wrapping
      // keeps the older void-returning behaviour from crashing here.
      Promise.resolve(audio.play()).then(
        () => {
          if (settled) return;
          // Playback has started. This -- not the arrival of the HTTP
          // response -- is the moment the interface may claim SPEAKING.
          onStart?.(audio);
        },
        (cause: unknown) => {
          const blocked =
            typeof cause === "object" && cause !== null && "name" in cause
              ? (cause as { name?: unknown }).name === "NotAllowedError"
              : false;
          finish(new OrionAudioError(blocked ? "blocked" : "failed"));
        },
      );
    });
  } finally {
    URL.revokeObjectURL(url);
    // Drop the element's own reference to the (now dead) URL so the decoded
    // audio is collectable immediately rather than at the next GC of `audio`.
    audio.removeAttribute("src");
    audio.load();
  }
}
