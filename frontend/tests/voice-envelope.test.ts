/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ENVELOPE_SILENCE_FLOOR,
  ringPhaseOffset,
  ringSpeedScale,
  updateAudioEnvelope,
} from '../src/services/VoiceVisuals.ts';

test('envelope rests at the silence floor, never fully flat', () => {
  assert.equal(updateAudioEnvelope(ENVELOPE_SILENCE_FLOOR, 0, 1000), ENVELOPE_SILENCE_FLOOR);
  assert.equal(updateAudioEnvelope(0, 0, 1000), ENVELOPE_SILENCE_FLOOR);
  assert.ok(updateAudioEnvelope(0, 0, 0) >= ENVELOPE_SILENCE_FLOOR);
});

test('attack is fast: a loud sample moves most of the way to target within one attack time constant', () => {
  const next = updateAudioEnvelope(ENVELOPE_SILENCE_FLOOR, 1, 90, { attackMs: 90 });
  // 1 - e^-1 ~= 0.632 of the distance from floor to 1.
  const expected = ENVELOPE_SILENCE_FLOOR + (1 - ENVELOPE_SILENCE_FLOOR) * (1 - Math.exp(-1));
  assert.ok(Math.abs(next - expected) < 1e-9, `expected ~${expected}, got ${next}`);
});

test('decay is slower than attack for the same elapsed time', () => {
  const attacked = updateAudioEnvelope(ENVELOPE_SILENCE_FLOOR, 1, 90, { attackMs: 90, decayMs: 420 });
  const decayed = updateAudioEnvelope(1, 0, 90, { attackMs: 90, decayMs: 420 });
  // Rising 90ms into a 90ms attack closes much more of the gap than falling
  // 90ms into a 420ms decay closes of its (much larger) time constant.
  const roseFraction = (attacked - ENVELOPE_SILENCE_FLOOR) / (1 - ENVELOPE_SILENCE_FLOOR);
  const fellFraction = (1 - decayed) / (1 - ENVELOPE_SILENCE_FLOOR);
  assert.ok(roseFraction > fellFraction, `attack fraction ${roseFraction} should exceed decay fraction ${fellFraction}`);
});

test('envelope converges to target given enough elapsed time, and clamps to [floor, 1]', () => {
  assert.ok(updateAudioEnvelope(ENVELOPE_SILENCE_FLOOR, 1, 100_000) > 0.999);
  assert.ok(Math.abs(updateAudioEnvelope(1, 0, 100_000) - ENVELOPE_SILENCE_FLOOR) < 1e-9);
  assert.equal(updateAudioEnvelope(2, 5, 1000), 1);
  assert.equal(updateAudioEnvelope(-5, -5, 1000), ENVELOPE_SILENCE_FLOOR);
});

test('envelope tolerates non-finite elapsed time by treating it as no time passing', () => {
  assert.equal(updateAudioEnvelope(0.5, 1, Number.NaN), 0.5);
  assert.equal(updateAudioEnvelope(0.5, 1, -50), 0.5);
});

test('ring phase/speed offsets are deterministic and spread layers apart', () => {
  const phases = [0, 1, 2, 3].map(ringPhaseOffset);
  assert.deepEqual(phases, [0, 1, 2, 3].map(ringPhaseOffset));
  const unique = new Set(phases.map(value => value.toFixed(6)));
  assert.equal(unique.size, phases.length);
  assert.ok(phases.every(value => value >= 0 && value < 1));
  const speeds = [0, 1, 2, 3].map(ringSpeedScale);
  assert.ok(new Set(speeds).size >= 3);
  assert.ok(speeds.every(value => value >= 1 && value <= 1.4));
});
