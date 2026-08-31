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
  assert.match(chat, /submitPrompt\(combined/);
});

test('voice preferences have durable keys, defaults, validation, and save functions', () => {
  assert.match(preferences, /magistrate\.voice\.capture-behavior/);
  assert.match(preferences, /magistrate\.voice\.transcript-behavior/);
  assert.match(preferences, /voiceCaptureBehavior: 'tap-to-toggle'/);
  assert.match(preferences, /voiceTranscriptBehavior: 'insert'/);
  assert.match(preferences, /saveVoiceCaptureBehavior/);
  assert.match(preferences, /saveVoiceTranscriptBehavior/);
});
