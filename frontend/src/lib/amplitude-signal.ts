/**
 * The audio -> visual seam, as a signal instead of as React state.
 *
 * WHY THIS EXISTS. `orion-analyser.ts` reports the envelope once per
 * animation frame, which is up to sixty times a second. Routing that through
 * `useState` meant every one of those frames scheduled a React render of the
 * whole interface -- the hook, `OrionInterface`, `OrionCore`, the analysis
 * deck -- so that, at the end of it, one custom property on one element could
 * change. Everything downstream of that render was already memoised precisely
 * because the render was known to be pointless; this removes the render
 * instead of continuing to make it cheap.
 *
 * WHAT REPLACES IT. One value, one set of listeners, no framework. The
 * analyser writes; `OrionCore` subscribes once on mount and writes the value
 * straight onto its own stage element as `--envelope`. React still owns
 * everything it owned before -- which of the four visual states ORION is in,
 * and therefore the *fallback* envelope when nothing is being measured -- and
 * now owns nothing that changes per frame.
 *
 * DELIBERATELY NOT A STORE. There is no `useSyncExternalStore` here, because
 * that hook exists to bring an external value *into* a render, which is the
 * one thing this must never do. The value's only destination is a DOM node.
 *
 * `null` IS NOT `0`. `null` means nothing is being analysed, and the core
 * falls back to its fixed per-state constant; `0` means a microphone or a
 * playing element genuinely reported silence. They look different on screen
 * and they mean different things, so they stay different values.
 *
 * NOT A CLOCK. This module starts nothing, cancels nothing and never calls
 * `requestAnimationFrame`. The analyser's loop remains the single animation
 * driver in the frontend; this is only what that loop reports into.
 */

/** Notified with every distinct value the source publishes. */
export type AmplitudeListener = (envelope: number | null) => void;

/** The read side, which is all a component is given. */
export interface AmplitudeSignal {
  /** The value as of right now, or `null` when nothing is being analysed. */
  current(): number | null;
  /** Observe every subsequent value. Returns the unsubscribe. */
  subscribe(listener: AmplitudeListener): () => void;
}

/** The write side, held only by the hook that owns the analyser. */
export interface AmplitudeSource extends AmplitudeSignal {
  /** Publish a value, or `null` to hand the core back to its constant. */
  set(envelope: number | null): void;
}

export function createAmplitudeSignal(): AmplitudeSource {
  let value: number | null = null;
  const listeners = new Set<AmplitudeListener>();

  return {
    current: () => value,

    subscribe(listener: AmplitudeListener): () => void {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    set(envelope: number | null): void {
      // An unchanged value notifies nobody. Combined with the quantisation in
      // `use-orion-turn`, most frames of ordinary speech end here, having
      // touched neither the DOM nor a style recalculation.
      if (envelope === value) return;
      value = envelope;
      // Iterated directly rather than through a copy: this runs on every
      // frame that moved, and there is exactly one listener in practice.
      for (const listener of listeners) listener(envelope);
    },
  };
}
