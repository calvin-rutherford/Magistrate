/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { transitionVoiceState } from '../src/services/VoiceSessionReducer.ts';

test('voice session follows capture, review, confirmation, execution and response states', () => {
  let state = transitionVoiceState('READY', 'LISTENING');
  state = transitionVoiceState(state, 'TRANSCRIBING');
  state = transitionVoiceState(state, 'REVIEW');
  state = transitionVoiceState(state, 'RESOLVING');
  state = transitionVoiceState(state, 'CONFIRMING');
  state = transitionVoiceState(state, 'EXECUTING');
  state = transitionVoiceState(state, 'SPEAKING');
  assert.equal(transitionVoiceState(state, 'READY'), 'READY');
});

test('cancel and permission errors recover without entering chat', () => {
  assert.equal(transitionVoiceState('LISTENING', 'READY'), 'READY');
  assert.equal(transitionVoiceState('READY', 'ERROR'), 'ERROR');
  assert.equal(transitionVoiceState('ERROR', 'LISTENING'), 'LISTENING');
  assert.throws(() => transitionVoiceState('READY', 'EXECUTING'), /Invalid Voice Mode transition/);
  const source = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
  const capture = readFileSync(new URL('../src/input/VoiceInputAdapter.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /router\.push\s*\(\s*['"]\/chat/);
  assert.match(source, /submitVoiceMove\(utterance, target, key\)/);
  assert.match(capture, /Microphone permission was denied/);
});

test('voice result waits for a correlated gateway result and exposes interruption controls', () => {
  assert.equal(transitionVoiceState('EXECUTING', 'WAITING_RESULT'), 'WAITING_RESULT');
  assert.equal(transitionVoiceState('WAITING_RESULT', 'READY'), 'READY');
  const source = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
  const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');
  assert.match(source, /IMMEDIATE ACKNOWLEDGEMENT/);
  assert.match(source, /pollVoiceMove/);
  assert.match(source, /STOP SPEAKING/);
  assert.match(source, /Microphone is off until you press/);
  assert.doesNotMatch(client, /magistrate-device-token-12345/);
  assert.match(client, /Authorization: `Bearer/);
});
