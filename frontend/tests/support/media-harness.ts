/**
 * Deterministic stand-ins for the three browser capabilities jsdom does not
 * implement at all: `getUserMedia`, `MediaRecorder` and Web Audio.
 *
 * Nothing here fakes ORION and nothing here fakes *sound*. The recorder
 * produces real `Blob`s of a size the test chose, the analyser fills the
 * caller's buffer with a level the test chose, and the animation-frame clock
 * only advances when the test says so. Every timing decision a real browser
 * would make — when permission resolves, when a chunk arrives, when a frame
 * runs — is therefore made explicitly by the test rather than by a timer,
 * which is what lets the assertions be about behaviour instead of luck.
 *
 * The harness also records what was *released*: which tracks were stopped,
 * which nodes were disconnected, which contexts were closed. Those counters
 * are the point — a microphone that is never released is invisible in a
 * screenshot and obvious here.
 */

export type CaptureOutcome = "grant" | { readonly errorName: string };

export interface FakeTrack {
  readonly kind: "audio";
  readyState: "live" | "ended";
  stopCount: number;
  stop(): void;
}

export interface FakeRecorder extends EventTarget {
  state: "inactive" | "recording" | "paused";
  readonly mimeType: string;
  start(): void;
  stop(): void;
}

export interface FakeNode {
  connectCount: number;
  disconnectCount: number;
}

export interface FakeContext {
  state: string;
  closed: boolean;
  resumeCount: number;
  readonly nodes: FakeNode[];
}

export interface MediaHarness {
  /** Constraints passed to every `getUserMedia` call, in order. */
  readonly constraints: MediaStreamConstraints[];
  /** Every track handed out, whether or not it was stopped. */
  readonly tracks: FakeTrack[];
  /** Every recorder constructed. */
  readonly recorders: FakeRecorder[];
  /** Every `AudioContext` built by the analyser. */
  readonly contexts: FakeContext[];
  /** Media types `MediaRecorder.isTypeSupported` will accept. */
  supportedTypes: string[];
  /** What the next `getUserMedia` does. */
  outcome: CaptureOutcome;
  /** Bytes the next recording produces. 0 means "nothing was captured". */
  chunkBytes: number;
  /** Make the next `stop()` fail the recorder instead of finishing it. */
  failRecording: boolean;
  /** The constant sample value the analyser reports, i.e. the "loudness". */
  level: number;
  /** Make `createMediaElementSource` throw, as a re-routed element does. */
  failElementSource: boolean;
  /** Build an analyser with no `getFloatTimeDomainData`, as old Safari had. */
  omitTimeDomainRead: boolean;
  /** Make the time-domain read throw part-way through the frame loop. */
  failTimeDomainRead: boolean;
  /** Run every pending animation frame once, at `timestamp`. */
  tick(timestamp: number): void;
  /** Animation frames requested and not yet run or cancelled. */
  pendingFrames(): number;
  restore(): void;
}

interface Globals {
  navigator?: unknown;
  MediaRecorder?: unknown;
  AudioContext?: unknown;
  requestAnimationFrame?: unknown;
  cancelAnimationFrame?: unknown;
}

export function installMediaHarness(): MediaHarness {
  const scope = globalThis as unknown as Globals & Record<string, unknown>;

  const original = {
    mediaDevices: Object.getOwnPropertyDescriptor(navigator, "mediaDevices"),
    MediaRecorder: scope["MediaRecorder"],
    AudioContext: scope["AudioContext"],
    requestAnimationFrame: scope["requestAnimationFrame"],
    cancelAnimationFrame: scope["cancelAnimationFrame"],
  };

  const constraints: MediaStreamConstraints[] = [];
  const tracks: FakeTrack[] = [];
  const recorders: FakeRecorder[] = [];
  const contexts: FakeContext[] = [];

  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;

  const harness: MediaHarness = {
    constraints,
    tracks,
    recorders,
    contexts,
    supportedTypes: ["audio/webm", "audio/ogg", "audio/mp4"],
    outcome: "grant",
    chunkBytes: 4096,
    failRecording: false,
    level: 0,
    failElementSource: false,
    omitTimeDomainRead: false,
    failTimeDomainRead: false,
    tick(timestamp: number) {
      const due = [...frames.entries()];
      frames.clear();
      for (const [, callback] of due) callback(timestamp);
    },
    pendingFrames: () => frames.size,
    restore() {
      if (original.mediaDevices) {
        Object.defineProperty(navigator, "mediaDevices", original.mediaDevices);
      } else {
        delete (navigator as unknown as Record<string, unknown>)["mediaDevices"];
      }
      scope["MediaRecorder"] = original.MediaRecorder;
      scope["AudioContext"] = original.AudioContext;
      scope["requestAnimationFrame"] = original.requestAnimationFrame;
      scope["cancelAnimationFrame"] = original.cancelAnimationFrame;
    },
  };

  // ------------------------------------------------------------ capture ---

  const makeTrack = (): FakeTrack => {
    const track: FakeTrack = {
      kind: "audio",
      readyState: "live",
      stopCount: 0,
      stop() {
        track.stopCount += 1;
        track.readyState = "ended";
      },
    };
    tracks.push(track);
    return track;
  };

  const getUserMedia = (request: MediaStreamConstraints): Promise<MediaStream> => {
    constraints.push(request);
    if (harness.outcome !== "grant") {
      const error = new Error("capture refused");
      error.name = harness.outcome.errorName;
      return Promise.reject(error);
    }
    // Two tracks, so "released the microphone" cannot pass by stopping one.
    const own = [makeTrack(), makeTrack()];
    const stream = { getTracks: () => own } as unknown as MediaStream;
    return Promise.resolve(stream);
  };

  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });

  // ----------------------------------------------------------- recorder ---

  class HarnessRecorder extends EventTarget implements FakeRecorder {
    state: "inactive" | "recording" | "paused" = "inactive";
    readonly mimeType: string;

    constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
      super();
      this.mimeType = options?.mimeType ?? "audio/webm";
      recorders.push(this);
    }

    static isTypeSupported(type: string): boolean {
      return harness.supportedTypes.includes(type);
    }

    start(): void {
      this.state = "recording";
    }

    stop(): void {
      if (this.state === "inactive") return;
      this.state = "inactive";
      if (harness.failRecording) {
        this.dispatchEvent(new Event("error"));
        return;
      }
      if (harness.chunkBytes > 0) {
        const data = new Blob([new Uint8Array(harness.chunkBytes)], { type: this.mimeType });
        const event = new Event("dataavailable") as Event & { data: Blob };
        event.data = data;
        this.dispatchEvent(event);
      }
      this.dispatchEvent(new Event("stop"));
    }
  }

  scope["MediaRecorder"] = HarnessRecorder;

  // ---------------------------------------------------------- web audio ---

  const makeNode = <T extends object>(context: FakeContext, extra: T): T & FakeNode => {
    const node = {
      connectCount: 0,
      disconnectCount: 0,
      connect() {
        node.connectCount += 1;
      },
      disconnect() {
        node.disconnectCount += 1;
      },
      ...extra,
    } as unknown as T & FakeNode;
    context.nodes.push(node);
    return node;
  };

  class HarnessAudioContext {
    state = "running";
    closed = false;
    resumeCount = 0;
    readonly nodes: FakeNode[] = [];
    readonly destination = {};

    constructor() {
      contexts.push(this as unknown as FakeContext);
    }

    createAnalyser() {
      const read = {
        getFloatTimeDomainData(samples: Float32Array) {
          if (harness.failTimeDomainRead) throw new Error("analyser went away");
          samples.fill(harness.level);
        },
      };
      return makeNode(
        this as unknown as FakeContext,
        harness.omitTimeDomainRead ? { fftSize: 2048 } : { fftSize: 2048, ...read },
      );
    }

    createGain() {
      return makeNode(this as unknown as FakeContext, { gain: { value: 1 } });
    }

    createMediaStreamSource(_stream: MediaStream) {
      return makeNode(this as unknown as FakeContext, {});
    }

    createMediaElementSource(_element: HTMLAudioElement) {
      if (harness.failElementSource) throw new Error("element already routed");
      return makeNode(this as unknown as FakeContext, {});
    }

    resume(): Promise<void> {
      this.resumeCount += 1;
      return Promise.resolve();
    }

    close(): Promise<void> {
      this.closed = true;
      this.state = "closed";
      return Promise.resolve();
    }
  }

  scope["AudioContext"] = HarnessAudioContext;

  // --------------------------------------------------------- frame loop ---

  scope["requestAnimationFrame"] = (callback: FrameRequestCallback): number => {
    frameId += 1;
    frames.set(frameId, callback);
    return frameId;
  };
  scope["cancelAnimationFrame"] = (id: number): void => {
    frames.delete(id);
  };

  return harness;
}

/** The sample level whose RMS lands at `decibels` dBFS. */
export const levelAt = (decibels: number): number => 10 ** (decibels / 20);

/** Every track handed out has been released. */
export const allTracksReleased = (harness: MediaHarness): boolean =>
  harness.tracks.length > 0 && harness.tracks.every((track) => track.readyState === "ended");
