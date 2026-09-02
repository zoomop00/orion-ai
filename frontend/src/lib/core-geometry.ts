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
    /*
     * ONE NUMBER DECIDES EVERYTHING ABOUT A PARTICLE, AND IT IS ITS DEPTH.
     *
     * Size, brightness and orbital rate used to be drawn from three unrelated
     * hashes, which is exactly what makes a particle field read as dust: a
     * large faint slow mote next to a small bright fast one describes no
     * space at all. Here they are all functions of how close the orbit is to
     * the core, so the field obeys the same rules the eye already knows --
     * near things are bigger, brighter and cross faster, far things are
     * smaller, dimmer and drift. The jitter is kept, but only as a small
     * variation on top, so the field is organic without being arbitrary.
     *
     * The angles are untouched: phyllotaxis is what stops the ring from
     * showing spokes, and it is pinned by test.
     */
    const depth = 1 - spread;
    return {
      angle: round((i * GOLDEN_ANGLE) % 360, 3),
      radius: round(0.3 + spread * 0.24, 4),
      size: round(0.0031 + depth * 0.0063 + jitter * 0.0008, 4),
      duration: round(64 - depth * 27, 2),
      delay: round(-((i * 29) % 340) / 10, 2),
      // Only every fifth mote is allowed to be bright, and even then only if
      // it is near. A particle that competes with the core is noise.
      opacity: round(
        (i % 5 === 0 ? 0.32 + depth * 0.52 : 0.07 + depth * 0.19) + jitter * 0.06,
        3,
      ),
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
 * Links between lattice nodes.
 *
 * NEIGHBOURS, NOT CHORDS. Skipping by two produced a star polygon whose lines
 * crossed the middle of the core; once the aperture was tightened against the
 * sphere those lines read as a wireframe triangle laid over ORION's face,
 * which is the single most distracting thing the core has had. Joining
 * neighbours puts the graph *around* ORION instead of through it.
 *
 * It does not become an outline: the nodes alternate between two radii, so the
 * ring the links make is visibly irregular, and each link still carries its
 * own travelling signal. A perfect polygon would be decoration; a lopsided one
 * that fires is a graph.
 */
export const LATTICE_EDGES: readonly (readonly [LatticeNodeLayout, LatticeNodeLayout])[] =
  LATTICE_NODES.flatMap((node, i) => {
    const partner = LATTICE_NODES[(i + 1) % LATTICE_NODES.length];
    return partner ? [[node, partner] as const] : [];
  });

/* --------------------------------------------------------------- halo --- */

/**
 * The concentric ring system around the core.
 *
 * THIS IS THE LAYER THAT MAKES ORION A BODY RATHER THAN A DOT. Four orbital
 * rings around a sphere is a diagram of an atom; twenty-six rings of graduated
 * radius, weight, break pattern and rate is an aperture, and an aperture is
 * something that opens, measures and looks.
 *
 * THE RINGS ARE NOT EVENLY SPACED, AND THAT IS THE WHOLE POINT. Spread evenly
 * they read as a set of separate concentric circles -- a target, or a set of
 * loading spinners at different sizes. Bunched hard against the sphere and
 * thinning outward, the same rings read as ONE luminous lens the core sits
 * inside. `HALO_FALLOFF` is the exponent that does it: at 1 the rings are
 * evenly spaced, and at 1.75 nearly half of them are inside the first third of the
 * radius. That single number is the difference between a diagram and a body.
 *
 * The rings are drawn in the core's own `-50..50` coordinate space and the
 * SVG that holds them is deliberately sized well past the stage box, so the
 * ring system extends far beyond the layout footprint the core occupies. That
 * separation is what lets the visual object be more than half the width of a
 * desktop while the layout object stays the modest, well-behaved box every
 * viewport invariant was measured against.
 */
const HALO_ARC_COUNT = 22;

/** Exponent on the radius distribution. 1 is even; above 1 crowds the core. */
const HALO_FALLOFF = 1.75;

/** The sphere's own radius in this coordinate space, for reference: 18. */
const HALO_INNER = 19.5;
const HALO_OUTER = 53;

/**
 * Break patterns, expressed against `pathLength="100"` so one pattern means
 * the same proportion of arc on a ring of any radius. Without that
 * normalisation an inner ring reads as densely dashed and an outer one as
 * nearly solid, from the same numbers.
 */
/*
 * PARTIAL ARCS AND MEASURE TICKS, AND NOTHING THAT CLOSES.
 *
 * Three of these used to be a closed ring, a half-and-half split and an even
 * dash -- the three patterns that read as *drawn* rather than as *measured*.
 * A closed circle is a target; an even dash is a dotted line; a 50/50 split
 * is a progress ring. What reads as an instrument is an arc that was cut at
 * an unrepeating angle, and a fine graduated scale beside it, which is what
 * every pattern here now is: long asymmetric spans for the arcs, sub-unit
 * ticks for the scales, and no two ratios that resolve to anything simple.
 *
 * Only the equatorial rings stay unbroken, because they are the sphere's own
 * axis rather than part of the aperture.
 */
const HALO_DASHES: readonly string[] = [
  "26 11 4 59",
  "37 5 11 47",
  "21 3 6 3 29 38",
  "0.6 2.4",
  "0.5 1.7",
  "31 9 2 58",
  "13 6 3 6 13 59",
  "2 2.5 7 88.5",
  "44 8 16 32",
  "0.9 3.1",
];

export interface HaloArcLayout {
  /** Horizontal radius in the core's -50..50 space. */
  readonly rx: number;
  /** Vertical radius. Unequal on some rings, which is what tilts an orbit. */
  readonly ry: number;
  /** Hairline weight in CSS pixels - the stroke does not scale with the ring. */
  readonly width: number;
  readonly opacity: number;
  /** `stroke-dasharray`, or "none" for a closed ring. */
  readonly dash: string;
  /** Static tilt of the ring's own plane, degrees. */
  readonly tilt: number;
  /** Seconds per revolution at tempo 1. */
  readonly duration: number;
  readonly reverse: boolean;
  /** Negative offset so no two rings start from the same angle. */
  readonly delay: number;
  /**
   * 0 for the lens hugging the sphere, 1 for the working band, 2 for the
   * outer field. The three bands are what let a state change be a *gesture*
   * -- the aperture drawing in while the outer field opens out -- rather than
   * everything on screen getting brighter at once.
   */
  readonly band: 0 | 1 | 2;
  /**
   * Whether this ring turns on its own clock.
   *
   * ONLY THE INNER BAND DOES. Twenty-seven independently rotating groups was
   * measured at 27 of the 361 animations running at once, every one of them
   * re-deriving a stroke against a changing transform. The mid and outer
   * bands now turn as two groups instead, which is visually almost free to
   * give up -- a concentric circle rotating in a group looks identical to one
   * rotating alone, the only loss being that ringmates hold their dash
   * alignment. The inner band, which is what the eye actually reads, keeps its
   * own clocks.
   */
  readonly solo: boolean;
  /**
   * Very flat rings, seen almost edge-on.
   *
   * The reference's core has a bright equatorial disc cutting across it, and
   * it is most of why the sphere reads as a body with an axis rather than as
   * a circle. Three of the twenty-six are squashed to a few percent of their
   * width and carry their own brighter treatment.
   */
  readonly equatorial: boolean;
}

export const HALO_ARCS: readonly HaloArcLayout[] = Array.from(
  { length: HALO_ARC_COUNT },
  (_, i): HaloArcLayout => {
    const t = i / (HALO_ARC_COUNT - 1);
    // Crowded against the sphere, thinning outward. See HALO_FALLOFF.
    const radius = HALO_INNER + (HALO_OUTER - HALO_INNER) * t ** HALO_FALLOFF;
    const equatorial = i === 2 || i === 5 || i === 9;
    // Elliptical every fifth ring. A field of perfect circles is a target;
    // a field with a few tilted orbits in it is a system seen from an angle.
    const squash = equatorial
      ? 0.05 + (((i * 13) % 9) / 100)
      : i % 5 === 3
        ? 0.6 + (((i * 17) % 22) / 100)
        : 1;
    /*
     * Brightest right against the sphere and falling away outward, so the
     * lens has a hot core rather than a bright band floating in the middle of
     * nowhere.
     *
     * The curve matters as much as the range. Linear falloff spreads the
     * light evenly enough that every ring reads as equally important, which
     * is the single thing that makes a ring system look decorative; raised to
     * a power, the first few rings carry the light and the outer field is
     * present without asking to be read. That is the hierarchy the whole
     * composition depends on -- core, then near field, then distance.
     */
    const emphasis = Math.max(0.07, (1 - t) ** 1.4);
    const band = (t < 0.44 ? 0 : t < 0.76 ? 1 : 2) as 0 | 1 | 2;
    return {
      rx: round(radius, 3),
      ry: round(radius * squash, 3),
      width: round(0.5 + (((i * 29) % 5) / 5) * 0.75, 3),
      /*
       * The equatorial band is the sphere's own axis and is allowed to lead;
       * everything else in the aperture sits under it.
       *
       * Carried at the reference's luminosity, which is considerably higher
       * than this started at -- the ring system there is the second brightest
       * structure in the frame. Brightness rather than width is what does it:
       * see the note on `stroke-width` in the stylesheet.
       */
      opacity: round(0.04 + emphasis ** 1.15 * (equatorial ? 1 : 0.66), 3),
      dash: equatorial ? "none" : (HALO_DASHES[(i * 7) % HALO_DASHES.length] ?? "none"),
      tilt: equatorial ? round(((i * 11) % 24) - 12, 2) : round(((i * 47) % 180) - 90, 2),
      duration: round(19 + ((i * 53) % 188) / 2, 2),
      reverse: i % 3 === 1,
      delay: round(-((i * 71) % 240) / 2, 2),
      band,
      solo: band === 0 || equatorial,
      equatorial,
    };
  },
);

/* -------------------------------------------------------- wave rings --- */

export interface PulseRingLayout {
  /** Seconds for one full expansion. */
  readonly duration: number;
  readonly delay: number;
  readonly width: number;
  readonly opacity: number;
}

/**
 * Rings of energy leaving the core.
 *
 * Four, on periods that share no factor -- 7.9, 11.3, 14.7 and 19.1 seconds --
 * so two of them are never in the same place twice. Each rests for most of
 * its cycle: a ring leaving continuously is a loading spinner, and a ring
 * that leaves occasionally is something happening.
 */
export const PULSE_RINGS: readonly PulseRingLayout[] = [
  { duration: 7.9, delay: 0, width: 1.4, opacity: 0.5 },
  { duration: 11.3, delay: -3.1, width: 1, opacity: 0.38 },
  { duration: 14.7, delay: -8.6, width: 0.8, opacity: 0.3 },
  { duration: 19.1, delay: -14.2, width: 0.6, opacity: 0.22 },
];

/* ---------------------------------------------------------- orbitals --- */

export interface OrbitalRingLayout {
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
 * The true three-dimensional orbits, tilted out of the screen plane.
 *
 * These are the layer the halo is NOT: the halo is flat and reads as an
 * instrument face, these lean away from the viewer and read as paths around a
 * body. Seven rather than the original four, and three of them now reach past
 * the stage box, so the orbits and the aperture occupy the same volume
 * instead of the orbits sitting neatly inside it.
 *
 * Equal spacing and a shared rotation rate is what makes generated "sci-fi
 * circles" look generated; every mismatch here is deliberate.
 */
export const ORBITAL_RINGS: readonly OrbitalRingLayout[] = [
  { size: 0.52, tiltX: 68, tiltZ: -22, duration: 18, reverse: false, variant: "arc", nodes: [0] },
  { size: 0.66, tiltX: 78, tiltZ: 34, duration: 27, reverse: true, variant: "line", nodes: [140] },
  { size: 0.81, tiltX: 60, tiltZ: 12, duration: 41, reverse: false, variant: "arc", nodes: [200, 316] },
  { size: 0.97, tiltX: 84, tiltZ: -8, duration: 63, reverse: true, variant: "line", nodes: [] },
  { size: 1.18, tiltX: 71, tiltZ: 51, duration: 89, reverse: false, variant: "arc", nodes: [64] },
  { size: 1.42, tiltX: 81, tiltZ: -37, duration: 127, reverse: true, variant: "arc", nodes: [248] },
  { size: 1.66, tiltX: 87, tiltZ: 7, duration: 173, reverse: false, variant: "line", nodes: [] },
];
