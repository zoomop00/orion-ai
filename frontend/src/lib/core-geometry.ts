/**
 * Deterministic layout data for the ORION core's generated elements.
 *
 * Everything here is a pure function of an index. That is not stylistic
 * preference -- `Math.random()` in a React render would produce different
 * particle positions on the server and in the browser, and Next's hydration
 * would either warn or visibly re-shuffle the field on first paint. Integer
 * arithmetic gives the field an organic, non-repeating look while staying
 * byte-identical across renders.
 *
 * These arrays are computed once at module load, not per render.
 */

/** Golden angle in degrees: the classic phyllotaxis spacing. */
const GOLDEN_ANGLE = 137.507764;

const PARTICLE_COUNT = 26;
const WAVE_BAR_COUNT = 44;
const LATTICE_NODE_COUNT = 7;

function round(value: number, places: number): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

export interface ParticleLayout {
  /** Starting angle around the core, degrees. */
  readonly angle: number;
  /** Orbital radius as a fraction of the core stage size. */
  readonly radius: number;
  /** Diameter in fractions of the core stage size. */
  readonly size: number;
  /** Seconds for one full orbit at tempo 1. */
  readonly duration: number;
  /** Negative delay so the field is already mid-orbit on the first frame. */
  readonly delay: number;
  /** Resting opacity multiplier. */
  readonly opacity: number;
  /** -1 or 1 - roughly a third of the field counter-orbits, which reads as depth. */
  readonly direction: -1 | 1;
}

export const PARTICLES: readonly ParticleLayout[] = Array.from(
  { length: PARTICLE_COUNT },
  (_, i): ParticleLayout => {
    const spread = ((i * 37) % 100) / 100;
    const jitter = ((i * 53) % 90) / 90;
    return {
      angle: round((i * GOLDEN_ANGLE) % 360, 3),
      radius: round(0.3 + spread * 0.24, 4),
      size: round(0.004 + jitter * 0.005, 4),
      duration: round(34 - spread * 12, 2),
      delay: round(-((i * 29) % 340) / 10, 2),
      opacity: round(0.28 + jitter * 0.62, 3),
      direction: i % 3 === 0 ? -1 : 1,
    };
  },
);

export interface WaveBarLayout {
  /** Angle around the ring, degrees. Bar 0 sits at the top. */
  readonly angle: number;
  /**
   * Per-bar share of the amplitude envelope, 0..1. A fixed harmonic profile,
   * not noise: two out-of-phase sine terms give a waveform silhouette that
   * looks intentional rather than jittery.
   */
  readonly weight: number;
  /** Animation offset, seconds - staggering these makes the wave travel. */
  readonly delay: number;
}

export const WAVE_BARS: readonly WaveBarLayout[] = Array.from(
  { length: WAVE_BAR_COUNT },
  (_, i): WaveBarLayout => {
    const t = i / WAVE_BAR_COUNT;
    const harmonic =
      0.62 * Math.sin(t * Math.PI * 6) + 0.38 * Math.sin(t * Math.PI * 10 + 1.1);
    return {
      angle: round(t * 360, 3),
      weight: round(0.35 + 0.65 * ((harmonic + 1) / 2), 4),
      delay: round(-(t * 2.6), 3),
    };
  },
);

export interface LatticeNodeLayout {
  /** Position in the core stage's -50..50 coordinate space. */
  readonly x: number;
  readonly y: number;
  readonly delay: number;
}

/**
 * The "intelligence nodes" that light up while ORION is thinking. Seven points
 * on a slightly irregular ring: a perfect polygon reads as decoration, an
 * irregular one reads as a graph.
 */
export const LATTICE_NODES: readonly LatticeNodeLayout[] = Array.from(
  { length: LATTICE_NODE_COUNT },
  (_, i): LatticeNodeLayout => {
    const angle = ((i * 360) / LATTICE_NODE_COUNT + (i % 2 === 0 ? 8 : -6)) * (Math.PI / 180);
    const radius = i % 3 === 0 ? 27 : 33;
    return {
      x: round(Math.cos(angle) * radius, 3),
      y: round(Math.sin(angle) * radius, 3),
      delay: round(-(i * 0.45), 2),
    };
  },
);

/**
 * Chords between lattice nodes. Skipping by two rather than connecting
 * neighbours produces a star polygon that crosses the middle of the core,
 * which is what makes it read as a network instead of an outline.
 */
export const LATTICE_EDGES: readonly (readonly [LatticeNodeLayout, LatticeNodeLayout])[] =
  LATTICE_NODES.flatMap((node, i) => {
    const partner = LATTICE_NODES[(i + 2) % LATTICE_NODES.length];
    return partner ? [[node, partner] as const] : [];
  });
