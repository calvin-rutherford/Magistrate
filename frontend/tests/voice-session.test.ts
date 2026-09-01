/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { transitionVoiceState } from '../src/services/VoiceSessionReducer.ts';

test('continuous voice loop cycles listening, thinking, speaking, then listening again', () => {
  let state = transitionVoiceState('READY', 'STARTING');
  state = transitionVoiceState(state, 'LISTENING');
  state = transitionVoiceState(state, 'TRANSCRIBING');
  state = transitionVoiceState(state, 'THINKING');
  state = transitionVoiceState(state, 'SPEAKING');
  assert.equal(transitionVoiceState(state, 'STARTING'), 'STARTING');
});

test('fleet-control moves pause the loop for confirmation and resume it either way', () => {
  assert.equal(transitionVoiceState('THINKING', 'CONFIRMING'), 'CONFIRMING');
  assert.equal(transitionVoiceState('CONFIRMING', 'THINKING'), 'THINKING');
  assert.equal(transitionVoiceState('CONFIRMING', 'STARTING'), 'STARTING');
});

test('cancel and permission errors recover without entering chat', () => {
  assert.equal(transitionVoiceState('LISTENING', 'READY'), 'READY');
  assert.equal(transitionVoiceState('READY', 'ERROR'), 'ERROR');
  assert.equal(transitionVoiceState('ERROR', 'STARTING'), 'STARTING');
  assert.throws(() => transitionVoiceState('READY', 'SPEAKING'), /Invalid Voice Mode transition/);
  const source = readFileSync(new URL('../app/voice.tsx', import.meta.url), 'utf8');
  const capture = readFileSync(new URL('../src/input/VoiceInputAdapter.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /router\.push\s*\(\s*['"]\/chat/);
  // Voice Mode submits through the move endpoint and shares the captain thread;
  // the client message id is what ties its optimistic row to the canonical turn
  // the gateway records (see ../CHAT_ARCHITECTURE_FIX.md).
  assert.match(source, /submitVoiceMove\(utterance, 'captain', key, false, undefined, clientMessageId\)/);
  assert.match(source, /reconcileCanonicalMessages\(getConversationMessages\('captain'\), canonical\)/);
  assert.match(capture, /Microphone permission was denied/);
});
