/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { ACTIVE_MARK_SPIRAL, ACTIVE_MARK_TRIANGLE, audioEnergyScale, clampAudioPeak, waveformBarHeight } from '../src/services/VoiceVisuals.ts';

const voiceSource = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
const environmentSource = readFileSync(new URL('../src/components/EnvironmentBackground.tsx', import.meta.url), 'utf8');

test('voice renders the canonical active triangle and centered spiral in every branded layer', () => {
  assert.equal(ACTIVE_MARK_TRIANGLE, '64,112 448,112 256,444');
  assert.match(ACTIVE_MARK_SPIRAL, /^M256 226C270 226/);
  assert.match(voiceSource, /Polygon points=\{ACTIVE_MARK_TRIANGLE\}/);
  assert.match(voiceSource, /Path d=\{ACTIVE_MARK_SPIRAL\}/);
  for (const state of ['READY', 'LISTENING', 'THINKING', 'CONFIRMING', 'SPEAKING', 'ERROR']) {
    assert.match(voiceSource, new RegExp(`${state}:`));
  }
  assert.match(voiceSource, /testID="voice-state"/);
});

test('ambient energy accepts real microphone peaks but stays restrained', () => {
  assert.equal(clampAudioPeak(-1), 0);
  assert.equal(clampAudioPeak(2), 1);
  assert.equal(audioEnergyScale(0), 1);
  assert.equal(audioEnergyScale(1), 1.22);
  assert.ok(audioEnergyScale(1) < 1.3);
  assert.equal(audioEnergyScale(1, true), 1);
  assert.equal(waveformBarHeight(1, true), 46);
  assert.match(voiceSource, /amplitudeRef\.current/);
  assert.match(voiceSource, /Animated\.timing\(audioPeak/);
  assert.match(voiceSource, /audioPeak\.interpolate/);
});

test('reduced motion disables ambient and hover animation without disabling the voice loop', () => {
  assert.match(voiceSource, /if \(reducedMotion\) \{ ripple\.setValue\(0\.34\); audioPeak\.setValue\(0\); return; \}/);
  assert.match(voiceSource, /if \(!reducedMotion\) Animated\.timing\(audioPeak/);
  assert.match(voiceSource, /useNativeDriver: Platform\.OS !== 'web'/);
  assert.match(voiceSource, /if \(reducedMotion\) \{ hoverProgress\.setValue\(0\); return; \}/);
  assert.match(voiceSource, /hoverProgress\.interpolate/);
});

test('voice uses translucent branded contrast so selected scenes remain visible', () => {
  assert.match(voiceSource, /rgba\(5,7,10,0\.68\)/);
  assert.match(voiceSource, /rgba\(247,248,250,0\.58\)/);
});

test('voice uses persisted account backgrounds under its dark immersive treatment', () => {
  assert.match(voiceSource, /loadChatPreferences\(\)/);
  assert.match(voiceSource, /<EnvironmentBackground hideBottomControls voiceMode>/);
  assert.match(environmentSource, /subscribeActiveBackground/);
  assert.match(environmentSource, /voiceMode \? 0\.58/);
  assert.match(environmentSource, /voiceModeTreatment/);
});
