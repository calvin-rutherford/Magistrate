/** Canonical geometry from the Magistrate active mark asset. Keep these paths in sync
 * with Branding/Magistrate_Brand_Package/01_Logos/SVG/magistrate-mark-active.svg. */
export const ACTIVE_MARK_TRIANGLE = '64,112 448,112 256,444';
export const ACTIVE_MARK_SPIRAL = 'M256 226C270 226 277 241 269 252C257 268 232 261 228 242C222 212 248 189 277 196C316 205 327 248 305 278C276 317 216 307 192 264';

export function clampAudioPeak(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/** Keep ambient energy restrained: the microphone can enlarge it, never dominate the mark. */
export function audioEnergyScale(peak: number, reducedMotion = false): number {
  return reducedMotion ? 1 : 1 + clampAudioPeak(peak) * 0.22;
}

/** Fast-attack/slow-decay envelope tuning. Restrained: speech should register
 * quickly without the field snapping instantly, and should settle gradually
 * rather than cutting off the instant the captain pauses. */
export const ENVELOPE_DEFAULT_ATTACK_MS = 90;
export const ENVELOPE_DEFAULT_DECAY_MS = 420;
/** The field never goes fully flat: a silence floor keeps the ceremonial
 * ripple field breathing even between words instead of visibly stopping. */
export const ENVELOPE_SILENCE_FLOOR = 0.03;

export interface AudioEnvelopeOptions {
  attackMs?: number;
  decayMs?: number;
  floor?: number;
}

/**
 * One deterministic step of a fast-attack/slow-decay envelope follower.
 *
 * `previous` and the returned value are both in [floor, 1]. `amplitudeSample`
 * is clamped before use, so callers can pass a raw/noisy sample directly.
 * `dtMs` is supplied by the caller (never read from a clock in here) so this
 * stays pure and unit-testable without RN/Animated or real time.
 */
export function updateAudioEnvelope(
  previous: number,
  amplitudeSample: number,
  dtMs: number,
  options: AudioEnvelopeOptions = {},
): number {
  const floor = options.floor ?? ENVELOPE_SILENCE_FLOOR;
  const clampedPrevious = Math.max(floor, clampAudioPeak(previous));
  const target = Math.max(floor, clampAudioPeak(amplitudeSample));
  const rising = target > clampedPrevious;
  const tau = Math.max(1, rising ? (options.attackMs ?? ENVELOPE_DEFAULT_ATTACK_MS) : (options.decayMs ?? ENVELOPE_DEFAULT_DECAY_MS));
  const elapsed = Number.isFinite(dtMs) ? Math.max(0, dtMs) : 0;
  const alpha = 1 - Math.exp(-elapsed / tau);
  const next = clampedPrevious + (target - clampedPrevious) * alpha;
  return Math.max(floor, clampAudioPeak(next));
}

/** One ripple layer's own phase/speed, so a field of them reads as living
 * motion rather than synchronized rings. Deterministic given `index`. */
export function ringPhaseOffset(index: number): number {
  return (index * 0.6180339887498949) % 1; // golden-ratio spacing, irrational so layers never re-sync
}

export function ringSpeedScale(index: number): number {
  return 1 + (index % 3) * 0.17;
}
