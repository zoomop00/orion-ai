/**
 * Deterministic layout data for the chamber ORION occupies.
 *
 * `core-geometry.ts` describes the object; this describes the room around it.
 * The split matters because the two are sized against completely different
 * things: everything there is a fraction of one `--stage` length, everything
 * here is a fraction of the viewport. Mixing them is what produced an earlier
 * revision's central failure -- a core that read as a graphic pasted onto a
 * page instead of a body sitting inside a space.
 *
 * The same rule as the core's geometry applies and applies harder here,
 * because there is more of it: EVERY VALUE IS A PURE FUNCTION OF AN INDEX.
 * `Math.random()` in a React render would give the server and the browser
 * different fields and Next would either warn or visibly reshuffle the whole
 * room on first paint. Integer hashes and sine sums give an organic,
 * non-repeating look while staying byte-identical across renders.
 *
 * NOTHING HERE ENCODES INFORMATION. These are ribbons, motes, columns of
 * light and an abstract graph. No node stands for a service, no edge for a
 * connection, no count for a measurement -- there is no telemetry in this
 * interface and none of this is a disguised readout. The room communicates
 * that ORION is alive and working; the words communicate everything else.
 *
 * All arrays are computed once at module load, not per render.
 */

/** Degrees to radians. */
const DEG = Math.PI / 180;

function round(value: number, places: number): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

/* ------------------------------------------------------------- ribbons --- */

/**
 * The waves that cross the room.
 *
 * These are the signature of the whole composition: long, low-frequency
 * ribbons that pass *through* the core rather than orbiting it, so ORION
 * reads as sitting inside a moving field instead of on top of a backdrop.
 *
 * WHY THEY LOOP SEAMLESSLY. Each path is drawn across two periods of a
 * 1000-unit wave and the SVG group is translated by exactly one period, so
 * the frame after the loop restarts is identical to the frame before it.
 * Every harmonic therefore has an INTEGER wavenumber -- a fractional one
 * would put a visible seam through the middle of the screen once per cycle.
 */
const RIBBON_PERIOD = 1000;
const RIBBON_SPAN = RIBBON_PERIOD * 2;
/** Vertical centre of the ribbon viewBox (`0 0 1000 400`). */
const RIBBON_MID = 200;
/**
 * Sample spacing. 25 units gives 81 points per ribbon: fine enough that the
 * polyline is indistinguishable from a curve at any viewport width, coarse
 * enough that seven ribbons cost about six kilobytes of markup rather than
 * fifty.
 */
const RIBBON_STEP = 25;

interface Harmonic {
  /** Cycles across one period. Integer, or the loop seams. */
  readonly k: number;
  readonly amp: number;
  readonly phase: number;
}

function ribbonPath(harmonics: readonly Harmonic[]): string {
  const points: string[] = [];
  for (let x = 0; x <= RIBBON_SPAN; x += RIBBON_STEP) {
    let y = RIBBON_MID;
    for (const harmonic of harmonics) {
      y += harmonic.amp * Math.sin((2 * Math.PI * harmonic.k * x) / RIBBON_PERIOD + harmonic.phase);
    }
    points.push(`${x} ${round(y, 2)}`);
  }
  return `M${points.join("L")}`;
}

export interface RibbonLayout {
  readonly d: string;
  readonly width: number;
  /**
   * Width of the companion stroke drawn under the bright one.
   *
   * This is the bloom, and it is why no ribbon needs a filter. A wide, very
   * faint stroke on the identical path reads as light spilling off the line;
   * a drop-shadow reads the same and costs a full re-rasterisation of a
   * four-thousand-unit path on every frame the group moves.
   */
  readonly glow: number;
  readonly opacity: number;
  /** Seconds to travel one period. Deliberately all coprime-ish. */
  readonly drift: number;
  /** Seconds for the slow vertical sway, on an unrelated period. */
  readonly sway: number;
  readonly reverse: boolean;
  /** Negative offset so the field is already mid-flight on the first frame. */
  readonly delay: number;
  /** 0 farthest, 2 nearest. Drives blur and weight in the stylesheet. */
  readonly depth: 0 | 1 | 2;
}

const RIBBON_SPECS: readonly {
  readonly harmonics: readonly Harmonic[];
  readonly width: number;
  readonly glow: number;
  readonly opacity: number;
  readonly drift: number;
  readonly sway: number;
  readonly reverse: boolean;
  readonly delay: number;
  readonly depth: 0 | 1 | 2;
}[] = [
  {
    harmonics: [
      { k: 1, amp: 74, phase: 0 },
      { k: 2, amp: 26, phase: 1.9 },
      { k: 5, amp: 6, phase: 0.4 },
    ],
    width: 2,
    glow: 9,
    opacity: 0.8,
    drift: 71,
    sway: 29,
    reverse: false,
    delay: -11,
    depth: 2,
  },
  {
    harmonics: [
      { k: 1, amp: 58, phase: 2.3 },
      { k: 3, amp: 19, phase: 0.7 },
      { k: 4, amp: 9, phase: 2.9 },
    ],
    width: 1.5,
    glow: 7,
    opacity: 0.68,
    drift: 89,
    sway: 37,
    reverse: true,
    delay: -37,
    depth: 2,
  },
  {
    harmonics: [
      { k: 1, amp: 92, phase: 4.1 },
      { k: 2, amp: 14, phase: 3.4 },
    ],
    width: 1.2,
    glow: 6,
    opacity: 0.55,
    drift: 113,
    sway: 43,
    reverse: false,
    delay: -64,
    depth: 1,
  },
  {
    harmonics: [
      { k: 2, amp: 44, phase: 1.1 },
      { k: 3, amp: 24, phase: 5.2 },
      { k: 7, amp: 5, phase: 1.7 },
    ],
    width: 1,
    glow: 5,
    opacity: 0.46,
    drift: 137,
    sway: 53,
    reverse: true,
    delay: -19,
    depth: 1,
  },
  {
    harmonics: [
      { k: 1, amp: 118, phase: 5.6 },
      { k: 4, amp: 11, phase: 2.2 },
    ],
    width: 0.8,
    glow: 4,
    opacity: 0.32,
    drift: 167,
    sway: 61,
    reverse: false,
    delay: -95,
    depth: 0,
  },
];

export const RIBBONS: readonly RibbonLayout[] = RIBBON_SPECS.map(
  ({ harmonics, ...rest }): RibbonLayout => ({ d: ribbonPath(harmonics), ...rest }),
);

/**
 * The two families that pass IN FRONT of the core.
 *
 * Everything else in the chamber sits behind ORION, which is right for a room
 * and wrong for a body -- with nothing ever crossing it the core reads as the
 * front layer of a flat stack. Two families in front is all it takes: the eye
 * reads the occlusion instantly.
 *
 * They are the same two the back field already carries, so the current running
 * in front of ORION is the current running behind it. The layer is masked to a
 * band on the core's own axis and is gone well above where ORION speaks.
 */
export const RIBBONS_FRONT: readonly RibbonLayout[] = [0, 1].flatMap((index) => {
  const spec = RIBBON_SPECS[index];
  if (spec === undefined) return [];
  const { harmonics, ...rest } = spec;
  return [{ ...rest, d: ribbonPath(harmonics), depth: 2 as const }];
});

/* ------------------------------------------------------------- network --- */

/** The network viewBox is `0 0 1000 600`. */
const NETWORK_CENTRE_X = 500;
const NETWORK_NODE_COUNT = 18;

/**
 * How far a node must stay clear of the middle. The core's own halo reaches
 * roughly here, and a node inside it reads as clutter stuck to ORION rather
 * than as something standing off in the room.
 */
const NETWORK_KEEPOUT_X = 215;

export interface NetworkNodeLayout {
  readonly x: number;
  readonly y: number;
  /** Circumradius of the hexagon, in viewBox units. */
  readonly size: number;
  readonly opacity: number;
  /** Seconds for the node's own slow flicker. */
  readonly duration: number;
  readonly delay: number;
  /** 0 far, 1 mid, 2 near. */
  readonly depth: 0 | 1 | 2;
}

/**
 * The intelligence field: sparse nodes standing off to either side of the
 * core, never in front of it.
 *
 * Two clusters rather than a ring, because a ring of nodes around a sphere is
 * a diagram of an atom and this is meant to read as a space with structure in
 * it. Alternating the side by index parity spreads them evenly without any
 * placement logic that could bias one half.
 *
 * BLOCKCHAIN-INSPIRED, NOT CRYPTO-BRANDED. They are hexagon outlines at a few
 * percent opacity with a point at the centre -- no chain, no cube, no coin, no
 * label, nothing gold. The geometry is a hint; the restraint is the point.
 */
const ALL_NETWORK_NODES: readonly NetworkNodeLayout[] = Array.from(
  { length: NETWORK_NODE_COUNT },
  (_, i): NetworkNodeLayout => {
    const side = i % 2 === 0 ? -1 : 1;
    const spread = ((i * 37) % 100) / 100;
    const height = ((i * 61) % 100) / 100;
    const jitter = ((i * 23) % 100) / 100;
    /*
     * Brightness follows depth, and only depth.
     *
     * Drawn from a hash, the eighteen nodes covered a continuous range of
     * brightness, which reads as eighteen unrelated marks. Tied to the three
     * depth tiers the layer already has, the same eighteen read as three
     * planes standing at three distances -- which is what a graph in a room
     * looks like, and what a scatter of hexagons does not.
     */
    const depth = (i % 3) as 0 | 1 | 2;
    return {
      x: round(NETWORK_CENTRE_X + side * (NETWORK_KEEPOUT_X + spread * 215), 2),
      y: round(70 + height * 460, 2),
      size: round(3.4 + depth * 1.9 + jitter * 2.2, 2),
      opacity: round(0.07 + depth * 0.14 + jitter * 0.16, 3),
      duration: round(6.5 + spread * 7, 2),
      delay: round(-((i * 43) % 130) / 10, 2),
      depth,
    };
  },
);

export interface NetworkEdgeLayout {
  readonly from: NetworkNodeLayout;
  readonly to: NetworkNodeLayout;
  readonly opacity: number;
  /** Whether this connection carries a travelling signal at all. */
  readonly carries: boolean;
  /** Seconds for one signal to travel the edge. */
  readonly signal: number;
  readonly delay: number;
  /** Seconds for the connection itself to fade in and out of existence. */
  readonly life: number;
  readonly lifeDelay: number;
}

/**
 * Connections, and the signals that travel them.
 *
 * Each node reaches for its two nearest neighbours inside `MAX_EDGE`, and the
 * pair is kept once rather than twice. Index skips were tried first and gave
 * a scatter of hexagons with almost nothing between them, because two nodes
 * adjacent in the array are usually nowhere near each other on screen --
 * which read as decoration rather than as a graph. Nearest-neighbour is what
 * makes the connections local, the clusters legible, and the occasional long
 * span across the room actually mean something.
 *
 * `MAX_EDGE` is why the two sides stay two sides: nothing reaches across the
 * middle, so no line is ever drawn through ORION.
 *
 * A signal is a point running the length of an edge, and connections fade in
 * and out on their own periods -- the graph is never quite the graph it was a
 * moment ago, which is most of what keeps it from looking like a diagram.
 */
const MAX_EDGE = 155;
const NEIGHBOURS_PER_NODE = 2;

/** One propagating pass over the graph, and a second on an unrelated period. */
const SIGNAL_PERIODS = [5.9, 8.3] as const;

/**
 * How many connections carry a travelling signal at all.
 *
 * Every edge used to carry one, which is a couple of dozen points of light
 * moving at once -- the whole graph lighting up rather than information moving
 * through it. Six is enough to read as traffic and few enough that the eye can
 * follow an individual signal, which is the difference between computation and
 * a screensaver. They are taken evenly along the outward rank, so the wave
 * still travels away from ORION.
 */
const SIGNAL_COUNT = 6;

interface Candidate {
  readonly from: NetworkNodeLayout;
  readonly to: NetworkNodeLayout;
  readonly i: number;
  readonly j: number;
  /** Distance of the link's midpoint from the core, for the propagation order. */
  readonly reach: number;
}

const CANDIDATES: readonly Candidate[] = (() => {
  const found: Candidate[] = [];
  const seen = new Set<string>();

  ALL_NETWORK_NODES.forEach((node, i) => {
    const nearest = ALL_NETWORK_NODES.map((other, j) => ({
      other,
      j,
      length: Math.hypot(other.x - node.x, other.y - node.y),
    }))
      .filter(({ j, length }) => j !== i && length <= MAX_EDGE)
      // Ties broken by index, so the ordering is total and the result is
      // identical on the server and in the browser.
      .sort((a, b) => a.length - b.length || a.j - b.j)
      .slice(0, NEIGHBOURS_PER_NODE);

    for (const { other, j } of nearest) {
      const key = i < j ? `${i}-${j}` : `${j}-${i}`;
      if (seen.has(key)) continue;
      seen.add(key);
      found.push({
        from: node,
        to: other,
        i,
        j,
        reach: round(
          Math.hypot((node.x + other.x) / 2 - NETWORK_CENTRE_X, (node.y + other.y) / 2 - 300),
          2,
        ),
      });
    }
  });

  return found;
})();

/**
 * The links, ordered so their signals propagate outward from ORION.
 *
 * THIS IS THE COORDINATED MOTION, AND IT IS THE POINT OF THE WHOLE LAYER.
 * Independent random delays give a graph that twinkles, which is pretty and
 * says nothing. Ranking the links by how far they are from the core and
 * staggering the delays along that rank gives a wave of signals leaving ORION
 * and travelling out through the room -- so the network is not decorating the
 * core, it is visibly carrying something away from it.
 *
 * It is a wave, not a metronome. The links alternate between two periods that
 * share no factor, so two passes are running at once and out of phase, and the
 * pair never repeats inside any span a person will watch. Nothing here is
 * synchronised; it is *sequenced*, which is the difference between a loading
 * animation and a system thinking.
 */
export const NETWORK_EDGES: readonly NetworkEdgeLayout[] = [...CANDIDATES]
  .sort((a, b) => a.reach - b.reach || a.i - b.i || a.j - b.j)
  .map((candidate, rank, all): NetworkEdgeLayout => {
    const period = SIGNAL_PERIODS[rank % SIGNAL_PERIODS.length] ?? SIGNAL_PERIODS[0];
    const progress = rank / Math.max(1, all.length - 1);
    const h = candidate.i * 29 + candidate.j * 13;
    return {
      from: candidate.from,
      to: candidate.to,
      /*
       * Brightest at the core and fading with reach, on the same rank the
       * signals propagate along. A graph of evenly lit lines is a diagram;
       * one that dims as it gets further from the thing driving it is a
       * graph being driven by that thing.
       */
      opacity: round(0.045 + (1 - progress) * 0.155 + ((h % 60) / 60) * 0.045, 3),
      carries: rank % Math.max(1, Math.round(all.length / SIGNAL_COUNT)) === 0,
      signal: period,
      // Negative, and spread across one whole period along the outward rank.
      delay: round(-progress * period, 2),
      life: round(11 + ((h * 41) % 90) / 10, 2),
      lifeDelay: round(-((h * 67) % 210) / 10, 2),
    };
  });

/**
 * The nodes that are actually part of the graph.
 *
 * A hexagon with nothing attached to it is a sticker on the background, and
 * six of them scattered round the edges of the frame was most of what made
 * this layer read as decoration rather than as structure. Only nodes that a
 * link reaches are drawn.
 */
export const NETWORK_NODES: readonly NetworkNodeLayout[] = ALL_NETWORK_NODES.filter((node) =>
  NETWORK_EDGES.some((edge) => edge.from === node || edge.to === node),
);

/* ---------------------------------------------------------------- dust --- */

const DUST_COUNT = 30;

export interface DustLayout {
  /** Position as a percentage of the chamber, so it needs no viewBox. */
  readonly x: number;
  readonly y: number;
  /** 0 far, 1 mid, 2 near -- the stylesheet turns this into size and weight. */
  readonly depth: 0 | 1 | 2;
  readonly opacity: number;
}

export interface DustPlaneLayout {
  readonly depth: 0 | 1 | 2;
  /** Seconds for the plane's own slow traverse. */
  readonly duration: number;
  readonly delay: number;
  /** How far the plane drifts, as a percentage of the chamber. */
  readonly drift: number;
}

/**
 * Three planes of dust, and the PLANE is what moves.
 *
 * Forty-four motes each drifting on their own clock was forty-four animations
 * for something the eye reads as one thing: air with depth in it. Three plane
 * transforms give the identical parallax -- near dust crossing faster than far
 * dust is exactly what depth *is* -- at a fifteenth of the cost, and the three
 * periods share no factor so the field never resets.
 */
export const DUST_PLANES: readonly DustPlaneLayout[] = [
  { depth: 0, duration: 227, delay: -41, drift: 1.1 },
  { depth: 1, duration: 163, delay: -87, drift: 2.2 },
  { depth: 2, duration: 109, delay: -19, drift: 3.6 },
];

/**
 * Far-field motes.
 *
 * Deliberately NOT a starfield: forty-four points across a whole screen is
 * about one every four hundred square pixels, which the eye reads as depth
 * rather than as sky. Three depth tiers drifting at three unrelated rates is
 * what makes the black have somewhere to be.
 */
export const DUST: readonly DustLayout[] = Array.from(
  { length: DUST_COUNT },
  (_, i): DustLayout => ({
    x: round(((i * 73) % 101) + (((i * 17) % 10) / 10), 2),
    y: round(((i * 47) % 101) + (((i * 31) % 10) / 10), 2),
    depth: (i % 3) as 0 | 1 | 2,
    opacity: round(0.1 + (((i * 19) % 55) / 100), 3),
  }),
);

/* --------------------------------------------------------------- light --- */

export interface ShaftLayout {
  /** Horizontal position as a percentage of the chamber. */
  readonly x: number;
  /** Width in viewport-width units. */
  readonly width: number;
  readonly opacity: number;
  readonly height: number;
  readonly duration: number;
  readonly delay: number;
}

/**
 * Columns of light falling through the room.
 *
 * The reference's chamber has faint vertical streaks well behind everything
 * else -- the visual grammar of a very large volume of air with something
 * bright in it. Six is enough to imply the volume; more starts to look like a
 * pattern, which is exactly what a room is not.
 */
export const SHAFTS: readonly ShaftLayout[] = Array.from(
  { length: 6 },
  (_, i): ShaftLayout => ({
    x: round(7 + ((i * 173) % 87), 2),
    width: round(0.28 + ((i * 7) % 5) * 0.16, 3),
    opacity: round(0.05 + ((i * 11) % 7) * 0.014, 3),
    height: round(52 + ((i * 29) % 40), 2),
    duration: round(38 + i * 11.7, 2),
    delay: round(-(i * 9.3), 2),
  }),
);

/* ---------------------------------------------------------------- floor --- */

export interface FloorRingLayout {
  /** Half-width as a percentage of the floor plane. */
  readonly rx: number;
  readonly ry: number;
  readonly opacity: number;
  readonly width: number;
  readonly duration: number;
  readonly delay: number;
}

/**
 * The ground ORION's light lands on.
 *
 * Concentric ellipses rather than a grid: a grid reads as a game level, and
 * the brief asks for the core to feel physically present without an obvious
 * ellipse pasted underneath it. They expand outward very slowly on unrelated
 * periods, so the floor looks like it is responding to something rather than
 * being drawn on.
 */
export const FLOOR_RINGS: readonly FloorRingLayout[] = Array.from(
  { length: 7 },
  (_, i): FloorRingLayout => ({
    rx: round(11 + i * 11.5, 2),
    ry: round(3.2 + i * 3.8, 2),
    opacity: round(0.42 - i * 0.05, 3),
    width: round(1.1 - i * 0.1, 3),
    duration: round(23 + i * 7.4, 2),
    delay: round(-(i * 5.7), 2),
  }),
);

export const GOLDEN_ANGLE_DEGREES = 137.507764;

/** Exported for the core's own use so the two modules share one constant. */
export const RADIANS_PER_DEGREE = DEG;
