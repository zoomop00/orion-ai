import { describe, expect, it } from "vitest";

import {
  ORION_STATE_PROFILES,
  ORION_VISUAL_STATES,
  isOrionVisualState,
  nextInSequence,
  profileFor,
  type OrionVisualState,
} from "@/state/orion-visual-state";

describe("ORION visual state model", () => {
  it("declares exactly the four presentation states", () => {
    expect([...ORION_VISUAL_STATES]).toEqual(["idle", "listening", "thinking", "speaking"]);
    expect(new Set(ORION_VISUAL_STATES).size).toBe(ORION_VISUAL_STATES.length);
  });

  it("has a complete profile for every state and no extras", () => {
    expect(Object.keys(ORION_STATE_PROFILES).sort()).toEqual([...ORION_VISUAL_STATES].sort());
    for (const state of ORION_VISUAL_STATES) {
      const profile = profileFor(state);
      expect(profile.state).toBe(state);
      expect(profile.label.length).toBeGreaterThan(0);
      expect(profile.status.length).toBeGreaterThan(0);
    }
  });

  it("keeps every normalised tuning value inside 0..1", () => {
    for (const state of ORION_VISUAL_STATES) {
      const { glow, particles, lattice, amplitude } = profileFor(state);
      for (const value of [glow, particles, lattice, amplitude]) {
        expect(value).toBeGreaterThanOrEqual(0);
        expect(value).toBeLessThanOrEqual(1);
      }
    }
  });

  it("gives every state a positive tempo and a sane core scale", () => {
    for (const state of ORION_VISUAL_STATES) {
      const { tempo, coreScale } = profileFor(state);
      expect(tempo).toBeGreaterThan(0);
      expect(coreScale).toBeGreaterThanOrEqual(1);
      expect(coreScale).toBeLessThan(1.5);
    }
  });

  it("renders each state distinguishably from every other state", () => {
    // If two states produced identical tuning, the interface would be lying
    // about which one it is in.
    const fingerprints = ORION_VISUAL_STATES.map((state) => {
      const p = profileFor(state);
      return [p.coreScale, p.glow, p.tempo, p.particles, p.lattice, p.amplitude].join("|");
    });
    expect(new Set(fingerprints).size).toBe(ORION_VISUAL_STATES.length);
  });

  it("only opens the waveform envelope for the two voice-active states", () => {
    // Phase 1 honesty check: idle and thinking must not pretend to have audio.
    expect(profileFor("idle").amplitude).toBe(0);
    expect(profileFor("listening").amplitude).toBeGreaterThan(0.3);
    expect(profileFor("speaking").amplitude).toBeGreaterThan(0.3);
    expect(profileFor("thinking").amplitude).toBeLessThan(0.3);
  });

  it("moves thinking fastest, matching the brief's escalating orbital motion", () => {
    expect(profileFor("thinking").tempo).toBeGreaterThan(profileFor("listening").tempo);
    expect(profileFor("listening").tempo).toBeGreaterThan(profileFor("idle").tempo);
  });

  it("cycles idle -> listening -> thinking -> speaking -> idle", () => {
    let state: OrionVisualState = "idle";
    const visited: OrionVisualState[] = [];
    for (let i = 0; i < ORION_VISUAL_STATES.length; i += 1) {
      state = nextInSequence(state);
      visited.push(state);
    }
    expect(visited).toEqual(["listening", "thinking", "speaking", "idle"]);
  });

  it("guards unknown values", () => {
    for (const state of ORION_VISUAL_STATES) {
      expect(isOrionVisualState(state)).toBe(true);
    }
    for (const value of ["understanding", "analyzing", "IDLE", "", null, undefined, 3, {}]) {
      expect(isOrionVisualState(value)).toBe(false);
    }
  });
});
