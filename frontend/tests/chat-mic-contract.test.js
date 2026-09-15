const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const chat = fs.readFileSync(path.join(__dirname, '../app/(tabs)/chat.tsx'), 'utf8');
const preferences = fs.readFileSync(path.join(__dirname, '../src/services/ChatPreferences.ts'), 'utf8');
const adapter = fs.readFileSync(path.join(__dirname, '../src/input/VoiceInputAdapter.ts'), 'utf8');

test('chat mic contract covers every truthful capture state and hold release guard', () => {
  for (const state of ['idle', 'requesting', 'listening', 'transcribing', 'ready', 'error']) assert.match(chat, new RegExp(`'${state}'`));
  assert.match(adapter, /Microphone permission was denied/);
  assert.match(chat, /voiceCaptureBehavior === 'hold-to-talk'/);
  assert.match(chat, /holdActiveRef\.current/);
  assert.match(chat, /capture\.cancel\(\)/);
  assert.match(chat, /voiceTranscriptBehavior === 'auto-send'/);
  assert.match(chat, /queuePrompt\(transcript, 'voice'/);
});

test('iOS composer follows keyboard frames without double-counting the home indicator', () => {
  assert.match(chat, /useSafeAreaInsets/);
  assert.match(chat, /new NativeAnimated\.Value\(0\)/);
  assert.match(chat, /keyboardWillShow/);
  assert.match(chat, /keyboardWillHide/);
  assert.match(chat, /const offset = -Math\.max\(0, keyboardHeight - safeAreaBottom\)/);
  assert.match(chat, /<NativeAnimated\.View testID="composer-dock"/);
  assert.match(chat, /behavior=\{Platform\.OS === 'android' \? 'height' : undefined\}/);
});

test('chat keeps one voice entry point and restores native/web material fallbacks', () => {
  assert.doesNotMatch(chat, /testID="chat-primary-action"/);
  assert.match(chat, /testID="inline-mic-button"/);
  assert.match(chat, /NativeGlassBlur dark=\{dark\} intensity=\{20\}/);
  assert.match(chat, /NativeGlassBlur dark=\{dark\} intensity=\{24\}/);
  assert.match(chat, /backdropFilter: `blur\(\$\{radius\}px\)`/);
});

test('voice preferences have durable keys, defaults, validation, and save functions', () => {
  assert.match(preferences, /magistrate\.voice\.capture-behavior/);
  assert.match(preferences, /magistrate\.voice\.transcript-behavior/);
  assert.match(preferences, /voiceCaptureBehavior: 'tap-to-toggle'/);
  assert.match(preferences, /voiceTranscriptBehavior: 'insert'/);
  assert.match(preferences, /saveVoiceCaptureBehavior/);
  assert.match(preferences, /saveVoiceTranscriptBehavior/);
});
