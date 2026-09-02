import { describe, expect, it, vi } from "vitest";

import { createAmplitudeSignal } from "@/lib/amplitude-signal";

/**
 * The seam the analyser writes into, on its own.
 *
 * It is four lines of state and a `Set`, and it is tested because the whole
 * performance contract rests on two of its properties: an unchanged value
 * must reach nobody, and a listener must genuinely stop being called once it
 * has unsubscribed. Both are things a frame loop would otherwise expose sixty
 * times a second.
 */
describe("the amplitude signal", () => {
  it("starts at null, meaning nothing is being analysed", () => {
    expect(createAmplitudeSignal().current()).toBeNull();
  });

  it("remembers the last value it was given", () => {
    const signal = createAmplitudeSignal();
    signal.set(0.4);
    expect(signal.current()).toBe(0.4);
    signal.set(null);
    expect(signal.current()).toBeNull();
  });

  it("notifies a subscriber with every value that actually moved", () => {
    const signal = createAmplitudeSignal();
    const seen = vi.fn();
    signal.subscribe(seen);

    signal.set(0.2);
    signal.set(0.4);
    signal.set(null);

    expect(seen.mock.calls.map(([value]) => value)).toEqual([0.2, 0.4, null]);
  });

  it("drops a repeated value rather than passing it on", () => {
    // This is the quantisation's whole payoff: during ordinary speech most
    // frames land in the step they were already in, and end here.
    const signal = createAmplitudeSignal();
    const seen = vi.fn();
    signal.subscribe(seen);

    signal.set(0.36);
    signal.set(0.36);
    signal.set(0.36);

    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("distinguishes measured silence from nothing being measured", () => {
    // 0 is a microphone reporting silence; null is no microphone at all.
    const signal = createAmplitudeSignal();
    const seen = vi.fn();
    signal.subscribe(seen);

    signal.set(0);
    signal.set(null);

    expect(seen.mock.calls.map(([value]) => value)).toEqual([0, null]);
  });

  it("stops calling a listener that has unsubscribed", () => {
    const signal = createAmplitudeSignal();
    const seen = vi.fn();
    const unsubscribe = signal.subscribe(seen);

    signal.set(0.1);
    unsubscribe();
    signal.set(0.9);

    expect(seen).toHaveBeenCalledTimes(1);
    // The value is still current; only the notification stopped.
    expect(signal.current()).toBe(0.9);
  });

  it("serves several listeners and lets each leave independently", () => {
    const signal = createAmplitudeSignal();
    const first = vi.fn();
    const second = vi.fn();
    const leave = signal.subscribe(first);
    signal.subscribe(second);

    signal.set(0.5);
    leave();
    signal.set(0.6);

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);
  });
});
