/**
 * The amplitude envelope: raw audio samples in, one 0..1 number out.
 *
 * DELIBERATELY NOT DSP. There is no FFT here, no band splitting, no
 * spectral flux, no onset detection and no windowing function. ORION's core
 * needs to know one thing — *how loud is it right now* — and root-mean-square
 * over a block of time-domain samples answers exactly that in six lines.
 * `AnalyserNode` will happily hand out frequency bins; using them would mean
 * choosing bands, weighting them, and defending both choices forever, for a
 * waveform whose bars are already laid out by a fixed harmonic profile
 * (`core-geometry.ts`). The brief says a smoothed RMS envelope; this is one.
 *
 * WHY dBFS AND NOT LINEAR RMS. Linear amplitude is a terrible drive signal
 * for a visual: normal speech sits around 0.02–0.1 RMS, so a bar scaled
 * linearly would barely twitch and then clip on a cough. Loudness is
 * logarithmic, so the mapping is too: a decibel window from `SILENCE_DBFS`
 * to `FULL_DBFS` is stretched across the full 0..1 range, which puts
 * conversational speech in the middle of the visual range where it belongs.
 *
 * WHY SMOOTHING IS ASYMMETRIC. A single time constant either lags the start
 * of a word (too slow) or strobes on every glottal pulse (too fast). Fast
 * attack and slow release is the standard envelope-follower shape: the core
 * responds the instant someone speaks and settles gracefully afterwards,
 * which reads as breathing rather than flickering.
 *
 * FRAME-RATE INDEPENDENT. The smoothing coefficient is derived from the real
 * elapsed milliseconds, not from a per-frame constant, so a 144 Hz display,
 * a 60 Hz display and a frame the browser dropped all produce the same
 * envelope shape in wall-clock time.
 *
 * PURE, AND THAT IS THE POINT. Nothing in this module touches the browser:
 * no `AudioContext`, no DOM, no clock of its own. It is a function of its
 * inputs, so the tests drive it with synthetic audio — silence, sine waves at
 * known amplitudes, clicks, DC offset — and assert exact numbers rather than
 * "it moved".
 */

/** Below this, treat it as silence: the envelope closes to exactly 0. */
export const SILENCE_DBFS = -62;

/** At or above this, the envelope is fully open. */
export const FULL_DBFS = -12;

/** Time constant for a rising envelope. Short: catch the start of a word. */
export const ATTACK_MS = 45;

/** Time constant for a falling envelope. Longer: settle, do not strobe. */
export const RELEASE_MS = 260;

/**
 * A frame gap longer than this is treated as this long. A backgrounded tab
 * can hand back a delta of many seconds; without a ceiling that single frame
 * would snap the envelope, and with one it merely arrives quickly. Chosen
 * comfortably above `RELEASE_MS` so the cap can only ever affect a gap that
 * is already pathological, never an ordinary one.
 */
const MAX_DELTA_MS = 500;

const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));

/**
 * Root mean square of a block of time-domain samples in -1..1.
 *
 * Non-finite samples count as zero rather than poisoning the whole block
 * with `NaN`: a single bad sample from a driver glitch must not blank the
 * visualization, and `NaN` propagating into a CSS custom property would.
 */
export function rootMeanSquare(samples: ArrayLike<number>): number {
  const count = samples.length;
  if (count === 0) return 0;

  let sum = 0;
  for (let index = 0; index < count; index += 1) {
    const sample = samples[index];
    if (sample === undefined || !Number.isFinite(sample)) continue;
    sum += sample * sample;
  }

  const mean = sum / count;
  return mean > 0 ? Math.sqrt(mean) : 0;
}

/**
 * Map an RMS level to a 0..1 envelope through the decibel window above.
 *
 * Total: every input, including 0, a negative number and `NaN`, produces a
 * number in 0..1.
 */
export function envelopeFromRms(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return 0;

  const decibels = 20 * Math.log10(rms);
  if (decibels <= SILENCE_DBFS) return 0;
  if (decibels >= FULL_DBFS) return 1;

  return (decibels - SILENCE_DBFS) / (FULL_DBFS - SILENCE_DBFS);
}

/**
 * One step of an asymmetric one-pole filter from `current` toward `target`.
 *
 * `alpha = 1 - e^(-dt/tau)` is the exact discrete solution of a first-order
 * lag over `dt`, which is what makes the result depend on elapsed time
 * rather than on how often this happens to be called.
 */
export function smoothTowards(current: number, target: number, deltaMs: number): number {
  const from = Number.isFinite(current) ? clamp01(current) : 0;
  const to = Number.isFinite(target) ? clamp01(target) : 0;
  if (!Number.isFinite(deltaMs) || deltaMs <= 0) return from;

  const elapsed = Math.min(deltaMs, MAX_DELTA_MS);
  const tau = to > from ? ATTACK_MS : RELEASE_MS;
  const alpha = clamp01(1 - Math.exp(-elapsed / tau));

  return clamp01(from + (to - from) * alpha);
}

/**
 * The stateful follower the render loop actually holds: push a block of
 * samples and the elapsed time since the last block, get the envelope.
 */
export class EnvelopeFollower {
  #value = 0;

  get value(): number {
    return this.#value;
  }

  push(samples: ArrayLike<number>, deltaMs: number): number {
    const target = envelopeFromRms(rootMeanSquare(samples));
    this.#value = smoothTowards(this.#value, target, deltaMs);
    return this.#value;
  }

  reset(): void {
    this.#value = 0;
  }
}
