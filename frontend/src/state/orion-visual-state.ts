/**
 * ORION's *presentation* state model.
 *
 * This is deliberately NOT a second voice state machine. The backend already
 * owns exactly one, in `src/orion/voice/schemas.py` (`VoiceState`) sequenced by
 * `src/orion/voice/state.py` (`VoiceStateMachine`), with five members:
 *
 *     IDLE -> LISTENING -> UNDERSTANDING -> ANALYZING -> SPEAKING -> IDLE
 *
 * The interface renders four, because UNDERSTANDING and ANALYZING look like the
 * same thing to a human being watching a screen: ORION is working. Collapsing
 * them into one THINKING presentation is a rendering concern, not a change to
 * the protocol.
 *
 * Nothing here validates or restricts transitions. Legality of a transition is
 * the backend machine's job and only the backend machine's job -- duplicating
 * `_ALLOWED_TRANSITIONS` here would create exactly the second, drifting state
 * graph this module exists to avoid. When Phase 2+ connects a real runtime, the
 * only new code needed is one total function at the integration boundary
 * (`VoiceState -> OrionVisualState`, with UNDERSTANDING and ANALYZING both
 * mapping to "thinking"). That function is intentionally not written yet: there
 * is no runtime to receive states from, and an unused adapter is a liability.
 */

export const ORION_VISUAL_STATES = ["idle", "listening", "thinking", "speaking"] as const;

export type OrionVisualState = (typeof ORION_VISUAL_STATES)[number];

/**
 * The presentation tuning for one state.
 *
 * Every number is a dimensionless multiplier handed to CSS as a custom
 * property, never a hardcoded pixel value -- the stylesheet decides what
 * `coreScale: 1.06` means at a given viewport size, which is what keeps the
 * whole system responsive from a 360px phone to a 4K display.
 */
export interface OrionStateProfile {
  readonly state: OrionVisualState;
  /** Short uppercase label rendered under the core. */
  readonly label: string;
  /** One calm sentence in ORION's voice. No claims, no data, no numbers. */
  readonly status: string;
  /** Multiplier on the central sphere's resting size. */
  readonly coreScale: number;
  /** 0..1 - how far the bloom/glow layers are pushed. */
  readonly glow: number;
  /**
   * Multiplier on the tempo of every looping animation in the core. 1 is the
   * resting tempo defined in CSS; the ramp between values is eased at runtime
   * by `useOrionTempo` so a state change accelerates rather than snaps.
   */
  readonly tempo: number;
  /** 0..1 - opacity/energy of the orbiting particle field. */
  readonly particles: number;
  /** 0..1 - visibility of the "intelligence lattice" node graph. */
  readonly lattice: number;
  /**
   * 0..1 envelope for the circular energy waveform.
   *
   * IMPORTANT (Phase 1 honesty): this is a fixed per-state constant, not
   * measured audio. Nothing in this prototype analyses a microphone or a TTS
   * stream, and no random number stands in for one. The waveform's *shape*
   * is a designed, deterministic travelling wave; this value only sets how far
   * it opens. Phase 4 replaces this constant with a real, per-frame amplitude
   * from `AnalyserNode` -- see `OrionCore`'s `amplitude` prop, which already
   * accepts exactly that and overrides this default.
   */
  readonly amplitude: number;
}

export const ORION_STATE_PROFILES: Readonly<Record<OrionVisualState, OrionStateProfile>> = {
  idle: {
    state: "idle",
    label: "Idle",
    status: "Standing by.",
    coreScale: 1,
    glow: 0.42,
    tempo: 1,
    particles: 0.34,
    lattice: 0.06,
    amplitude: 0,
  },
  listening: {
    state: "listening",
    label: "Listening",
    status: "I'm listening.",
    coreScale: 1.09,
    glow: 0.78,
    tempo: 1.45,
    particles: 0.62,
    lattice: 0.12,
    amplitude: 0.62,
  },
  thinking: {
    state: "thinking",
    label: "Thinking",
    status: "Working through it.",
    coreScale: 1.03,
    glow: 0.68,
    tempo: 2.35,
    particles: 0.86,
    lattice: 0.92,
    amplitude: 0.12,
  },
  speaking: {
    state: "speaking",
    label: "Speaking",
    status: "Responding.",
    coreScale: 1.14,
    glow: 1,
    tempo: 1.7,
    particles: 0.7,
    lattice: 0.2,
    amplitude: 0.95,
  },
};

/**
 * The cinematic demo order from the Mission 10 brief:
 * IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE.
 *
 * Used only by the development controls' "cycle" affordance. It describes the
 * shape of a conversational turn for demo purposes; it does not enforce it.
 */
export function nextInSequence(current: OrionVisualState): OrionVisualState {
  const index = ORION_VISUAL_STATES.indexOf(current);
  return ORION_VISUAL_STATES[(index + 1) % ORION_VISUAL_STATES.length] ?? "idle";
}

export function isOrionVisualState(value: unknown): value is OrionVisualState {
  return (
    typeof value === "string" && (ORION_VISUAL_STATES as readonly string[]).includes(value)
  );
}

export function profileFor(state: OrionVisualState): OrionStateProfile {
  return ORION_STATE_PROFILES[state];
}
