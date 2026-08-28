"use client";

import { useMemo, useRef, type CSSProperties } from "react";

import { useOrionTempo } from "@/hooks/use-orion-tempo";
import { LATTICE_EDGES, LATTICE_NODES, PARTICLES, WAVE_BARS } from "@/lib/core-geometry";
import { profileFor, type OrionVisualState } from "@/state/orion-visual-state";

import styles from "./OrionCore.module.css";

/**
 * React's `CSSProperties` has no index signature for custom properties, but
 * custom properties are exactly how this component talks to its stylesheet:
 * TypeScript decides the shape of the state, CSS decides what it looks like at
 * a given viewport size.
 */
type CoreStyle = CSSProperties & Record<`--${string}`, string | number>;

interface RingSpec {
  /** Diameter as a fraction of the core stage. */
  readonly size: number;
  /** Degrees of X-axis tilt - this is what turns a circle into an orbit. */
  readonly tiltX: number;
  readonly tiltZ: number;
  /** Seconds per revolution at tempo 1. */
  readonly duration: number;
  readonly reverse: boolean;
  readonly variant: "line" | "arc";
  /** Angles, in degrees, at which a node rides the ring. */
  readonly nodes: readonly number[];
}

/**
 * Four rings at deliberately unequal tilts and speeds. Equal spacing and a
 * shared rotation rate is what makes generated "sci-fi circles" look generated;
 * the mismatch is what makes this read as instrumentation.
 */
const RINGS: readonly RingSpec[] = [
  { size: 0.6, tiltX: 68, tiltZ: -22, duration: 18, reverse: false, variant: "arc", nodes: [0] },
  { size: 0.74, tiltX: 78, tiltZ: 34, duration: 27, reverse: true, variant: "line", nodes: [140] },
  { size: 0.88, tiltX: 60, tiltZ: 12, duration: 41, reverse: false, variant: "arc", nodes: [200, 316] },
  { size: 1, tiltX: 84, tiltZ: -8, duration: 63, reverse: true, variant: "line", nodes: [] },
];

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
   */
  readonly amplitude?: number | undefined;
}

export function OrionCore({ state, amplitude }: OrionCoreProps) {
  const profile = profileFor(state);
  const stageRef = useRef<HTMLDivElement>(null);

  useOrionTempo(stageRef, profile.tempo);

  const measured = amplitude !== undefined;
  const envelope = clamp01(amplitude ?? profile.amplitude);

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
   * The four generated layers depend on nothing but module constants, while
   * `amplitude` now changes up to sixty times a second. Memoising them means
   * an envelope update re-renders one element's inline style instead of
   * reconciling ~120 particles, bars, rings and lattice nodes -- which is what
   * makes driving the core from live audio affordable at all. The markup
   * itself is byte-for-byte what Phase 1 wrote.
   */
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
        {RINGS.map((ring, index) => (
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

      {particleField}
      {ringField}
      {lattice}
      {wave}

      <div className={styles.sphereWrap} aria-hidden="true">
        <div className={styles.sphere} data-testid="orion-sphere">
          <div className={styles.shimmer} />
          <div className={styles.hotspot} />
          <div className={styles.rim} />
        </div>
      </div>
    </div>
  );
}
