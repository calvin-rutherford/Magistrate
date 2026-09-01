import assert from 'node:assert/strict';
import test from 'node:test';
import { capabilityFor, resolveVoiceInputMode, VoiceInputCapabilities } from '../src/services/VoiceInputModes';

const capabilities: VoiceInputCapabilities = {
  modes: [
    { id: 'automatic', label: 'Automatic', available: 'available' },
    { id: 'browser', label: 'Browser speech', available: 'unavailable', reason: 'Browser speech is not installed.' },
    { id: 'native', label: 'Native device', available: 'unavailable', reason: 'Native build required.' },
    { id: 'openai', label: 'Gateway OpenAI', available: 'available' },
  ],
};

test('capability reporting identifies unsupported providers without pretending they work', () => {
  assert.equal(capabilityFor(capabilities, 'browser').available, 'unavailable');
  assert.match(capabilityFor(capabilities, 'browser').reason || '', /not installed/);
  assert.equal(capabilityFor(capabilities, 'openai').available, 'available');
});

test('selected unavailable mode resolves to Automatic with an explicit fallback reason', () => {
  assert.deepEqual(resolveVoiceInputMode('browser', capabilities), {
    mode: 'automatic', fallbackReason: 'Browser speech is unavailable. Using Automatic instead.',
  });
});

test('unknown gateway capability preserves the explicit choice while the operation remains truthful', () => {
  const unknown: VoiceInputCapabilities = { modes: [
    { id: 'automatic', label: 'Automatic', available: 'unknown' },
    { id: 'openai', label: 'Gateway OpenAI', available: 'unknown' },
  ] };
  assert.deepEqual(resolveVoiceInputMode('automatic', unknown), { mode: 'automatic' });
  assert.deepEqual(resolveVoiceInputMode('openai', unknown), { mode: 'openai' });
});
