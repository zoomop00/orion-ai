/**
 * The Web Audio boundary: the *only* file in this frontend that may create
 * an `AudioContext` or read from an `AnalyserNode`.
 *
 * It exists so the ORION core's `amplitude` seam — declared in Phase 1,
 * deliberately unused until now — can be driven by sound that is genuinely
 * happening: the user's microphone while ORION is LISTENING, and ORION's own
 * synthesized speech while ORION is SPEAKING. Nothing here generates,
 * simulates, interpolates or randomises a level. If the browser has no Web
 * Audio, or the graph cannot be built, this module returns an inert monitor
 * and the core falls back to its fixed per-state constant — a slightly less
 * alive visual, never a fabricated one.
 *
 * TIME DOMAIN ONLY. `getFloatTimeDomainData` is the single read; the
 * frequency-domain calls are not used and remain banned frontend-wide by
 * `tests/source-safety.test.ts`. See `audio-envelope.ts` for why an RMS
 * envelope is the right amount of signal processing for this job and an FFT
 * is not.
 *
 * TWO GRAPHS, TWO REASONS FOR THEIR SHAPE.
 *
 *   microphone: MediaStreamSource -> Analyser -> Gain(0) -> destination
 *   playback:   MediaElementSource -> Analyser -> destination
 *
 * The microphone chain ends in a *silent* gain node. Connecting a live
 * microphone to the speakers would be an instant feedback howl, but a graph
 * with no path to the destination is not guaranteed to be pulled by every
 * engine — a zero gain gives the graph its sink while guaranteeing not one
 * sample of the user's voice is ever played back. The playback chain, by
 * contrast, *must* reach the destination: `createMediaElementSource`
 * permanently reroutes the element's output into the graph, so an analyser
 * that did not forward it would silence ORION. That is also why every step
 * of that path is wrapped — if any of it fails, the monitor is abandoned and
 * playback is left exactly as it was.
 *
 * EVERYTHING IS RELEASED. `stop()` cancels the animation frame, disconnects
 * every node, closes the `AudioContext` and reports a final zero so the core
 * settles. It is idempotent and safe to call from a React cleanup that may
 * run before the graph ever finished being built.
 *
 * NOTHING IS RECORDED. No sample leaves this module. The only value that
 * escapes is one number per frame, in 0..1.
 */

import { EnvelopeFollower } from "./audio-envelope";

/**
 * 1024 samples is ~21 ms at 48 kHz: long enough that the RMS of a low
 * male-voice fundamental is stable, short enough to stay well inside one
 * animation frame. Larger windows smear consonants; smaller ones make the
 * envelope depend on where in a waveform cycle the frame happened to land.
 */
export const ANALYSER_FFT_SIZE = 1024;

export type MonitorSource =
  | { readonly kind: "microphone"; readonly stream: MediaStream }
  | { readonly kind: "playback"; readonly element: HTMLAudioElement };

export interface AmplitudeMonitor {
  /** False when the browser could not provide analysis at all. */
  readonly active: boolean;
  /** Release every resource. Idempotent. */
  stop(): void;
}

/** A monitor that never started: no context, no frame, nothing to release. */
const INERT: AmplitudeMonitor = { active: false, stop() {} };

type AudioContextConstructor = new () => AudioContext;

function audioContextConstructor(): AudioContextConstructor | null {
  if (typeof window === "undefined") return null;
  const scope = window as unknown as {
    AudioContext?: AudioContextConstructor;
    webkitAudioContext?: AudioContextConstructor;
  };
  return scope.AudioContext ?? scope.webkitAudioContext ?? null;
}

/**
 * Begin reporting the live amplitude of `source` to `onEnvelope`, once per
 * animation frame, until the returned monitor is stopped.
 *
 * Never throws and never rejects: an unsupported browser, a blocked context
 * or an element that has already been routed through some other graph all
 * produce `INERT`. A failure to visualise sound must not be able to break the
 * turn that is producing it.
 */
export function monitorAmplitude(
  source: MonitorSource,
  onEnvelope: (envelope: number) => void,
): AmplitudeMonitor {
  const Constructor = audioContextConstructor();
  if (Constructor === null) return INERT;
  if (typeof requestAnimationFrame !== "function") return INERT;

  let context: AudioContext;
  try {
    context = new Constructor();
  } catch {
    return INERT;
  }

  const nodes: AudioNode[] = [];
  let frame: number | null = null;
  let stopped = false;

  const release = (): void => {
    if (stopped) return;
    stopped = true;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    for (const node of nodes) {
      try {
        node.disconnect();
      } catch {
        // A node the engine already tore down. Nothing left to release.
      }
    }
    nodes.length = 0;
    try {
      void context.close();
    } catch {
      // Already closed, or closing is unsupported. Either way, done.
    }
    onEnvelope(0);
  };

  let analyser: AnalyserNode;
  try {
    analyser = context.createAnalyser();
    analyser.fftSize = ANALYSER_FFT_SIZE;
    nodes.push(analyser);

    if (source.kind === "microphone") {
      const input = context.createMediaStreamSource(source.stream);
      const silence = context.createGain();
      // Explicitly zero: the user's own voice is never played back.
      silence.gain.value = 0;
      input.connect(analyser);
      analyser.connect(silence);
      silence.connect(context.destination);
      nodes.push(input, silence);
    } else {
      const input = context.createMediaElementSource(source.element);
      input.connect(analyser);
      // Mandatory, not optional: see the module docstring.
      analyser.connect(context.destination);
      nodes.push(input);
    }
  } catch {
    release();
    return INERT;
  }

  if (typeof analyser.getFloatTimeDomainData !== "function") {
    release();
    return INERT;
  }

  // A context created outside a user gesture starts suspended. Both callers
  // are inside one, so this is belt and braces rather than the mechanism.
  try {
    void Promise.resolve(context.resume()).catch(() => undefined);
  } catch {
    // Some engines resume synchronously and have no promise to attach to.
  }

  const samples = new Float32Array(analyser.fftSize);
  const follower = new EnvelopeFollower();
  let previous: number | null = null;

  const step = (timestamp: number): void => {
    if (stopped) return;
    try {
      analyser.getFloatTimeDomainData(samples);
    } catch {
      release();
      return;
    }
    const delta = previous === null ? 0 : timestamp - previous;
    previous = timestamp;
    onEnvelope(follower.push(samples, delta));
    frame = requestAnimationFrame(step);
  };

  frame = requestAnimationFrame(step);

  return {
    active: true,
    stop: release,
  };
}
