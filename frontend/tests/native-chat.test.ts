import assert from 'node:assert/strict';
import test from 'node:test';

import {
  hasMagiReconciliationConflict, normalizeMagiMessageRecords, reconcileMagiMessages,
} from '../src/services/MagiConversation';
import type { MagiMessage } from '../src/services/MagiConversationSession';

const message = (overrides: Record<string, unknown> = {}) => ({
  id: 'mgm_user_1', conversation_id: 'mgc_native_1', turn_id: 'mgt_native_1',
  client_message_id: 'client-native-1', reply_to_message_id: null, role: 'user',
  content: 'café 東京 🚀', status: 'completed', source: 'voice', sequence_index: 0,
  revision: 1, attachments: [], created_at: 1_789_000_000_000,
  updated_at: 1_789_000_000_000, ...overrides,
});

test('provider-native records replace the matching optimistic user by canonical identity', () => {
  const optimistic: MagiMessage = {
    id: 'client-native-1', role: 'user', text: 'café 東京 🚀', source: 'voice',
    delivery: 'sending', progress: 'working', sentAt: 1_789_000_000_100,
  };
  const rendered = reconcileMagiMessages([optimistic], normalizeMagiMessageRecords([message()]));
  assert.equal(rendered.length, 1);
  assert.equal(rendered[0].id, optimistic.id);
  assert.equal(rendered[0].serverId, 'mgm_user_1');
  assert.equal(rendered[0].delivery, 'sent');

  const conflicting = reconcileMagiMessages([
    { ...optimistic, text: 'different local request' },
  ], normalizeMagiMessageRecords([message()]));
  assert.equal(conflicting[0].serverId, undefined);
  assert.equal(conflicting[0].text, 'different local request');
});

test('provider-native records preserve exact Unicode and stable identities', () => {
  const response = '# Complete\n\n1. café\n2. 東京\n\n```ts\nconst rocket = "🚀";\n```\n';
  const records = normalizeMagiMessageRecords([
    message(),
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: response, source: 'magi-native',
      sequence_index: 1, revision: 2 }),
  ]);
  assert.equal(records.length, 2);
  assert.equal(records[1].content, response);
  const rendered = reconcileMagiMessages([], records);
  assert.deepEqual(rendered.map(row => row.id), ['client-native-1', 'mgm_assistant_1']);
  assert.equal(rendered[1].text, response);
  assert.equal(rendered[1].progress, 'complete');
});

test('verified completion evidence can append an assistant-only native row', () => {
  const records = normalizeMagiMessageRecords([
    message(),
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: 'Objective accepted.',
      source: 'magi-native', sequence_index: 1 }),
    message({ id: 'mgm_completion_1', turn_id: 'mgt_completion_1', role: 'assistant',
      client_message_id: null, reply_to_message_id: 'mgm_user_1',
      content: 'The objective is complete and verified.', source: 'magi-native', sequence_index: 2 }),
  ]);
  const rendered = reconcileMagiMessages([], records);
  assert.equal(rendered.filter(row => row.role === 'user').length, 1);
  assert.equal(rendered[2].text, 'The objective is complete and verified.');
});

test('unrecognised provenance, status, identity, controls, and unknown fields fail closed', () => {
  const attachment = {
    id: 'upload_1234567890', upload_id: 'upload_1234567890', name: 'notes.txt',
    media_type: 'text/plain', size: 5, url: '/api/v1/uploads/upload_1234567890',
  };
  for (const invalid of [
    message({ id: 'mgm_assistant_bad', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', source: 'other-provider' }),
    message({ status: 'invented' }),
    message({ content: 'unsafe\u001b[31m' }),
    message({ conversation_id: 'another-thread' }),
    message({ attachments: [attachment, attachment] }),
    message({ id: 'mgm_assistant_bad', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', source: 'magi-native', status: 'failed', content: 'spoofed failure prose' }),
    message({ unexpected: 'field' }),
  ]) assert.deepEqual(normalizeMagiMessageRecords([invalid]), []);
});

test('pending, failed, and cancelled pairs map to truthful render state', () => {
  const pair = (status: string) => normalizeMagiMessageRecords([
    message(),
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native',
      status, sequence_index: 1 }),
  ]);
  assert.equal(reconcileMagiMessages([], pair('pending'))[0].progress, 'working');
  assert.equal(reconcileMagiMessages([], pair('failed'))[0].delivery, 'failed');
  assert.equal(reconcileMagiMessages([], pair('cancelled'))[1].progress, 'cancelled');
});

test('monotonic revision reconciliation rejects identity mutation and stale rollback', () => {
  const initial = reconcileMagiMessages([], normalizeMagiMessageRecords([
    message(), message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native', status: 'pending', sequence_index: 1 }),
  ]));
  const completed = reconcileMagiMessages(initial, normalizeMagiMessageRecords([
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: 'Done.', source: 'magi-native', status: 'completed', sequence_index: 1, revision: 2 }),
  ]));
  assert.equal(completed[1].text, 'Done.');
  assert.equal(completed[0].progress, 'complete', 'an assistant-only socket revision settles its user pair');
  const staleRecords = normalizeMagiMessageRecords([
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native', status: 'pending', sequence_index: 1 }),
  ]);
  assert.equal(hasMagiReconciliationConflict(completed, staleRecords), false,
    'a delayed lower revision is ignored without poisoning realtime');
  const stale = reconcileMagiMessages(completed, staleRecords);
  assert.equal(stale[1].text, 'Done.');
  const mutatedStatusRecords = normalizeMagiMessageRecords([
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native', status: 'failed', sequence_index: 1, revision: 2 }),
  ]);
  assert.equal(hasMagiReconciliationConflict(completed, mutatedStatusRecords), true);
  const sameRevisionMutation = reconcileMagiMessages(completed, mutatedStatusRecords);
  assert.equal(sameRevisionMutation[1].progress, 'complete');
  const mutatedIdentityRecords = normalizeMagiMessageRecords([
    message({ id: 'mgm_assistant_1', turn_id: 'mgt_mutated_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: 'Changed.', source: 'magi-native', status: 'completed', sequence_index: 1, revision: 3 }),
  ]);
  assert.equal(hasMagiReconciliationConflict(completed, mutatedIdentityRecords), true);
  const mutated = reconcileMagiMessages(completed, mutatedIdentityRecords);
  assert.equal(mutated[1].turnId, 'mgt_native_1');
  const changedClientId = reconcileMagiMessages(completed, normalizeMagiMessageRecords([
    message({ client_message_id: 'client-native-2', revision: 2 }),
  ]));
  assert.deepEqual(changedClientId.map(row => row.id), completed.map(row => row.id));
  const terminalRewrite = normalizeMagiMessageRecords([
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: 'Rewritten.', source: 'magi-native',
      status: 'completed', sequence_index: 1, revision: 3 }),
  ]);
  assert.equal(hasMagiReconciliationConflict(completed, terminalRewrite), true,
    'a higher revision cannot rewrite terminal provider bytes');
});

test('message batches reject partial, mixed-conversation, and duplicate identity payloads', () => {
  const assistant = message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
    reply_to_message_id: 'mgm_user_1', content: 'Done.', source: 'magi-native', sequence_index: 1 });
  assert.deepEqual(normalizeMagiMessageRecords([message(), { ...assistant, status: 'invented' }]), []);
  assert.deepEqual(normalizeMagiMessageRecords([message(), { ...assistant, conversation_id: 'mgc_native_2' }]), []);
  assert.deepEqual(normalizeMagiMessageRecords([message(), { ...assistant, sequence_index: 0 }]), []);
  assert.deepEqual(normalizeMagiMessageRecords([
    message({ client_message_id: 'mgm_assistant_1' }), assistant,
  ]), []);
});

test('authoritative reads prune only server rows and retain genuine pending sends', () => {
  const pending: MagiMessage = { id: 'u-local123', role: 'user', text: 'Pending', source: 'text', delivery: 'sending', progress: 'working' };
  const old: MagiMessage = { id: 'mgm_old', serverId: 'mgm_old', role: 'assistant', text: 'old', source: 'text', sequenceIndex: 9, revision: 1, turnId: 'mgt_old', sentAt: 1_789_000_000_000 };
  const result = reconcileMagiMessages([old, pending], normalizeMagiMessageRecords([message()]), { authoritative: true });
  assert.deepEqual(result.map(row => row.id), ['client-native-1', 'u-local123']);
});

test('bounded change replay updates a cached row outside the newest history page', () => {
  const oldPair = normalizeMagiMessageRecords([
    message(),
    message({ id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_1', content: '', source: 'magi-native',
      status: 'pending', sequence_index: 1 }),
  ]);
  const cached = reconcileMagiMessages([], oldPair).map(row => ({ ...row, fromCache: true }));
  const newerPage = normalizeMagiMessageRecords([
    message({ id: 'mgm_user_2', client_message_id: 'client-native-2', turn_id: 'mgt_native_2',
      sequence_index: 2, content: 'newer request' }),
    message({ id: 'mgm_assistant_2', role: 'assistant', client_message_id: null,
      reply_to_message_id: 'mgm_user_2', turn_id: 'mgt_native_2', sequence_index: 3,
      content: 'newer response', source: 'magi-native' }),
  ]);
  const correction = normalizeMagiMessageRecords([message({
    id: 'mgm_assistant_1', role: 'assistant', client_message_id: null,
    reply_to_message_id: 'mgm_user_1', content: 'Recovered after retry.', source: 'magi-native',
    status: 'completed', sequence_index: 1, revision: 2,
  })]);
  const pageOnly = reconcileMagiMessages(cached, newerPage, { authoritative: true });
  assert.equal(pageOnly.find(row => row.id === 'mgm_assistant_1')?.text, '');
  const synchronized = reconcileMagiMessages(cached, [...newerPage, ...correction], { authoritative: true });
  assert.equal(synchronized.find(row => row.id === 'mgm_assistant_1')?.text, 'Recovered after retry.');
  assert.equal(synchronized.find(row => row.id === 'client-native-1')?.progress, 'complete');
  assert.equal(synchronized.find(row => row.id === 'mgm_assistant_1')?.fromCache, false,
    'the replayed row leaves cache-only status');
});
