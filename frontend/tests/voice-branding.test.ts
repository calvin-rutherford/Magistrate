/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { audioEnergyScale, clampAudioPeak } from '../src/services/VoiceVisuals.ts';

const voiceSource = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
const tetrahedronSource = readFileSync(new URL('../src/components/VoiceTetrahedron.tsx', import.meta.url), 'utf8');
const geometrySource = readFileSync(new URL('../src/services/VoiceTetrahedronGeometry.ts', import.meta.url), 'utf8');
const environmentSource = readFileSync(new URL('../src/components/EnvironmentBackground.tsx', import.meta.url), 'utf8');

test('voice renders real perspective-projected tetrahedral geometry rather than an image or fake CSS perspective', () => {
  assert.match(geometrySource, /TETRAHEDRON_VERTICES/);
  assert.match(geometrySource, /TETRAHEDRON_FACES/);
  assert.match(geometrySource, /const perspective = camera \/ \(camera - z\)/);
  assert.match(geometrySource, /sort\(\(left, right\) => left\.depth - right\.depth\)/);
  assert.match(tetrahedronSource, /voice-tetrahedron-face-/);
  assert.doesNotMatch(tetrahedronSource, /Image|\.png|perspective:/);
  assert.match(voiceSource, /<VoiceTetrahedron/);
  for (const state of ['READY', 'LISTENING', 'THINKING', 'SPEAKING', 'ERROR']) assert.match(voiceSource, new RegExp(`${state}:`));
});

test('tetrahedron has monochrome rest, amplitude response, spectral speaking/thinking, and fallback states', () => {
  assert.match(tetrahedronSource, /state === 'LISTENING' \? clampAudioPeak\(amplitude\) : 0/);
  assert.match(tetrahedronSource, /state === 'SPEAKING' \|\| state === 'THINKING'/);
  assert.match(tetrahedronSource, /state === 'THINKING' \? 0\.48 : 0\.78/);
  assert.match(tetrahedronSource, /voice-spectral-ripple-/);
  assert.match(tetrahedronSource, /StaticFallback/);
  assert.match(tetrahedronSource, /20fps is intentionally capped/);
});

test('no giant secondary triangle, fake target reticle, or equalizer-style bars remain', () => {
  assert.doesNotMatch(voiceSource, /stageTriangle|EnergyWaves|function Waveform|VoiceRippleField/);
  assert.doesNotMatch(voiceSource, /testID="voice-energy-waves"|testID="voice-waveform"/);
});

test('ambient energy accepts real microphone peaks but stays restrained', () => {
  assert.equal(clampAudioPeak(-1), 0);
  assert.equal(clampAudioPeak(2), 1);
  assert.equal(audioEnergyScale(0), 1);
  assert.equal(audioEnergyScale(1), 1.22);
  assert.ok(audioEnergyScale(1) < 1.3);
  assert.equal(audioEnergyScale(1, true), 1);
  assert.match(voiceSource, /amplitudeRef\.current/);
  assert.match(voiceSource, /updateAudioEnvelope\(/);
  assert.match(voiceSource, /setVisualAmplitude\(reducedMotion \? ENVELOPE_SILENCE_FLOOR/);
});

test('a test-injectable amplitude source exists for browser evidence without a real microphone', () => {
  assert.match(voiceSource, /__voiceSetTestAmplitude/);
  assert.match(voiceSource, /testAmplitudeRef\.current \?\? amplitudeRef\.current/);
  assert.match(voiceSource, /Platform\.OS !== 'web'/);
});

test('reduced motion freezes 3D and amplitude animation without disabling voice', () => {
  assert.match(tetrahedronSource, /const animated = !reducedMotion/);
  assert.match(tetrahedronSource, /const rippleCount = reducedMotion \? 1/);
  assert.match(voiceSource, /if \(reducedMotion\) \{ hoverProgress\.setValue\(0\); return; \}/);
  assert.match(voiceSource, /hoverProgress\.interpolate/);
});

test('voice is a dedicated near-black ceremonial canvas, not the selected chat scene dimmed', () => {
  assert.doesNotMatch(voiceSource, /useChatColorScheme/);
  assert.match(voiceSource, /const textColor = brand\.paper;/);
  assert.match(environmentSource, /voiceModeTreatment/);
  assert.match(environmentSource, /backgroundColor: '#000000'/);
  assert.match(environmentSource, /!preserveCanvas && !voiceMode/);
});
