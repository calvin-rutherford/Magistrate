import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeNativeMagiMessages, reconcileCanonicalMessages } from '../src/services/CanonicalConversation';

const message = (overrides: Record<string, unknown>) => ({
  id: 'mgm_user_1',
  conversation_id: 'mgc_native_1',
  turn_id: 'mgt_native_1',
  client_message_id: 'client-native-1',
  reply_to_message_id: null,
  role: 'user',
  content: 'café 東京 🚀',
  status: 'completed',
  source: 'voice',
  sequence_index: 0,
  revision: 1,
  attachments: [],
  created_at: 1_789_000_000_000,
  updated_at: 1_789_000_000_000,
  ...overrides,
});

test('native Magi wire messages preserve Unicode, identity, and one assistant reply', () => {
  const response = '# Complete\n\n1. café\n2. 東京\n\n```ts\nconst rocket = "🚀";\n```\n';
  const canonical = normalizeNativeMagiMessages([
    message({}),
    message({
      id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: response, source: 'magi-native',
      sequence_index: 1, revision: 2,
    }),
  ]);
  assert.equal(canonical.length, 2);
  assert.equal(canonical[1].text, response);
  assert.equal(canonical[1].content_source, 'magi-native');
  const rendered = reconcileCanonicalMessages([], canonical);
  assert.equal(rendered.length, 2);
  assert.equal(rendered[1].contentSource, 'magi-native');
  assert.equal(rendered[1].text, response);
  assert.equal(rendered[1].progress, 'complete');
});

test('malformed native provenance, status, and control text fail closed', () => {
  assert.deepEqual(normalizeNativeMagiMessages([
    message({ id: 'mgm_assistant_bad', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', source: 'terminal', status: 'completed' }),
  ]), []);
  assert.deepEqual(normalizeNativeMagiMessages([
    message({ id: 'mgm_assistant_bad', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', source: 'magi-native', status: 'invented' }),
  ]), []);
  assert.deepEqual(normalizeNativeMagiMessages([
    message({ id: 'mgm_assistant_bad', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', source: 'magi-native', content: 'unsafe\u001b[31m' }),
  ]), []);
});


test('native pending and failed assistant placeholders remain truthful, empty, and retryable', () => {
  const pending = normalizeNativeMagiMessages([
    message({ status: 'completed' }),
    message({
      id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native',
      status: 'pending', sequence_index: 1,
    }),
  ]);
  assert.equal(pending.length, 2);
  assert.equal(pending[0].turn_status, 'awaiting_reply');
  assert.equal(pending[1].turn_status, 'awaiting_reply');
  assert.equal(reconcileCanonicalMessages([], pending)[1].progress, 'working');

  const failed = normalizeNativeMagiMessages([
    message({ status: 'completed' }),
    message({
      id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native',
      status: 'failed', sequence_index: 1, revision: 3,
    }),
  ]);
  assert.equal(failed.length, 2);
  assert.equal(reconcileCanonicalMessages([], failed)[1].progress, 'failed');
});
