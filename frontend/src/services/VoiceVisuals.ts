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

export function waveformBarHeight(sample: number, listening: boolean): number {
  return listening ? 4 + clampAudioPeak(sample) * 42 : 4;
}
