/**
 * The microphone boundary: the *only* file in this frontend that may call
 * `getUserMedia` or construct a `MediaRecorder`.
 *
 * PERMISSION IS ONLY EVER ASKED FOR BY HAND. `openMicrophone` is called from
 * exactly one place — the mic button's click handler — and from nothing that
 * runs on mount, on render, on focus or on a timer. There is no
 * `navigator.permissions` query either: probing permission state without the
 * user asking is itself a fingerprinting surface, and the honest way to find
 * out whether ORION may listen is to have the user say so.
 *
 * A HARD DURATION CEILING, ENFORCED HERE. `MAX_RECORDING_SECONDS` matches
 * Mission 9's `orion.voice.audio_input.MAX_RECORDING_SECONDS` (30 s) and
 * `MAX_RECORDING_BYTES` matches its `MAX_AUDIO_INPUT_BYTES` (25 MB, which is
 * also `orion.interface.schemas.MAX_REQUEST_BODY_BYTES`). The values are
 * restated rather than imported for the same reason Mission 9 restates
 * Deepgram's: this is a different runtime with no access to the Python
 * constants, and a client limit that is quietly looser than the server's is
 * worse than no client limit at all. The timer lives in this module and stops
 * the recorder itself, so a caller that forgets to stop cannot leave a
 * microphone open — the ceiling is a property of the boundary, not of the
 * calling code's discipline.
 *
 * THE TRACKS ARE ALWAYS RELEASED. Every exit path — a normal stop, the
 * duration ceiling, a recorder error, an oversized or empty result, a
 * cancellation, an unmount — runs `stop()` on every track of the stream, in a
 * `finally`. That is what turns the browser's recording indicator off; a
 * `MediaRecorder` that has merely stopped does not.
 *
 * NOTHING IS STORED. The audio exists as `Blob` chunks in memory for the
 * length of one turn and is handed straight to the API client. No object URL
 * is minted for it, no file is written, no `localStorage`, no cache, no
 * playback of the user's own voice. When the turn is over the only reference
 * is dropped.
 *
 * THE MEDIA TYPE IS OURS, NEVER THE BROWSER'S. `MediaRecorder` labels its
 * output with a codec-decorated string of its own choosing
 * (`audio/webm;codecs=opus`). That string is never forwarded anywhere: the
 * returned `Blob` is constructed with one of the three constants declared
 * below, each of which the Phase 2 backend already accepts, so the
 * `Content-Type` the API client eventually sends can only ever be a literal
 * from this file.
 */

/** Mirrors `orion.voice.audio_input.MAX_RECORDING_SECONDS`. */
export const MAX_RECORDING_SECONDS = 30;

/** Mirrors `orion.voice.audio_input.MAX_AUDIO_INPUT_BYTES`. */
export const MAX_RECORDING_BYTES = 25 * 1024 * 1024;

/**
 * Containers this build will record into, best first, restricted to types
 * `orion.interface.schemas.ACCEPTED_AUDIO_CONTENT_TYPES` already allows.
 * WebM/Opus is what Chrome and Firefox produce; Safari produces MP4/AAC.
 */
export const RECORDING_MEDIA_TYPES = ["audio/webm", "audio/ogg", "audio/mp4"] as const;

export type RecordingMediaType = (typeof RECORDING_MEDIA_TYPES)[number];

export type MicrophoneFailure =
  | "unsupported"
  | "denied"
  | "unavailable"
  | "failed"
  | "silent";

const FAILURE_MESSAGES: Readonly<Record<MicrophoneFailure, string>> = {
  unsupported: "This browser cannot record audio. Type instead.",
  denied: "ORION needs microphone permission to listen.",
  unavailable: "No microphone is available on this machine.",
  failed: "That recording could not be completed.",
  silent: "I heard nothing that time.",
};

export class OrionMicrophoneError extends Error {
  readonly failure: MicrophoneFailure;

  constructor(failure: MicrophoneFailure) {
    // No message parameter, matching `OrionApiError` and `OrionAudioError`:
    // there must be no way to attach a platform exception's text — which can
    // name devices, drivers and paths — to something the interface renders.
    super(failure);
    this.name = "OrionMicrophoneError";
    this.failure = failure;
  }
}

export function messageForMicrophoneFailure(failure: MicrophoneFailure): string {
  return FAILURE_MESSAGES[failure];
}

/**
 * Map a `getUserMedia` rejection onto our own coarse vocabulary.
 *
 * Only `name` is read. The `message` is deliberately never touched: browsers
 * put device names and, on some platforms, driver paths in there.
 */
function classifyCaptureError(cause: unknown): MicrophoneFailure {
  const name =
    typeof cause === "object" && cause !== null && "name" in cause
      ? String((cause as { name?: unknown }).name)
      : "";

  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return "denied";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "NotReadableError":
    case "TrackStartError":
    case "OverconstrainedError":
      return "unavailable";
    default:
      return "failed";
  }
}

interface MediaRecorderLike {
  new (stream: MediaStream, options?: MediaRecorderOptions): MediaRecorder;
  isTypeSupported?: (type: string) => boolean;
}

function recorderConstructor(): MediaRecorderLike | null {
  if (typeof window === "undefined") return null;
  const scope = window as unknown as { MediaRecorder?: MediaRecorderLike };
  return scope.MediaRecorder ?? null;
}

function chooseMediaType(Recorder: MediaRecorderLike): RecordingMediaType {
  const supports = Recorder.isTypeSupported;
  if (typeof supports !== "function") {
    // No way to ask. WebM is the broadest guess, and an unsupported choice
    // surfaces immediately as a construction failure rather than silently.
    return RECORDING_MEDIA_TYPES[0];
  }
  for (const candidate of RECORDING_MEDIA_TYPES) {
    if (supports.call(Recorder, candidate)) return candidate;
  }
  throw new OrionMicrophoneError("unsupported");
}

export interface MicrophoneSession {
  /** The live stream, for the analyser. Never played back, never stored. */
  readonly stream: MediaStream;
  /** One of `RECORDING_MEDIA_TYPES`. Never a browser-authored string. */
  readonly mediaType: RecordingMediaType;
  /**
   * Stop recording, release every track, and resolve with the audio.
   *
   * Rejects with `OrionMicrophoneError` if the recorder failed, if nothing
   * was captured (`silent`), or if the result is impossibly large. Releases
   * the tracks either way.
   */
  stop(): Promise<Blob>;
  /** Abandon the recording and release everything. Never rejects. */
  cancel(): void;
}

export interface OpenMicrophoneOptions {
  /**
   * Called when the duration ceiling stopped the recorder on its own, so the
   * interface can finish the turn it did not know was over. The recording is
   * already complete by then; the caller still calls `stop()` to collect it.
   */
  readonly onLimitReached?: (() => void) | undefined;
  /** Overridable for tests. Clamped to `MAX_RECORDING_SECONDS`. */
  readonly maxSeconds?: number | undefined;
}

/**
 * Ask for the microphone and start recording. Call only from a user gesture.
 *
 * Resolves once audio is genuinely being captured, so a caller may treat the
 * resolution — and nothing earlier — as the moment ORION is LISTENING.
 */
export async function openMicrophone({
  onLimitReached,
  maxSeconds = MAX_RECORDING_SECONDS,
}: OpenMicrophoneOptions = {}): Promise<MicrophoneSession> {
  const Recorder = recorderConstructor();
  const devices = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
  if (Recorder === null || typeof devices?.getUserMedia !== "function") {
    throw new OrionMicrophoneError("unsupported");
  }

  const mediaType = chooseMediaType(Recorder);

  let stream: MediaStream;
  try {
    stream = await devices.getUserMedia({
      // Audio only, stated explicitly. A bare `{ audio: true }` would be
      // equivalent today, but saying `video: false` means no future edit can
      // widen this to a camera without deleting the word "false".
      video: false,
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
  } catch (cause) {
    throw new OrionMicrophoneError(classifyCaptureError(cause));
  }

  const releaseTracks = (): void => {
    for (const track of stream.getTracks()) {
      try {
        track.stop();
      } catch {
        // Already ended. The indicator is off either way.
      }
    }
  };

  let recorder: MediaRecorder;
  try {
    recorder = new Recorder(stream, { mimeType: mediaType });
  } catch {
    releaseTracks();
    throw new OrionMicrophoneError("unsupported");
  }

  const chunks: Blob[] = [];
  let settled = false;

  const finished = new Promise<Blob>((resolve, reject) => {
    recorder.addEventListener("dataavailable", (event) => {
      const data = (event as BlobEvent).data;
      if (data && data.size > 0) chunks.push(data);
    });
    recorder.addEventListener("stop", () => {
      if (settled) return;
      settled = true;
      // Our constant, not `recorder.mimeType`. See the module docstring.
      resolve(new Blob(chunks, { type: mediaType }));
    });
    recorder.addEventListener("error", () => {
      if (settled) return;
      settled = true;
      reject(new OrionMicrophoneError("failed"));
    });
  });
  // `cancel()` abandons this promise. Attaching a no-op handler here keeps
  // that from surfacing as an unhandled rejection without swallowing the
  // error `stop()` still needs to see.
  finished.catch(() => undefined);

  const halt = (): void => {
    try {
      if (recorder.state !== "inactive") recorder.stop();
    } catch {
      // Already inactive, or the engine tore it down. Nothing to stop.
    }
  };

  const ceiling = Math.min(Math.max(1, maxSeconds), MAX_RECORDING_SECONDS);
  const limitTimer = setTimeout(() => {
    halt();
    onLimitReached?.();
  }, ceiling * 1000);

  try {
    recorder.start();
  } catch {
    clearTimeout(limitTimer);
    releaseTracks();
    throw new OrionMicrophoneError("failed");
  }

  let closed = false;
  const close = (): void => {
    if (closed) return;
    closed = true;
    releaseTracks();
  };

  return {
    stream,
    mediaType,
    async stop(): Promise<Blob> {
      clearTimeout(limitTimer);
      halt();
      try {
        const audio = await finished;
        if (audio.size === 0) throw new OrionMicrophoneError("silent");
        if (audio.size > MAX_RECORDING_BYTES) throw new OrionMicrophoneError("failed");
        return audio;
      } finally {
        close();
      }
    },
    cancel(): void {
      clearTimeout(limitTimer);
      halt();
      close();
    },
  };
}
