"use client";

import { useEffect, type RefObject } from "react";

/**
 * Eases every looping CSS animation inside `ref` toward a new tempo.
 *
 * Why this exists: changing `animation-duration` in CSS restarts the clock,
 * so an orbit visibly jumps when a state changes. The Web Animations API
 * exposes `playbackRate` instead, which is continuous in position -- the rings
 * keep exactly where they are and only change speed. Ramping that rate over
 * ~700ms with an ease-out curve is the difference between ORION "shifting gear"
 * and ORION stuttering.
 *
 * This uses only native browser APIs (`Element.getAnimations`,
 * `requestAnimationFrame`); it is the reason no animation library is needed.
 * Every access is feature-detected, so server rendering and jsdom (where
 * `getAnimations` does not exist) simply skip it and the CSS defaults stand.
 */
export function useOrionTempo(
  ref: RefObject<HTMLElement | null>,
  tempo: number,
  rampMs = 700,
): void {
  useEffect(() => {
    const root = ref.current;
    if (!root || typeof root.getAnimations !== "function") return;

    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Transitions are also Animations. Rate-shifting them would fight the
    // cinematic state cross-fade, so only genuine CSS keyframe loops are taken.
    const isLoopingCssAnimation = (animation: Animation): boolean =>
      typeof CSSAnimation !== "undefined" && animation instanceof CSSAnimation;

    const animations = root
      .getAnimations({ subtree: true })
      .filter(isLoopingCssAnimation);
    if (animations.length === 0) return;

    if (reduceMotion) {
      for (const animation of animations) animation.playbackRate = 1;
      return;
    }

    const start = performance.now();
    const from = animations.map((animation) => animation.playbackRate);
    let frame = 0;

    const step = (now: number): void => {
      const progress = Math.min(1, (now - start) / rampMs);
      // easeOutCubic - fast commitment to the new tempo, gentle settle.
      const eased = 1 - (1 - progress) ** 3;
      animations.forEach((animation, i) => {
        animation.playbackRate = (from[i] ?? 1) + (tempo - (from[i] ?? 1)) * eased;
      });
      if (progress < 1) frame = requestAnimationFrame(step);
    };

    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [ref, tempo, rampMs]);
}
