/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { ACTIVE_MARK_SPIRAL, ACTIVE_MARK_TRIANGLE, audioEnergyScale, clampAudioPeak } from '../src/services/VoiceVisuals.ts';

const voiceSource = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
const environmentSource = readFileSync(new URL('../src/components/EnvironmentBackground.tsx', import.meta.url), 'utf8');

test('voice renders the canonical active triangle and centered spiral as a single spectral stroke', () => {
  assert.equal(ACTIVE_MARK_TRIANGLE, '64,112 448,112 256,444');
  assert.match(ACTIVE_MARK_SPIRAL, /^M256 226C270 226/);
  assert.match(voiceSource, /Polygon points=\{ACTIVE_MARK_TRIANGLE\}/);
  assert.match(voiceSource, /Path d=\{ACTIVE_MARK_SPIRAL\}/);
  // One gradient stroke, not three offset monochrome copies plus a white core.
  assert.match(voiceSource, /stroke="url\(#magistrateSpectralStroke\)"/);
  assert.equal((voiceSource.match(/<Polygon points=\{ACTIVE_MARK_TRIANGLE\}/g) || []).length, 1);
  assert.doesNotMatch(voiceSource, /translate\(-5 2\)|translate\(5 -2\)/);
  for (const state of ['READY', 'LISTENING', 'THINKING', 'CONFIRMING', 'SPEAKING', 'ERROR']) {
    assert.match(voiceSource, new RegExp(`${state}:`));
  }
  assert.match(voiceSource, /testID="voice-state"/);
});

test('no giant secondary triangle or equalizer-style bars remain', () => {
  assert.doesNotMatch(voiceSource, /stageTriangle/);
  assert.doesNotMatch(voiceSource, /EnergyWaves/);
  assert.doesNotMatch(voiceSource, /function Waveform/);
  assert.doesNotMatch(voiceSource, /testID="voice-energy-waves"/);
  assert.doesNotMatch(voiceSource, /testID="voice-waveform"/);
});

test('the ripple field uses broken filament arcs rather than target-like closed rings', () => {
  const rippleBlock = voiceSource.match(/const RIPPLE_FILAMENTS = \[(.*?)\];/s)?.[1] || '';
  assert.equal((rippleBlock.match(/'M/g) || []).length, 5);
  assert.doesNotMatch(rippleBlock, /Z/i);
  assert.match(voiceSource, /rotate\(8 50 50\)/);
});

test('the ripple field is the one audio-reactive layer, and the mark itself never scales with amplitude', () => {
  assert.match(voiceSource, /function VoiceRippleField/);
  assert.match(voiceSource, /testID="voice-ripple-field"/);
  assert.match(voiceSource, /ringPhaseOffset\(index\)/);
  assert.match(voiceSource, /ringSpeedScale\(index\)/);
  // ActiveMark receives only a size, never an amplitude/scale-driven prop.
  assert.match(voiceSource, /<ActiveMark size=\{markSize\} \/>/);
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
  assert.match(voiceSource, /Animated\.timing\(audioPeak/);
});

test('a test-injectable amplitude source exists for browser evidence without a real microphone', () => {
  assert.match(voiceSource, /__voiceSetTestAmplitude/);
  assert.match(voiceSource, /testAmplitudeRef\.current \?\? amplitudeRef\.current/);
  assert.match(voiceSource, /Platform\.OS !== 'web'/);
});

test('reduced motion disables ambient and hover animation without disabling the voice loop', () => {
  assert.match(voiceSource, /if \(voiceState !== 'LISTENING' \|\| reducedMotion\) audioPeak\.setValue\(0\);/);
  assert.match(voiceSource, /if \(!reducedMotion\) Animated\.timing\(audioPeak/);
  assert.match(voiceSource, /if \(reducedMotion\) \{ phases\.forEach\(phase => phase\.setValue\(0\.5\)\); return; \}/);
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
