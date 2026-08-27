"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  OrionApiError,
  messageForFailure,
  requestTextTurn,
  type OrionTurn,
} from "@/lib/orion-api";
import { OrionAudioError, messageForAudioFailure, playTurnAudio } from "@/lib/orion-audio";
import type { OrionVisualState } from "@/state/orion-visual-state";

/**
 * One turn, from keystroke to silence.
 *
 * THE LIFECYCLE IS THE REAL ONE. `thinking` lasts exactly as long as the
 * HTTP request takes; `speaking` begins only when `playTurnAudio` reports
 * that the browser has actually started playing, and ends on the element's
 * own `ended` event. There is no timer anywhere in this file, no simulated
 * duration, and no state entered to "look busy" — if ORION appears to be
 * speaking, sound is coming out.
 *
 * STILL ONE STATE MODEL. `OrionVisualState` from `@/state/orion-visual-state`
 * remains the only vocabulary; this hook sequences it, it does not extend
 * it. The four values are unchanged and `listening` is untouched, because
 * nothing in Phase 3 listens — the microphone is Phase 4.
 *
 * WHY A REDUCER. The invalid states are the interesting ones: "busy but
 * idle", "speaking with no audio", "error while a request is still in
 * flight". Expressing every transition as one function makes those
 * unreachable by construction rather than by careful ordering of `setState`
 * calls. The `dev` action is refused outright while a turn is running, so
 * the development controls cannot yank the interface out from under a live
 * turn even if the UI forgets to disable them.
 */

export interface OrionTurnSnapshot {
  readonly visualState: OrionVisualState;
  /** What ORION understood. Untrusted text — rendered as text, never HTML. */
  readonly transcript: string | null;
  /** What ORION said back. Also rendered as text only. */
  readonly responseText: string | null;
  /** A calm, frontend-authored sentence. Never a server or exception string. */
  readonly error: string | null;
  /** True from submission until playback ends or the turn fails. */
  readonly busy: boolean;
}

type TurnAction =
  | { readonly type: "submitted" }
  | { readonly type: "heard"; readonly transcript: string; readonly responseText: string }
  | { readonly type: "speaking" }
  | { readonly type: "settled" }
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "dev"; readonly state: OrionVisualState };

const INITIAL: OrionTurnSnapshot = {
  visualState: "idle",
  transcript: null,
  responseText: null,
  error: null,
  busy: false,
};

function reduce(snapshot: OrionTurnSnapshot, action: TurnAction): OrionTurnSnapshot {
  switch (action.type) {
    case "submitted":
      return {
        visualState: "thinking",
        transcript: null,
        responseText: null,
        error: null,
        busy: true,
      };
    case "heard":
      // Deliberately does NOT touch `visualState`. The response has arrived
      // but no sound has been made yet, so ORION is still thinking.
      return { ...snapshot, transcript: action.transcript, responseText: action.responseText };
    case "speaking":
      return { ...snapshot, visualState: "speaking" };
    case "settled":
      return { ...snapshot, visualState: "idle", busy: false };
    case "failed":
      // Whatever failed, the interface comes to rest and is immediately
      // usable again. The last transcript/response is cleared so a stale
      // reply cannot sit under a fresh error.
      return {
        visualState: "idle",
        transcript: null,
        responseText: null,
        error: action.message,
        busy: false,
      };
    case "dev":
      return snapshot.busy ? snapshot : { ...snapshot, visualState: action.state };
  }
}

/** Map any thrown value to a sentence this interface owns. */
function messageFor(cause: unknown): string {
  if (cause instanceof OrionApiError) return messageForFailure(cause.failure);
  if (cause instanceof OrionAudioError) return messageForAudioFailure(cause.failure);
  return "ORION could not complete that request.";
}

export interface OrionTurnController extends OrionTurnSnapshot {
  /** Runs a turn. No-ops on blank input or while a turn is already running. */
  readonly submit: (text: string) => void;
  /** The Phase 1 development state switcher. Ignored while a turn runs. */
  readonly setVisualState: (state: OrionVisualState) => void;
}

export function useOrionTurn(): OrionTurnController {
  const [snapshot, dispatch] = useReducer(reduce, INITIAL);

  // `busy` is mirrored in a ref because `submit` is a stable callback that
  // would otherwise close over a stale value. The ref is owned entirely by
  // `submit` and its `finally` — never written during render — so a second
  // submission in the same tick is refused before React has even re-rendered.
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  // Monotonic, not random: identifies which turn a late result belongs to,
  // so a superseded turn can never write over a newer one.
  const turnRef = useRef(0);

  useEffect(
    () => () => {
      // Unmount: stop the request and any audio still playing. This is
      // cleanup, not user-facing interruption.
      abortRef.current?.abort();
    },
    [],
  );

  const submit = useCallback((raw: string) => {
    const text = raw.trim();
    if (text.length === 0 || busyRef.current) return;

    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    turnRef.current += 1;
    const turnId = turnRef.current;
    const current = () => turnId === turnRef.current && !controller.signal.aborted;

    busyRef.current = true;
    dispatch({ type: "submitted" });

    void (async () => {
      try {
        const turn: OrionTurn = await requestTextTurn(text, { signal: controller.signal });
        if (!current()) return;
        dispatch({
          type: "heard",
          transcript: turn.transcript,
          responseText: turn.responseText,
        });

        await playTurnAudio(turn, {
          signal: controller.signal,
          onStart: () => {
            if (current()) dispatch({ type: "speaking" });
          },
        });

        if (current()) dispatch({ type: "settled" });
      } catch (cause) {
        if (current()) dispatch({ type: "failed", message: messageFor(cause) });
      } finally {
        if (turnId === turnRef.current) busyRef.current = false;
      }
    })();
  }, []);

  const setVisualState = useCallback((state: OrionVisualState) => {
    dispatch({ type: "dev", state });
  }, []);

  return { ...snapshot, submit, setVisualState };
}
