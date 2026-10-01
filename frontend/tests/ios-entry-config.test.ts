/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const swift = readFileSync(new URL('../native/ios/MagistrateAppIntents.swift', import.meta.url), 'utf8');
const app = JSON.parse(readFileSync(new URL('../app.json', import.meta.url), 'utf8'));

test('iOS App Intents expose product entries through foreground allowlisted routes', () => {
  for (const intent of ['StartMagistrateIntent', 'TalkToMagistrateIntent', 'AskMagistrateIntent', 'WhatsRunningIntent', 'WhatNeedsAttentionIntent', 'MagistrateVoiceIntent']) assert.match(swift, new RegExp(`struct ${intent}`));
  assert.match(swift, /openAppWhenRun: Bool \{ true \}/);
  for (const route of [
    'magistrate:/voice?autostart=true',
    'magistrate:/chat?shortcut=running',
    'magistrate:/attention?overview=true',
  ]) assert.ok(swift.includes(route));
  assert.doesNotMatch(swift, /https?:|URLSession|AVAudio/);
});

test('OpenURLIntent dependency boundary requires iOS 18', () => {
  for (const declaration of [
    'private enum MagistrateIntentDestination',
    'protocol MagistrateOpeningIntent',
    'extension MagistrateOpeningIntent',
    'struct StartMagistrateIntent',
    'struct TalkToMagistrateIntent',
    'struct AskMagistrateIntent',
    'struct WhatsRunningIntent',
    'struct WhatNeedsAttentionIntent',
    'struct MagistrateVoiceIntent',
    'struct MagistrateAppShortcuts',
  ]) assert.ok(swift.includes(`@available(iOS 18.0, *)\n${declaration}`), `${declaration} must require iOS 18`);
  assert.match(swift, /OpenURLIntent\(Self\.destination\)/);
});

test('native configuration has microphone copy, foreground-only audio, push, and App Intents plugin', () => {
  assert.match(app.expo.ios.infoPlist.NSMicrophoneUsageDescription, /Voice Mode|voice/i);
  assert.deepEqual(app.expo.ios.infoPlist.UIBackgroundModes, ['remote-notification']);
  assert.ok(app.expo.plugins.includes('expo-notifications'));
  assert.ok(app.expo.plugins.includes('./plugins/withMagistrateAppIntents'));
  const audio = app.expo.plugins.find((value: unknown) => Array.isArray(value) && value[0] === 'expo-audio');
  assert.equal(audio[1].enableBackgroundRecording, false);
});
