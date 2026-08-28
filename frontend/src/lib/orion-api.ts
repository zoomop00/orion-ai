/**
 * The one place the browser talks to ORION.
 *
 * Every field, status code and failure mode here was read off the Phase 2
 * implementation (`src/orion/interface/schemas.py`, `service.py`, `app.py`),
 * not assumed. The server returns exactly seven fields; this module accepts
 * exactly those seven and rejects anything else.
 *
 * WHY NATIVE `fetch`, AND WHY NOTHING ELSE. One POST to one endpoint with a
 * JSON body needs no client library. `axios` would add a dependency, a
 * bundle, and an interceptor system for a call that has no interceptors.
 * Timeout and cancellation — the two things people usually reach for a
 * library to get — are `AbortController`, which is native.
 *
 * WHY THE BASE URL IS A CONSTANT AND NOT AN ENVIRONMENT VARIABLE. Phase 2's
 * `run()` hardcodes `127.0.0.1` and defaults to port 8000, and it exposes no
 * host parameter. There is therefore nothing to configure: a
 * `NEXT_PUBLIC_*` variable would be a new, permanently client-visible
 * configuration surface bought with no flexibility that is actually
 * reachable. It would also force `process.env` to be permitted in the
 * frontend, weakening a source-safety rule that currently holds absolutely.
 * `127.0.0.1` rather than `localhost` is deliberate too: on IPv6-first
 * systems `localhost` can resolve to `::1`, which the backend — bound to the
 * IPv4 loopback — is not listening on.
 *
 * WHY SERVER ERROR TEXT IS NEVER SHOWN. Phase 2's `detail` strings happen to
 * be calm and credential-free by construction. This module still does not
 * render them: it classifies the *status code* into one of a handful of
 * failures and shows a sentence written here. That way the interface cannot
 * be made to display foreign text by anything sitting between it and ORION —
 * a proxy error page, a captive portal, a future gateway — and the frontend
 * owns its own voice.
 *
 * NOTHING IS LOGGED. No `console` call anywhere in this file: a transcript
 * is untrusted user content and the audio is ORION's voice, and neither
 * belongs in a browser console that screenshots, extensions and screen
 * recordings can read.
 */

/** Phase 2 binds `127.0.0.1` and defaults to port 8000. See the module docstring. */
export const ORION_API_BASE = "http://127.0.0.1:8000";

export const TEXT_TURN_PATH = "/v1/voice/turn/text";

/**
 * Phase 4's spoken turn. Raw recorded bytes, not multipart — matching the
 * Phase 2 route exactly (`orion.interface.app.voice_turn`), which reads the
 * body as bytes and takes the container from the `Content-Type` header.
 */
export const AUDIO_TURN_PATH = "/v1/voice/turn";

/**
 * The only `Content-Type` values this client will ever put on a request.
 *
 * A closed allowlist rather than "whatever the `Blob` says": `MediaRecorder`
 * chooses its own codec-decorated label, and a header value assembled from a
 * platform-authored string is exactly the kind of thing that is fine until
 * one browser puts something surprising in it. Every entry is already in
 * `orion.interface.schemas.ACCEPTED_AUDIO_CONTENT_TYPES`.
 */
export const ACCEPTED_RECORDING_MEDIA_TYPES: readonly string[] = [
  "audio/webm",
  "audio/ogg",
  "audio/mp4",
];

/**
 * Mirrors `orion.interface.schemas.MAX_REQUEST_BODY_BYTES` (which is itself
 * Mission 9's `MAX_AUDIO_INPUT_BYTES`). Checked here so an over-long
 * recording is refused before it is put on the wire rather than after the
 * server has read 25 MB of it.
 */
export const MAX_AUDIO_REQUEST_BYTES = 25 * 1024 * 1024;

/**
 * How long the browser waits for a turn before giving up.
 *
 * Raised from 45s for Phase 5.2, because a turn can now be an *analysis*: a
 * Deepgram transcription, a market-data fetch, a model call and a
 * text-to-speech round trip, each behind its own server-side timeout (30s,
 * 10s, 60s, 30s respectively). Those per-vendor timeouts were deliberately
 * not raised — a hung vendor call must not hold a worker three times as
 * long — so the only party that had to become more patient is this one.
 * 90 seconds sits above the realistic worst case while still failing rather
 * than hanging forever if the backend stops answering.
 *
 * An ordinary conversational turn is unaffected: it still completes in the
 * same second or two it always did, and a longer ceiling costs nothing when
 * the response arrives early.
 */
export const REQUEST_TIMEOUT_MS = 90_000;

/**
 * A browser-memory ceiling on the decoded audio, not a mirror of any server
 * rule. Phase 2 limits the request *body*; nothing limits the response, and
 * a client that will happily allocate an arbitrary buffer because "our own
 * server sent it" is exactly the assumption this boundary exists to refuse.
 * Twelve megabytes is roughly four minutes of the 24 kHz mono WAV Mission 9
 * produces — far beyond any real spoken reply.
 */
export const MAX_AUDIO_RESPONSE_BYTES = 12 * 1024 * 1024;

/** Guards against a pathological string arriving where a sentence belongs. */
const MAX_TEXT_FIELD_CHARS = 8_000;

/**
 * How a turn can fail, from the interface's point of view. Deliberately
 * coarse: the user needs to know whether to try again, not which layer
 * broke.
 */
export type OrionFailure =
  | "unreachable"
  | "unavailable"
  | "rejected"
  | "upstream"
  | "server"
  | "malformed"
  | "cancelled";

/** Calm, ORION-voiced, written here — never derived from a response body. */
const FAILURE_MESSAGES: Readonly<Record<OrionFailure, string>> = {
  unreachable: "The connection to ORION was interrupted.",
  unavailable: "ORION's voice is not configured on this machine.",
  rejected: "ORION could not accept that request.",
  upstream: "ORION could not complete that request.",
  server: "ORION could not complete that request.",
  malformed: "ORION returned something unexpected.",
  cancelled: "That request was cancelled.",
};

export class OrionApiError extends Error {
  readonly failure: OrionFailure;

  constructor(failure: OrionFailure) {
    // The message is the failure key, never a server string or a caught
    // exception's text — there is no parameter through which one could be
    // attached, mirroring the backend's own `VoiceInterfaceError`.
    super(failure);
    this.name = "OrionApiError";
    this.failure = failure;
  }
}

export function messageForFailure(failure: OrionFailure): string {
  return FAILURE_MESSAGES[failure];
}

/**
 * One completed turn, in the interface's own vocabulary.
 *
 * Renamed from the server's snake_case on purpose: an object of this type
 * cannot exist unless it went through `parseTurn`, so "has this been
 * validated?" is answered by the type rather than by remembering.
 */
export interface OrionTurn {
  readonly transcript: string;
  readonly responseText: string;
  readonly audioBase64: string;
  readonly audioFormat: string;
  readonly audioMediaType: string;
  readonly audioBytes: number;
  readonly state: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readString = (source: Record<string, unknown>, key: string): string => {
  const value = source[key];
  if (typeof value !== "string" || value.length > MAX_TEXT_FIELD_CHARS) {
    throw new OrionApiError("malformed");
  }
  return value;
};

/**
 * Turn an unknown JSON value into an `OrionTurn`, or refuse.
 *
 * Written by hand rather than with a schema library. Zod would be ~14 kB of
 * runtime for seven fields, and this validator is small enough to read in
 * one sitting — which matters more here than elsewhere, because it is the
 * only thing standing between a malformed response and the React tree.
 *
 * Exported so tests (and a future audio-turn endpoint) can use exactly this
 * function rather than a second, drifting copy.
 */
export function parseTurn(value: unknown): OrionTurn {
  if (!isRecord(value)) throw new OrionApiError("malformed");

  const transcript = readString(value, "transcript");
  const responseText = readString(value, "response_text");
  const audioBase64 = readString(value, "audio_base64");
  const audioFormat = readString(value, "audio_format");
  const audioMediaType = readString(value, "audio_media_type");
  const state = readString(value, "state");
  const audioBytes = value["audio_bytes"];

  if (audioBase64.length === 0) throw new OrionApiError("malformed");
  if (audioFormat.length === 0) throw new OrionApiError("malformed");
  if (audioMediaType.length === 0) throw new OrionApiError("malformed");
  if (responseText.length === 0) throw new OrionApiError("malformed");

  if (
    typeof audioBytes !== "number" ||
    !Number.isInteger(audioBytes) ||
    audioBytes <= 0 ||
    audioBytes > MAX_AUDIO_RESPONSE_BYTES
  ) {
    throw new OrionApiError("malformed");
  }

  // Base64 inflates by exactly 4/3 (rounded up to a multiple of four), so a
  // wildly inconsistent pair means the two fields do not describe the same
  // audio. Checking here costs nothing and makes `audioBytes` trustworthy
  // enough for `orion-audio` to verify the decode against.
  const expectedEncodedLength = 4 * Math.ceil(audioBytes / 3);
  if (audioBase64.length !== expectedEncodedLength) throw new OrionApiError("malformed");

  return {
    transcript,
    responseText,
    audioBase64,
    audioFormat,
    audioMediaType,
    audioBytes,
    state,
  };
}

function failureForStatus(status: number): OrionFailure {
  if (status === 503) return "unavailable";
  if (status === 502) return "upstream";
  if (status >= 500) return "server";
  return "rejected";
}

export interface TextTurnOptions {
  /** Aborts the request. Used for component-unmount cleanup, not barge-in. */
  readonly signal?: AbortSignal | undefined;
  readonly timeoutMs?: number | undefined;
}

/**
 * The single request path both turn endpoints share.
 *
 * Extracted in Phase 4 rather than copied: the timeout, the abort
 * forwarding, the credential and cache posture, the "never read the error
 * body" rule and the response validation are security properties, and two
 * copies of a security property is one copy that will eventually be edited
 * alone. Only the path, the body and the content type differ.
 */
async function postTurn(
  path: string,
  body: BodyInit,
  headers: Readonly<Record<string, string>>,
  { signal, timeoutMs = REQUEST_TIMEOUT_MS }: TextTurnOptions,
): Promise<OrionTurn> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Forwarding rather than `AbortSignal.any` keeps this working in every
  // runtime the test suite and the browsers we target actually have.
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort, { once: true });

  let response: Response;
  try {
    response = await fetch(`${ORION_API_BASE}${path}`, {
      method: "POST",
      headers,
      body,
      signal: controller.signal,
      mode: "cors",
      // The backend sets `allow_credentials=False`; saying so on this side
      // too means no cookie or stored credential can ride along even if one
      // ever exists for this origin.
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  } catch {
    // Network down, DNS, CORS rejection, timeout, caller abort — all
    // indistinguishable to `fetch` by design, and none of them safe to
    // surface verbatim.
    throw new OrionApiError(signal?.aborted ? "cancelled" : "unreachable");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }

  if (!response.ok) {
    // The body is deliberately not read. See the module docstring.
    throw new OrionApiError(failureForStatus(response.status));
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new OrionApiError("malformed");
  }

  return parseTurn(payload);
}

/**
 * Ask ORION to answer `text`, and return the completed turn.
 *
 * Throws `OrionApiError` and nothing else — a network failure, a CORS
 * rejection, a non-2xx status, unparseable JSON and an unexpected shape all
 * arrive as a classified failure rather than as a `TypeError` from deep
 * inside `fetch`.
 */
export async function requestTextTurn(
  text: string,
  options: TextTurnOptions = {},
): Promise<OrionTurn> {
  return postTurn(
    TEXT_TURN_PATH,
    JSON.stringify({ text }),
    { "Content-Type": "application/json" },
    options,
  );
}

/**
 * Send one recorded utterance and return the completed turn.
 *
 * The recording is refused here — before a byte is sent — if it is empty,
 * larger than the server's own body ceiling, or labelled with anything
 * outside `ACCEPTED_RECORDING_MEDIA_TYPES`. All three are the client
 * declining to make a request that cannot succeed, and the last one is what
 * keeps a platform-authored string out of a request header.
 *
 * The audio itself is never stored, never given an object URL and never
 * played back; it exists as the `Blob` the caller passes in and is
 * unreferenced the moment this resolves.
 */
export async function requestAudioTurn(
  audio: Blob,
  options: TextTurnOptions = {},
): Promise<OrionTurn> {
  if (audio.size === 0 || audio.size > MAX_AUDIO_REQUEST_BYTES) {
    throw new OrionApiError("rejected");
  }
  if (!ACCEPTED_RECORDING_MEDIA_TYPES.includes(audio.type)) {
    throw new OrionApiError("rejected");
  }

  // `audio.type` has just been proven to be one of our own constants, so
  // this header value is a literal from this module in every reachable case.
  return postTurn(AUDIO_TURN_PATH, audio, { "Content-Type": audio.type }, options);
}
