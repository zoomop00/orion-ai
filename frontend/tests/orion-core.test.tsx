import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { OrionCore } from "@/components/OrionCore";
import { createAmplitudeSignal } from "@/lib/amplitude-signal";
import { LATTICE_EDGES, LATTICE_NODES, PARTICLES, WAVE_BARS } from "@/lib/core-geometry";
import { ORION_VISUAL_STATES, profileFor } from "@/state/orion-visual-state";

afterEach(cleanup);

describe("OrionCore", () => {
  it("renders in all four states and tags each one on the DOM", () => {
    for (const state of ORION_VISUAL_STATES) {
      const { unmount } = render(<OrionCore state={state} />);
      expect(screen.getByTestId("orion-core").getAttribute("data-state")).toBe(state);
      unmount();
    }
  });

  it("pushes the state profile into CSS custom properties", () => {
    for (const state of ORION_VISUAL_STATES) {
      const profile = profileFor(state);
      const { unmount } = render(<OrionCore state={state} />);
      const core = screen.getByTestId("orion-core");

      expect(core.style.getPropertyValue("--core-scale")).toBe(String(profile.coreScale));
      expect(core.style.getPropertyValue("--glow")).toBe(String(profile.glow));
      expect(core.style.getPropertyValue("--particle-energy")).toBe(String(profile.particles));
      expect(core.style.getPropertyValue("--lattice-energy")).toBe(String(profile.lattice));
      expect(core.style.getPropertyValue("--envelope")).toBe(String(profile.amplitude));

      unmount();
    }
  });

  it("builds every visual layer the brief asks for", () => {
    render(<OrionCore state="thinking" />);

    expect(screen.getByTestId("orion-sphere")).toBeTruthy();
    expect(screen.getByTestId("orion-rings").children.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByTestId("orion-particles").children).toHaveLength(PARTICLES.length);
    expect(screen.getByTestId("orion-wave").children).toHaveLength(WAVE_BARS.length);
    expect(screen.getByTestId("orion-lattice").querySelectorAll("circle")).toHaveLength(
      LATTICE_NODES.length,
    );
    expect(screen.getByTestId("orion-lattice").querySelectorAll("line")).toHaveLength(
      LATTICE_EDGES.length,
    );
  });

  it("eases a state change slowly and a measured amplitude quickly", () => {
    // A 900ms glide is right when the envelope moves once because the state
    // changed, and completely wrong for a value arriving sixty times a
    // second -- it would smear a voice into a swell arriving a second late.
    const { rerender } = render(<OrionCore state="listening" />);
    const core = screen.getByTestId("orion-core");
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("900ms");

    rerender(<OrionCore state="listening" amplitude={0.4} />);
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("90ms");

    // Zero is a measured value too, not "no measurement".
    rerender(<OrionCore state="listening" amplitude={0} />);
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("90ms");
    expect(core.style.getPropertyValue("--envelope")).toBe("0");

    rerender(<OrionCore state="listening" />);
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("900ms");
  });

  it("lets a caller override the waveform envelope, clamped to 0..1", () => {
    // This is the seam Phase 4 drives with real audio amplitude.
    const { rerender } = render(<OrionCore state="idle" amplitude={0.5} />);
    expect(screen.getByTestId("orion-core").style.getPropertyValue("--envelope")).toBe("0.5");

    rerender(<OrionCore state="idle" amplitude={4} />);
    expect(screen.getByTestId("orion-core").style.getPropertyValue("--envelope")).toBe("1");

    rerender(<OrionCore state="idle" amplitude={-2} />);
    expect(screen.getByTestId("orion-core").style.getPropertyValue("--envelope")).toBe("0");
  });

  it("re-renders only the envelope when live audio drives it, frame after frame", () => {
    // Phase 4 updates `amplitude` up to sixty times a second. The generated
    // layers are memoised, so the DOM nodes must survive an envelope change
    // untouched -- if they are rebuilt every frame the particle field is
    // being reconciled at 60fps for no reason at all.
    const { rerender } = render(<OrionCore state="listening" amplitude={0.1} />);
    const core = screen.getByTestId("orion-core");
    const particles = screen.getByTestId("orion-particles");
    const bars = screen.getByTestId("orion-wave");
    const firstBar = bars.firstElementChild;

    for (const value of [0.2, 0.45, 0.9, 0.3, 0]) {
      rerender(<OrionCore state="listening" amplitude={value} />);
      expect(core.style.getPropertyValue("--envelope")).toBe(String(value));
      expect(core.getAttribute("data-envelope")).toBe(String(value));
    }

    expect(screen.getByTestId("orion-particles")).toBe(particles);
    expect(screen.getByTestId("orion-wave")).toBe(bars);
    expect(bars.firstElementChild).toBe(firstBar);
    expect(particles.children).toHaveLength(PARTICLES.length);
  });

  it("exposes the state to assistive technology instead of hiding it in pixels", () => {
    render(<OrionCore state="speaking" />);
    expect(screen.getByRole("img", { name: /speaking/i })).toBeTruthy();
  });
});

describe("a live amplitude signal, which is not a render", () => {
  /**
   * The other way into the same seam. A number prop is React's; a signal is
   * the analyser's, and the difference is the whole performance contract:
   * nothing below calls `rerender`, because a live envelope must not be a
   * render at all. See `lib/amplitude-signal.ts`.
   */
  const listening = profileFor("listening").amplitude;

  it("writes each value straight onto the stage element", () => {
    const signal = createAmplitudeSignal();
    render(<OrionCore state="listening" amplitude={signal} />);
    const core = screen.getByTestId("orion-core");

    // Nothing measured yet, so the state's own constant stands and moves at
    // the slow, state-change pace.
    expect(core.style.getPropertyValue("--envelope")).toBe(String(listening));
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("900ms");

    signal.set(0.42);
    expect(core.style.getPropertyValue("--envelope")).toBe("0.42");
    expect(core.getAttribute("data-envelope")).toBe("0.42");
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("90ms");

    // Measured silence is a measurement: still live, still quick.
    signal.set(0);
    expect(core.style.getPropertyValue("--envelope")).toBe("0");
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("90ms");

    // null is not 0 -- the analyser has stopped, so the constant returns.
    signal.set(null);
    expect(core.style.getPropertyValue("--envelope")).toBe(String(listening));
    expect(core.style.getPropertyValue("--envelope-ease")).toBe("900ms");
  });

  it("clamps whatever the signal reports", () => {
    const signal = createAmplitudeSignal();
    render(<OrionCore state="idle" amplitude={signal} />);
    const core = screen.getByTestId("orion-core");

    signal.set(4);
    expect(core.style.getPropertyValue("--envelope")).toBe("1");
    signal.set(-2);
    expect(core.style.getPropertyValue("--envelope")).toBe("0");
  });

  it("keeps a live level across a state change, and the fallback in step", () => {
    const signal = createAmplitudeSignal();
    const { rerender } = render(<OrionCore state="listening" amplitude={signal} />);
    const core = screen.getByTestId("orion-core");

    signal.set(0.8);
    rerender(<OrionCore state="speaking" amplitude={signal} />);
    // A state change must not stamp the state's constant over a level that
    // is being measured right now.
    expect(core.style.getPropertyValue("--envelope")).toBe("0.8");

    signal.set(null);
    expect(core.style.getPropertyValue("--envelope")).toBe(
      String(profileFor("speaking").amplitude),
    );
  });

  it("leaves the layers below it entirely alone", () => {
    const signal = createAmplitudeSignal();
    render(<OrionCore state="listening" amplitude={signal} />);
    const particles = screen.getByTestId("orion-particles");
    const bars = screen.getByTestId("orion-wave");
    const firstBar = bars.firstElementChild;

    for (const value of [0.2, 0.45, 0.9, 0.3, 0]) signal.set(value);

    expect(screen.getByTestId("orion-particles")).toBe(particles);
    expect(screen.getByTestId("orion-wave")).toBe(bars);
    expect(bars.firstElementChild).toBe(firstBar);
    expect(particles.children).toHaveLength(PARTICLES.length);
  });

  it("stops listening when it unmounts, so a late frame writes nothing", () => {
    const signal = createAmplitudeSignal();
    const { unmount } = render(<OrionCore state="listening" amplitude={signal} />);
    const core = screen.getByTestId("orion-core");

    signal.set(0.5);
    expect(core.style.getPropertyValue("--envelope")).toBe("0.5");

    unmount();
    signal.set(0.9);
    expect(core.style.getPropertyValue("--envelope")).toBe("0.5");
  });
});

describe("core geometry", () => {
  it("is deterministic, so server and client render the identical field", () => {
    // Pinned values, not a self-comparison: if anyone swaps the phyllotaxis
    // maths for Math.random(), hydration would desynchronise and this fails.
    expect(PARTICLES).toHaveLength(26);
    expect(PARTICLES[0]?.angle).toBe(0);
    expect(PARTICLES[1]?.angle).toBe(137.508);
    expect(PARTICLES[2]?.angle).toBe(275.016);
    expect(WAVE_BARS).toHaveLength(44);
    expect(WAVE_BARS[0]?.angle).toBe(0);
    expect(LATTICE_NODES).toHaveLength(7);
    expect(PARTICLES.every((p) => Number.isFinite(p.angle) && Number.isFinite(p.radius))).toBe(
      true,
    );
    expect(WAVE_BARS.every((b) => b.weight > 0 && b.weight <= 1)).toBe(true);
  });

  it("keeps the particle field inside the core stage", () => {
    expect(PARTICLES.every((p) => p.radius > 0 && p.radius < 0.6)).toBe(true);
  });
});
