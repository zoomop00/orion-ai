"use client";

import { useEffect, useMemo, useRef, type CSSProperties } from "react";

import { useOrionTempo } from "@/hooks/use-orion-tempo";
import type { AmplitudeSignal } from "@/lib/amplitude-signal";
import {
  HALO_ARCS,
  LATTICE_EDGES,
  LATTICE_NODES,
  ORBITAL_RINGS,
  PARTICLES,
  PULSE_RINGS,
  WAVE_BARS,
} from "@/lib/core-geometry";
import { profileFor, type OrionVisualState } from "@/state/orion-visual-state";

import styles from "./OrionCore.module.css";

/**
 * React's `CSSProperties` has no index signature for custom properties, but
 * custom properties are exactly how this component talks to its stylesheet:
 * TypeScript decides the shape of the state, CSS decides what it looks like at
 * a given viewport size.
 */
type CoreStyle = CSSProperties & Record<`--${string}`, string | number>;

/** The aperture's three bands, outermost drawn first so the lens sits on top. */
const HALO_BANDS = [2, 1, 0] as const;

/**
 * Seconds per revolution for each band as a whole.
 *
 * Only the mid and outer bands actually use theirs -- the inner band's rings
 * each keep their own clock, because that band is what the eye reads and it is
 * where the non-repeating quality has to live. Three periods with no common
 * factor, slowest at the outside, so the field reads as depth rather than as a
 * disc.
 */
const BAND_SPIN: Readonly<Record<0 | 1 | 2, number>> = { 0: 0, 1: 149, 2: 227 };

const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));

/** Easing for an envelope that moved because the *state* changed. */
const STATE_ENVELOPE_EASE_MS = 900;

/**
 * Easing for an envelope arriving from a live `AnalyserNode`. Short, because
 * `audio-envelope.ts` has already smoothed it; long enough to hide the step
 * between two quantised values.
 */
const LIVE_ENVELOPE_EASE_MS = 90;

export interface OrionCoreProps {
  readonly state: OrionVisualState;
  /**
   * 0..1 energy envelope for the circular waveform.
   *
   * Declared in Phase 1 and left unset until Phase 4, in which case the
   * state's own fixed constant is used. It is now driven per frame by
   * `use-orion-turn` from a real `AnalyserNode`: the user's microphone while
   * ORION is listening, ORION's own synthesized speech while it is speaking
   * (see `lib/orion-analyser.ts`). Nothing fabricates it -- when analysis is
   * unavailable the prop is simply absent and the per-state constant stands,
   * because a fake amplitude would be indistinguishable from a real one on
   * screen, which is precisely why there has never been one.
   *
   * The signature did not change to accommodate any of that, which was the
   * point of declaring the seam three phases early.
   *
   * A NUMBER IS A RENDER; A SIGNAL IS NOT. Passing a number is still exactly
   * what it was and is what every test and every static use does: React owns
   * the value and re-renders this component when it changes. Passing an
   * `AmplitudeSignal` instead says "this will change sixty times a second,
   * do not render for it" -- the component subscribes once and writes each
   * value straight onto its own stage element. Both arrive at the same
   * `--envelope`, which is why the stylesheet did not have to learn anything
   * about the difference.
   */
  readonly amplitude?: number | AmplitudeSignal | undefined;
}

/**
 * ORION itself.
 *
 * THE OBJECT IS BIGGER THAN THE BOX. `.stage` is the core's *layout*
 * footprint and it is deliberately modest -- a well-behaved square sized from
 * the viewport and from nothing else. The halo, the outer orbits, the pulse
 * rings and the scanner all overflow it, some to nearly twice its width. That
 * separation is the single most important thing in this file: an earlier
 * revision tried to make ORION dominant by growing the layout box, which
 * immediately collided with the reply and the command line and ended with a
 * core that had to shrink whenever ORION said anything. Overflowing costs
 * nothing, collides with nothing, and is what lets the ring system span more
 * than half a desktop while every viewport invariant holds exactly as it did.
 *
 * NINE LAYERS, IN DEPTH ORDER. Atmosphere and bloom; the aperture of
 * concentric rings; energy leaving as pulse rings; the orbiting particle
 * field; the true three-dimensional orbits; the intelligence lattice; the
 * scanner sweeping on an axis of its own; the audio waveform; and the sphere
 * in front of all of it. Each is a different *kind* of motion -- rotation,
 * expansion, orbit, propagation, sweep, breath -- which is why the result
 * reads as a system rather than as a lot of things spinning.
 *
 * NOTHING IS SYNCHRONISED. Halo periods run 19 to 113 seconds, orbits 18 to
 * 173, pulses 7.9/11.3/14.7/19.1, the plasma cells 19 and 29, the scanner 23
 * and 37. No two share a factor worth speaking of, so the core does not
 * present the same arrangement twice inside any span a person will watch it
 * for. That, and not brightness, is what makes it look alive while idle.
 */
export function OrionCore({ state, amplitude }: OrionCoreProps) {
  const profile = profileFor(state);
  const stageRef = useRef<HTMLDivElement>(null);

  useOrionTempo(stageRef, profile.tempo);

  /*
   * Whichever way the envelope arrived, this is the value React renders: a
   * number prop if one was given, otherwise the state's own constant. When a
   * live signal is driving instead, the constant rendered here is precisely
   * the right thing to render -- it is what the core falls back to the moment
   * the analyser stops -- and the effect below overwrites it in place while
   * sound is actually being measured.
   */
  const level = typeof amplitude === "number" ? clamp01(amplitude) : null;
  const signal = typeof amplitude === "object" ? amplitude : null;
  const measured = level !== null;
  const constant = clamp01(profile.amplitude);
  const envelope = level ?? constant;

  const stageStyle: CoreStyle = {
    "--core-scale": profile.coreScale,
    "--glow": profile.glow,
    "--particle-energy": profile.particles,
    "--lattice-energy": profile.lattice,
    "--envelope": envelope,
    // See the waveform section of the stylesheet: a state change should
    // glide, a measured amplitude arriving every frame must not.
    "--envelope-ease": measured ? `${LIVE_ENVELOPE_EASE_MS}ms` : `${STATE_ENVELOPE_EASE_MS}ms`,
  };

  /**
   * The live audio seam, and the only place in this component that writes to
   * the DOM by hand.
   *
   * WHY IT IS NOT A PROP. An `AnalyserNode` reports up to sixty times a
   * second. As a React prop that was sixty renders a second of this component
   * and of everything above it, all so that one custom property could end up
   * with a new value -- the layers below are memoised for exactly that reason,
   * which is an admission that the render had nothing to do. Subscribing once
   * and setting the property directly removes the render instead of making it
   * cheap: no reconciliation, no commit, no effect flush, and the only work
   * per frame is the style invalidation the visual actually needs.
   *
   * NOT A SECOND ANIMATION LOOP. Nothing here schedules a frame. The
   * analyser's own `requestAnimationFrame` in `lib/orion-analyser.ts` remains
   * the single driver in the frontend, and `write` is simply what it calls.
   *
   * WHY IT RE-RUNS ON `constant`. `null` means the analyser has stopped, and
   * the core must go back to the fixed envelope for whatever state it is now
   * in. Re-subscribing when that constant changes is what keeps the fallback
   * honest across a state change, and re-writing the current value on the way
   * through is what stops a state change from stamping the constant over a
   * live level.
   */
  useEffect(() => {
    const node = stageRef.current;
    if (signal === null || node === null) return;

    const write = (value: number | null): void => {
      const next = clamp01(value ?? constant);
      node.style.setProperty("--envelope", String(next));
      // A measured value must not glide: see the waveform section of the
      // stylesheet, and `audio-envelope.ts`, which has already smoothed it.
      node.style.setProperty(
        "--envelope-ease",
        `${value === null ? STATE_ENVELOPE_EASE_MS : LIVE_ENVELOPE_EASE_MS}ms`,
      );
      // Kept in step with the property so a test can read the envelope the
      // same way whether a number or a signal is driving it.
      node.dataset.envelope = String(next);
    };

    write(signal.current());
    return signal.subscribe(write);
  }, [signal, constant]);

  /**
   * The generated layers depend on nothing but module constants. A live
   * envelope no longer re-renders this component at all, so these memos are
   * now about the renders that *do* happen -- a state change, a keystroke in
   * the command line above -- rather than about surviving sixty a second.
   */
  const halo = useMemo(
    () => (
      <svg
        className={styles.halo}
        viewBox="-50 -50 100 100"
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
        focusable="false"
        data-testid="orion-halo"
      >
        {/*
          Grouped into three bands, and the grouping is the point: a state
          change can then be a *gesture* -- the lens drawing in against the
          sphere while the outer field opens out -- instead of everything on
          screen simply getting brighter at once. See the band rules in the
          stylesheet.
        */}
        {HALO_BANDS.map((band) => (
          <g
            key={band}
            className={styles.haloBand}
            data-band={band}
            /*
             * The mid and outer bands turn here, as one group each, instead of
             * every ring turning on its own clock. Twenty-seven independently
             * rotating groups was a measurable share of the frame budget and a
             * concentric circle cannot tell you which it was doing.
             */
            style={
              {
                "--spin": `${BAND_SPIN[band]}s`,
                "--direction": band === 1 ? "reverse" : "normal",
              } as CoreStyle
            }
          >
            {HALO_ARCS.filter((arc) => arc.band === band).map((arc) => (
              <g
                key={arc.rx}
                className={arc.solo ? styles.haloSpin : styles.haloStill}
                data-equatorial={arc.equatorial}
                style={
                  {
                    "--spin": `${arc.duration}s`,
                    "--offset": `${arc.delay}s`,
                    "--direction": arc.reverse ? "reverse" : "normal",
                    "--tilt": `${arc.tilt}deg`,
                  } as CoreStyle
                }
              >
                <ellipse
                  className={styles.haloArc}
                  data-equatorial={arc.equatorial}
                  cx={0}
                  cy={0}
                  rx={arc.rx}
                  ry={arc.ry}
                  /*
                   * Normalised, so one dash pattern means the same proportion
                   * of arc on a ring of any radius. Without it the inner rings
                   * read as dotted and the outer ones as solid, from identical
                   * data.
                   */
                  pathLength={100}
                  style={
                    {
                      "--weight": arc.width,
                      "--alpha": arc.opacity,
                      "--dash": arc.dash,
                    } as CoreStyle
                  }
                />
              </g>
            ))}
          </g>
        ))}
      </svg>
    ),
    [],
  );

  const pulses = useMemo(
    () => (
      <div className={styles.pulseField} data-testid="orion-pulses" aria-hidden="true">
        {PULSE_RINGS.map((ring, index) => (
          <span
            key={index}
            className={styles.pulseRing}
            style={
              {
                "--spin": `${ring.duration}s`,
                "--offset": `${ring.delay}s`,
                "--weight": `${ring.width}px`,
                "--alpha": ring.opacity,
              } as CoreStyle
            }
          />
        ))}
      </div>
    ),
    [],
  );

  const particleField = useMemo(
    () => (
      <div className={styles.particleField} data-testid="orion-particles" aria-hidden="true">
        {PARTICLES.map((particle, index) => (
          <span
            key={index}
            className={styles.particle}
            style={
              {
                "--angle": `${particle.angle}deg`,
                "--radius": particle.radius,
                "--dot": particle.size,
                "--spin": `${particle.duration}s`,
                "--offset": `${particle.delay}s`,
                "--alpha": particle.opacity,
                "--direction": particle.direction === -1 ? "reverse" : "normal",
              } as CoreStyle
            }
          />
        ))}
      </div>
    ),
    [],
  );

  const ringField = useMemo(
    () => (
      <div className={styles.ringField} data-testid="orion-rings" aria-hidden="true">
        {ORBITAL_RINGS.map((ring, index) => (
          <div
            key={index}
            className={styles.ringPlane}
            style={
              {
                "--ring-size": `${ring.size * 100}%`,
                "--tilt-x": `${ring.tiltX}deg`,
                "--tilt-z": `${ring.tiltZ}deg`,
              } as CoreStyle
            }
          >
            <div
              className={styles.ringSpin}
              style={
                {
                  "--spin": `${ring.duration}s`,
                  "--direction": ring.reverse ? "reverse" : "normal",
                } as CoreStyle
              }
            >
              <div className={ring.variant === "arc" ? styles.ringArc : styles.ringLine} />
              {ring.nodes.map((angle) => (
                <div
                  key={angle}
                  className={styles.ringArm}
                  style={{ "--angle": `${angle}deg` } as CoreStyle}
                >
                  <span className={styles.ringNode} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    ),
    [],
  );

  const lattice = useMemo(
    () => (
      <svg
        className={styles.lattice}
        viewBox="-50 -50 100 100"
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
        focusable="false"
        data-testid="orion-lattice"
      >
        <g className={styles.latticeSpin}>
          {LATTICE_EDGES.map(([from, to], index) => (
            <line
              key={index}
              className={styles.latticeEdge}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
            />
          ))}
          {LATTICE_NODES.map((node, index) => (
            <circle
              key={index}
              className={styles.latticeNode}
              cx={node.x}
              cy={node.y}
              r={1.15}
              style={{ animationDelay: `${node.delay}s` }}
            />
          ))}
        </g>
      </svg>
    ),
    [],
  );

  const wave = useMemo(
    () => (
      <div className={styles.wave} data-testid="orion-wave" aria-hidden="true">
        {WAVE_BARS.map((bar, index) => (
          <span
            key={index}
            className={styles.barSlot}
            style={
              {
                "--angle": `${bar.angle}deg`,
                "--weight": bar.weight,
              } as CoreStyle
            }
          >
            <span className={styles.bar} style={{ animationDelay: `${bar.delay}s` }} />
          </span>
        ))}
      </div>
    ),
    [],
  );

  return (
    <div
      ref={stageRef}
      className={styles.stage}
      data-state={state}
      data-envelope={envelope}
      data-testid="orion-core"
      role="img"
      aria-label={`ORION core, ${profile.label.toLowerCase()}`}
      style={stageStyle}
    >
      <div className={styles.aura} aria-hidden="true" />
      <div className={styles.bloom} aria-hidden="true" />

      {halo}
      {pulses}
      {particleField}
      {ringField}
      {lattice}

      {/*
        The scanner: one slow sweep around a tilted axis unrelated to every
        other rotation here, and a ring scan that fires rarely rather than
        continuously. A sweep that never stops is a loading indicator; one
        that rests is something looking.
      */}
      <div className={styles.scanner} aria-hidden="true">
        <div className={styles.scanSweep} />
        <div className={styles.scanRing} />
      </div>

      {wave}

      <div className={styles.sphereWrap} aria-hidden="true">
        <div className={styles.sphere} data-testid="orion-sphere">
          {/*
            Filaments radiating from the hot centre, clipped by the sphere's
            own overflow. This is the difference between a lit ball and a star
            seen through glass -- the reference's core is visibly *generating*
            light from a structured centre, and a radial gradient alone can
            only ever be smooth.
          */}
          <div className={styles.corona} />
          <div className={styles.shimmer} />
          <div className={styles.hotspot} />
          <div className={styles.rim} />
        </div>
      </div>
    </div>
  );
}
