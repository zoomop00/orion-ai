// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  ATTACK_MS,
  EnvelopeFollower,
  FULL_DBFS,
  RELEASE_MS,
  SILENCE_DBFS,
  envelopeFromRms,
  rootMeanSquare,
  smoothTowards,
} from "@/lib/audio-envelope";

/**
 * The envelope, tested against synthetic audio with arithmetic answers.
 *
 * Every signal here is generated from a formula, so every assertion is an
 * exact number rather than "it went up a bit": a full-scale sine has an RMS
 * of exactly 1/sqrt(2), a one-pole filter stepped by exactly its own time
 * constant covers exactly 1 - 1/e of the remaining distance, and the decibel
 * window's midpoint maps to exactly 0.5. If the mapping is ever retuned,
 * these fail loudly and on purpose — the visual reads as honest only while
 * the numbers behind it are.
 *
 * Node environment: this module touches no browser API at all, which is the
 * property that makes it testable this precisely in the first place.
 */

const BLOCK = 1024;

/** A sine of `amplitude`, over a whole number of cycles so RMS is exact. */
function sine(amplitude: number, cycles = 8, length = BLOCK): Float32Array {
  const samples = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    samples[index] = amplitude * Math.sin((2 * Math.PI * cycles * index) / length);
  }
  return samples;
}

function constant(value: number, length = BLOCK): Float32Array {
  return new Float32Array(length).fill(value);
}

/** The RMS whose level in dBFS is exactly `decibels`. */
const rmsAt = (decibels: number): number => 10 ** (decibels / 20);

describe("rootMeanSquare", () => {
  it("is zero for an empty block and for digital silence", () => {
    expect(rootMeanSquare(new Float32Array(0))).toBe(0);
    expect(rootMeanSquare(constant(0))).toBe(0);
  });

  it("is the magnitude of a DC signal, regardless of sign", () => {
    expect(rootMeanSquare(constant(0.5))).toBeCloseTo(0.5, 12);
    expect(rootMeanSquare(constant(-0.5))).toBeCloseTo(0.5, 12);
  });

  it("is 1/sqrt(2) for a full-scale sine, as the arithmetic requires", () => {
    expect(rootMeanSquare(sine(1))).toBeCloseTo(Math.SQRT1_2, 6);
  });

  it("scales linearly with amplitude", () => {
    const loud = rootMeanSquare(sine(1));
    const quiet = rootMeanSquare(sine(0.5));
    expect(quiet).toBeCloseTo(loud / 2, 6);
  });

  it("ignores non-finite samples instead of poisoning the whole block", () => {
    // One bad sample from a driver glitch must not blank the visualization,
    // which is exactly what a propagating NaN in a CSS variable would do.
    const glitched = sine(1);
    glitched[0] = Number.NaN;
    glitched[1] = Number.POSITIVE_INFINITY;
    glitched[2] = Number.NEGATIVE_INFINITY;

    const value = rootMeanSquare(glitched);
    expect(Number.isFinite(value)).toBe(true);
    expect(value).toBeCloseTo(Math.SQRT1_2, 3);
  });

  it("is never negative and never NaN, for any block", () => {
    for (const block of [constant(0), constant(1), constant(-1), sine(0.001), sine(1, 3)]) {
      const value = rootMeanSquare(block);
      expect(Number.isFinite(value)).toBe(true);
      expect(value).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("envelopeFromRms", () => {
  it("closes completely at and below the silence floor", () => {
    expect(envelopeFromRms(0)).toBe(0);
    expect(envelopeFromRms(rmsAt(SILENCE_DBFS))).toBe(0);
    expect(envelopeFromRms(rmsAt(SILENCE_DBFS - 20))).toBe(0);
  });

  it("opens completely at and above the ceiling", () => {
    expect(envelopeFromRms(rmsAt(FULL_DBFS))).toBe(1);
    expect(envelopeFromRms(1)).toBe(1);
    expect(envelopeFromRms(50)).toBe(1);
  });

  it("puts the midpoint of the decibel window at exactly half open", () => {
    const midpoint = (SILENCE_DBFS + FULL_DBFS) / 2;
    expect(envelopeFromRms(rmsAt(midpoint))).toBeCloseTo(0.5, 10);
  });

  it("refuses to produce anything outside 0..1, including for hostile input", () => {
    for (const value of [-1, -0, Number.NaN, Number.POSITIVE_INFINITY, 1e12, 1e-12]) {
      const envelope = envelopeFromRms(value);
      expect(Number.isFinite(envelope)).toBe(true);
      expect(envelope).toBeGreaterThanOrEqual(0);
      expect(envelope).toBeLessThanOrEqual(1);
    }
  });

  it("never decreases as the signal gets louder", () => {
    let previous = -1;
    for (let decibels = -90; decibels <= 0; decibels += 1) {
      const envelope = envelopeFromRms(rmsAt(decibels));
      expect(envelope).toBeGreaterThanOrEqual(previous);
      previous = envelope;
    }
  });

  it("places ordinary speech in the usable middle of the range", () => {
    // Conversational speech sits around -30 dBFS. A linear mapping would put
    // it at ~0.03 and the core would barely move; this is the whole reason
    // the window is logarithmic.
    const speech = envelopeFromRms(rmsAt(-30));
    expect(speech).toBeGreaterThan(0.4);
    expect(speech).toBeLessThan(0.8);
  });
});

describe("smoothTowards", () => {
  it("does nothing when no time has passed", () => {
    expect(smoothTowards(0.25, 1, 0)).toBe(0.25);
    expect(smoothTowards(0.25, 1, -5)).toBe(0.25);
    expect(smoothTowards(0.25, 1, Number.NaN)).toBe(0.25);
  });

  it("covers exactly 1 - 1/e of the distance in one time constant", () => {
    expect(smoothTowards(0, 1, ATTACK_MS)).toBeCloseTo(1 - Math.exp(-1), 12);
    expect(smoothTowards(1, 0, RELEASE_MS)).toBeCloseTo(Math.exp(-1), 12);
  });

  it("attacks faster than it releases, which is what stops it strobing", () => {
    const rise = smoothTowards(0.5, 1, 16) - 0.5;
    const fall = 0.5 - smoothTowards(0.5, 0, 16);
    expect(rise).toBeGreaterThan(fall);
  });

  it("is frame-rate independent: two short steps equal one long one", () => {
    const twice = smoothTowards(smoothTowards(0, 1, 16), 1, 16);
    const once = smoothTowards(0, 1, 32);
    expect(twice).toBeCloseTo(once, 12);
  });

  it("clamps an enormous gap instead of snapping, so a backgrounded tab eases in", () => {
    const resumed = smoothTowards(0, 1, 60_000);
    expect(resumed).toBeGreaterThan(0.99);
    expect(resumed).toBeLessThan(1);
  });

  it("stays inside 0..1 even when handed values that are not", () => {
    expect(smoothTowards(-3, 9, 1000)).toBeLessThanOrEqual(1);
    expect(smoothTowards(-3, 9, 1000)).toBeGreaterThanOrEqual(0);
    expect(smoothTowards(Number.NaN, Number.NaN, 16)).toBe(0);
  });
});

describe("EnvelopeFollower", () => {
  const frames = (follower: EnvelopeFollower, block: Float32Array, count: number): number => {
    let value = follower.value;
    for (let index = 0; index < count; index += 1) value = follower.push(block, 16);
    return value;
  };

  it("starts closed", () => {
    expect(new EnvelopeFollower().value).toBe(0);
  });

  it("opens on a sustained full-scale tone and settles again on silence", () => {
    const follower = new EnvelopeFollower();

    // ~0.4s of a loud tone at 60fps.
    expect(frames(follower, sine(1), 25)).toBeGreaterThan(0.99);
    // ~1.6s of silence afterwards.
    expect(frames(follower, constant(0), 100)).toBeLessThan(0.01);
  });

  it("tracks quieter speech to a lower level than a loud tone", () => {
    const loud = new EnvelopeFollower();
    const quiet = new EnvelopeFollower();

    frames(loud, sine(1), 40);
    frames(quiet, sine(0.02), 40);

    expect(quiet.value).toBeLessThan(loud.value);
    expect(quiet.value).toBeGreaterThan(0);
  });

  it("reacts to the start of a word faster than it lets go of the end of one", () => {
    const follower = new EnvelopeFollower();
    const rising = follower.push(sine(1), 30);

    const falling = new EnvelopeFollower();
    frames(falling, sine(1), 40);
    const before = falling.value;
    const after = falling.push(constant(0), 30);

    expect(rising).toBeGreaterThan(before - after);
  });

  it("never leaves 0..1, whatever the block contains", () => {
    const follower = new EnvelopeFollower();
    const hostile = new Float32Array(BLOCK).fill(Number.NaN);

    for (const block of [hostile, constant(1e9), constant(-1e9), sine(1), new Float32Array(0)]) {
      const value = follower.push(block, 16);
      expect(Number.isFinite(value)).toBe(true);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
  });

  it("returns to zero on reset", () => {
    const follower = new EnvelopeFollower();
    frames(follower, sine(1), 20);
    expect(follower.value).toBeGreaterThan(0);

    follower.reset();
    expect(follower.value).toBe(0);
  });
});
