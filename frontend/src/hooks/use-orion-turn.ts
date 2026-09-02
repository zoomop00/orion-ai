"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import {
  createAmplitudeSignal,
  type AmplitudeSignal,
  type AmplitudeSource,
} from "@/lib/amplitude-signal";
import { monitorAmplitude, type AmplitudeMonitor } from "@/lib/orion-analyser";
import {
  OrionApiError,
  messageForFailure,
  requestAudioTurn,
  requestTextTurn,
  type OrionTurn,
} from "@/lib/orion-api";
import type { OrionAnalysis } from "@/lib/orion-analysis";
import { OrionAudioError, messageForAudioFailure, playTurnAudio } from "@/lib/orion-audio";
import {
  OrionMicrophoneError,
  messageForMicrophoneFailure,
  openMicrophone,
  type MicrophoneSession,
} from "@/lib/orion-microphone";
import type { OrionVisualState } from "@/state/orion-visual-state";

/**
 * One turn, from keystroke *or microphone* to silence.
 *
 * THE LIFECYCLE IS THE REAL ONE. `listening` begins only when the browser
 * has actually granted the microphone and a `MediaRecorder` is running;
 * `thinking` lasts exactly as long as the HTTP request takes; `speaking`
 * begins only when `playTurnAudio` reports that the browser has genuinely
 * started playing, and ends on the element's own `ended` event. There is no
 * timer anywhere in this file except the one that *ends* a recording at the
 * duration ceiling, and that one lives in `orion-microphone.ts`. No state is
 * ever entered to look busy.
 *
 * STILL ONE STATE MODEL. `OrionVisualState` from `@/state/orion-visual-state`
 * remains the only vocabulary and still has exactly four members. Phase 4
 * adds no state, no parallel machine and no second source of truth — it
 * finally *reaches* `listening`, which Phase 1 declared and Phase 3 left to
 * the development controls. The reducer below sequences that vocabulary; the
 * backend's `VoiceStateMachine` remains the authority on the protocol.
 *
 * WHY AMPLITUDE IS NOT IN THE REDUCER, AND NO LONGER IN REACT AT ALL. The
 * envelope changes up to sixty times a second. Threading that through the
 * same reducer that owns the lifecycle would mean every frame constructing a
 * new snapshot of fields that did not change, and would blur the line between
 * "what ORION is doing" and "how loud it is right now". Keeping it in a
 * `useState` of its own fixed the blurred line but not the cost: a render of
 * this hook, of `OrionInterface` and of `OrionCore` was still scheduled every
 * frame so that one custom property could change at the end of it. It now
 * lives in an `AmplitudeSignal` (see `lib/amplitude-signal.ts`) that the
 * analyser writes and `OrionCore` subscribes to, and the frame loop causes no
 * React render whatsoever. The reducer still owns the lifecycle; React still
 * owns which of the four states ORION is in, and so still owns the fallback
 * envelope for when nothing is being measured.
 *
 * WHY A REDUCER. The invalid states are the interesting ones: "busy but
 * idle", "speaking with no audio", "listening with no microphone", "error
 * while a request is still in flight". Expressing every transition as one
 * function makes those unreachable by construction rather than by careful
 * ordering of `setState` calls. The `dev` action is refused outright while a
 * turn is running, so the development controls cannot yank the interface out
 * from under a live turn — including out from under an open microphone.
 */

export interface OrionTurnSnapshot {
  readonly visualState: OrionVisualState;
  /** What ORION understood. Untrusted text — rendered as text, never HTML. */
  readonly transcript: string | null;
  /**
   * What the user typed, echoed back the instant it was sent and held only
   * until `transcript` arrives to replace it.
   *
   * NOT A SECOND TRANSCRIPT, AND NOT A CLAIM. `transcript` remains what ORION
   * says it understood, and only the backend ever writes it. This is the
   * user's own text, shown so a turn reads as *addressed* rather than as a
   * paragraph appearing under an orb — the interface acknowledges the input
   * before it has an answer. A spoken turn has nothing to echo and leaves it
   * null, because ORION genuinely does not know what was said until the
   * transcript comes back.
   */
  readonly prompt: string | null;
  /** What ORION said back. Also rendered as text only. */
  readonly responseText: string | null;
  /**
   * The analysis this turn produced, or `null` — which is every ordinary
   * turn, and every turn before this one finished.
   *
   * Carried, not acted on. Phase 5.3 validates it at the API boundary and
   * puts it here; Phase 5.4 draws it. It deliberately adds no state: an
   * analysis is a *result* of the `thinking` -> `speaking` sequence the four
   * visual states already describe, not a fifth thing ORION can be doing.
   */
  readonly analysis: OrionAnalysis | null;
  /** A calm, frontend-authored sentence. Never a server or exception string. */
  readonly error: string | null;
  /** True from submission (or from asking for the microphone) until rest. */
  readonly busy: boolean;
}

type TurnAction =
  | { readonly type: "arming" }
  | { readonly type: "listening" }
  | { readonly type: "submitted"; readonly prompt?: string }
  | {
      readonly type: "heard";
      readonly transcript: string;
      readonly responseText: string;
      readonly analysis: OrionAnalysis | null;
    }
  | { readonly type: "speaking" }
  | { readonly type: "settled" }
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "dev"; readonly state: OrionVisualState };

const INITIAL: OrionTurnSnapshot = {
  visualState: "idle",
  transcript: null,
  prompt: null,
  responseText: null,
  analysis: null,
  error: null,
  busy: false,
};

function reduce(snapshot: OrionTurnSnapshot, action: TurnAction): OrionTurnSnapshot {
  switch (action.type) {
    case "arming":
      // The permission prompt is open. ORION is *not* listening yet and must
      // not claim to be: the interface is locked, and the core stays at rest
      // until the microphone is genuinely running.
      return {
        visualState: "idle",
        transcript: null,
        prompt: null,
        responseText: null,
        analysis: null,
        error: null,
        busy: true,
      };
    case "listening":
      return { ...snapshot, visualState: "listening" };
    case "submitted":
      return {
        visualState: "thinking",
        transcript: null,
        // A typed turn carries its own text; a spoken one does not, and is
        // left with nothing to show until ORION reports what it heard.
        prompt: action.prompt ?? null,
        responseText: null,
        analysis: null,
        error: null,
        busy: true,
      };
    case "heard":
      // Deliberately does NOT touch `visualState`. The response has arrived
      // but no sound has been made yet, so ORION is still thinking.
      return {
        ...snapshot,
        transcript: action.transcript,
        responseText: action.responseText,
        analysis: action.analysis,
      };
    case "speaking":
      return { ...snapshot, visualState: "speaking" };
    case "settled":
      return { ...snapshot, visualState: "idle", busy: false };
    case "failed":
      // Whatever failed — a refused microphone, a dead backend, a recording
      // with nothing in it — the interface comes to rest and is immediately
      // usable again. The last transcript/response is cleared so a stale
      // reply cannot sit under a fresh error.
      return {
        visualState: "idle",
        transcript: null,
        prompt: null,
        responseText: null,
        analysis: null,
        error: action.message,
        busy: false,
      };
    case "dev":
      // Refused outright while a turn is running, so the development
      // controls cannot yank the interface out from under a live turn.
      // Otherwise it also clears any error still on screen: a previewed
      // state is a new interaction, and copy describing a failure that is
      // no longer happening must not survive into it.
      return snapshot.busy
        ? snapshot
        : { ...snapshot, visualState: action.state, error: null };
  }
}

/** Map any thrown value to a sentence this interface owns. */
function messageFor(cause: unknown): string {
  if (cause instanceof OrionApiError) return messageForFailure(cause.failure);
  if (cause instanceof OrionAudioError) return messageForAudioFailure(cause.failure);
  if (cause instanceof OrionMicrophoneError) return messageForMicrophoneFailure(cause.failure);
  return "ORION could not complete that request.";
}

const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));

export interface OrionTurnController extends OrionTurnSnapshot {
  /**
   * The live 0..1 envelope of whatever sound is actually happening — the
   * user's voice while listening, ORION's own speech while speaking — or
   * `null` when nothing is being analysed, in which case the core falls back
   * to its fixed per-state constant. Never a simulated value.
   *
   * A signal rather than a number, and that is the whole point: reading it
   * per frame must not be a render. The object identity is stable for the
   * life of the hook, so a component subscribes to it once.
   */
  readonly amplitude: AmplitudeSignal;
  /** True only while a microphone is genuinely open. */
  readonly capturing: boolean;
  /** Runs a typed turn. No-ops on blank input or while a turn is running. */
  readonly submit: (text: string) => void;
  /** Asks for the microphone and starts recording. User gesture only. */
  readonly startListening: () => void;
  /** Stops recording, releases the device, and runs the spoken turn. */
  readonly stopListening: () => void;
  /** The Phase 1 development state switcher. Ignored while a turn runs. */
  readonly setVisualState: (state: OrionVisualState) => void;
}

export function useOrionTurn(): OrionTurnController {
  const [snapshot, dispatch] = useReducer(reduce, INITIAL);
  // Created once and never replaced: it is the identity `OrionCore`
  // subscribes to, not a value, so it must not change between renders.
  const [amplitude] = useState<AmplitudeSource>(createAmplitudeSignal);
  const [capturing, setCapturing] = useState(false);

  // `busy` is mirrored in a ref because the callbacks below are stable and
  // would otherwise close over a stale value. The ref is owned entirely by
  // those callbacks and their `finally` — never written during render — so a
  // second submission in the same tick is refused before React has even
  // re-rendered.
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  // Monotonic, not random: identifies which turn a late result belongs to,
  // so a superseded turn can never write over a newer one.
  const turnRef = useRef(0);
  const sessionRef = useRef<MicrophoneSession | null>(null);
  const monitorRef = useRef<AmplitudeMonitor | null>(null);
  // Set from an effect below so the recording ceiling, which fires from a
  // timer inside `orion-microphone`, always reaches the current handler.
  const onLimitRef = useRef<(() => void) | null>(null);

  const pushAmplitude = useCallback(
    (value: number) => {
      /*
       * Fifty steps, not a hundred.
       *
       * This is the one place in the interface where something arrives sixty
       * times a second, so it is the one place where the quantisation is a
       * performance decision rather than a rounding convenience. The waveform
       * is forty-four bars a few pixels tall: a two-percent change in the
       * envelope moves no bar by a visible amount, and stepping there rather
       * than at one percent roughly halves how often anything downstream is
       * touched at all -- the signal drops every frame that lands in the same
       * step, which during ordinary speech is most of them, so most frames
       * write no custom property and invalidate no style.
       *
       * The envelope itself is not coarsened by this. `audio-envelope.ts`
       * still smooths the real signal at full precision and the transition in
       * the stylesheet still eases between steps, so what reaches the eye is a
       * continuous curve; the step only decides how often the curve is
       * resampled.
       */
      amplitude.set(Math.round(clamp01(value) * 50) / 50);
    },
    [amplitude],
  );

  const stopMonitor = useCallback(() => {
    monitorRef.current?.stop();
    monitorRef.current = null;
    // `null`, not `0`: nothing is being analysed any more, so the core goes
    // back to its per-state constant rather than sitting at measured silence.
    amplitude.set(null);
  }, [amplitude]);

  useEffect(
    () => () => {
      // Unmount: stop the request, the analysis and the microphone. This is
      // cleanup, not user-facing interruption. `stop()` cancels the pending
      // animation frame inside the monitor, so no callback survives this --
      // including one already scheduled for the frame after unmount.
      abortRef.current?.abort();
      monitorRef.current?.stop();
      monitorRef.current = null;
      sessionRef.current?.cancel();
      sessionRef.current = null;
      onLimitRef.current = null;
    },
    [],
  );

  /** Claim the next turn id and a fresh abort controller. */
  const begin = useCallback(() => {
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    turnRef.current += 1;
    const turnId = turnRef.current;
    return {
      controller,
      turnId,
      current: () => turnId === turnRef.current && !controller.signal.aborted,
    };
  }, []);

  /**
   * The half of a turn that is identical however it started: play the audio,
   * drive SPEAKING from real playback, and analyse the sound while it lasts.
   */
  const speak = useCallback(
    async (turn: OrionTurn, signal: AbortSignal, current: () => boolean) => {
      dispatch({
        type: "heard",
        transcript: turn.transcript,
        responseText: turn.responseText,
        // Already validated in full by `parseTurn`; nothing here inspects,
        // reshapes or renders it.
        analysis: turn.analysis,
      });

      // An unspoken turn is a complete turn: ORION answered in writing
      // because this deployment has no text-to-speech credential. The reply
      // is already on screen from the dispatch above, so the core settles
      // straight back to idle rather than entering SPEAKING with nothing to
      // speak -- and no audio failure is reported, because nothing failed.
      if (!turn.spoken) {
        if (current()) dispatch({ type: "settled" });
        return;
      }

      await playTurnAudio(turn, {
        signal,
        onStart: (element) => {
          if (!current()) return;
          // Never two graphs at once: a spoken turn's microphone monitor is
          // already stopped by `stopListening`, and this makes that a fact
          // rather than an ordering the next edit has to preserve.
          monitorRef.current?.stop();
          // The Phase 1/3 seam, finally connected: SPEAKING now reacts to
          // the actual waveform ORION is emitting, not to a constant.
          monitorRef.current = monitorAmplitude(
            { kind: "playback", element },
            pushAmplitude,
          );
          dispatch({ type: "speaking" });
        },
      });

      stopMonitor();
      if (current()) dispatch({ type: "settled" });
    },
    [pushAmplitude, stopMonitor],
  );

  const submit = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (text.length === 0 || busyRef.current) return;

      const { controller, turnId, current } = begin();
      busyRef.current = true;
      dispatch({ type: "submitted", prompt: text });

      void (async () => {
        try {
          const turn = await requestTextTurn(text, { signal: controller.signal });
          if (!current()) return;
          await speak(turn, controller.signal, current);
        } catch (cause) {
          stopMonitor();
          if (current()) dispatch({ type: "failed", message: messageFor(cause) });
        } finally {
          if (turnId === turnRef.current) busyRef.current = false;
        }
      })();
    },
    [begin, speak, stopMonitor],
  );

  const startListening = useCallback(() => {
    if (busyRef.current || sessionRef.current !== null) return;

    const { turnId, current } = begin();
    busyRef.current = true;
    dispatch({ type: "arming" });

    void (async () => {
      try {
        const session = await openMicrophone({
          onLimitReached: () => onLimitRef.current?.(),
        });

        if (!current()) {
          // Unmounted, or superseded, while the permission prompt was open.
          // Release the device rather than leave the indicator burning.
          session.cancel();
          return;
        }

        sessionRef.current = session;
        setCapturing(true);
        monitorRef.current = monitorAmplitude(
          { kind: "microphone", stream: session.stream },
          pushAmplitude,
        );
        // Only now is ORION genuinely listening.
        dispatch({ type: "listening" });
      } catch (cause) {
        if (current()) dispatch({ type: "failed", message: messageFor(cause) });
        if (turnId === turnRef.current) busyRef.current = false;
      }
    })();
  }, [begin, pushAmplitude]);

  const stopListening = useCallback(() => {
    const session = sessionRef.current;
    if (session === null) return;

    sessionRef.current = null;
    setCapturing(false);
    stopMonitor();

    const controller = abortRef.current ?? new AbortController();
    const turnId = turnRef.current;
    const current = () => turnId === turnRef.current && !controller.signal.aborted;

    dispatch({ type: "submitted" });

    void (async () => {
      try {
        // Resolves with the recording and releases every track, on every
        // path including failure.
        const audio = await session.stop();
        if (!current()) return;

        const turn = await requestAudioTurn(audio, { signal: controller.signal });
        if (!current()) return;
        await speak(turn, controller.signal, current);
      } catch (cause) {
        stopMonitor();
        if (current()) dispatch({ type: "failed", message: messageFor(cause) });
      } finally {
        if (turnId === turnRef.current) busyRef.current = false;
      }
    })();
  }, [speak, stopMonitor]);

  useEffect(() => {
    onLimitRef.current = stopListening;
  }, [stopListening]);

  const setVisualState = useCallback((state: OrionVisualState) => {
    dispatch({ type: "dev", state });
  }, []);

  return {
    ...snapshot,
    amplitude,
    capturing,
    submit,
    startListening,
    stopListening,
    setVisualState,
  };
}
