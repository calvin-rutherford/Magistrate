import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CanonicalMessage,
  canonicalRowId,
  normalizeCanonicalMessages,
  reconcileCanonicalMessages,
  sameRenderedTranscript,
} from '../src/services/CanonicalConversation';
import { filterCanonicalMessages } from '../src/services/ChatHistory';
import { ConversationMessage } from '../src/services/ConversationSession';

const canonical = (overrides: Partial<CanonicalMessage> & Pick<CanonicalMessage, 'id' | 'role' | 'text' | 'sequence_index'>): CanonicalMessage => ({
  type: 'conversation', visible_in_chat: true, turn_status: 'answered', created_at: 1756000000000, ...overrides,
});

test('a canonical user message and its optimistic bubble are one row', () => {
  const optimistic: ConversationMessage[] = [
    { id: 'u-1', role: 'user', text: 'redeploy the demo', source: 'text', sentAt: 1000, audience: 'captain', delivery: 'sending', progress: 'working' },
  ];
  const rows = reconcileCanonicalMessages(optimistic, [
    canonical({ id: 'cm_1', role: 'user', text: 'redeploy the demo', sequence_index: 0, client_message_id: 'u-1', turn_status: 'awaiting_reply', created_at: 1756000000456 }),
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, 'u-1', 'the submission id stays the row id');
  assert.equal(rows[0].canonicalId, 'cm_1');
  assert.equal(rows[0].delivery, 'sent');
  assert.equal(rows[0].sentAt, 1756000000456, 'gateway time replaces the optimistic placeholder after acknowledgement');
});

test('canonical attachment references replace optimistic upload state without carrying bytes', () => {
  const [message] = normalizeCanonicalMessages([{
    id: 'cm_attachment', turn_id: 'ct_attachment', client_message_id: 'u-attachment',
    role: 'user', type: 'conversation', text: 'review this', visible_in_chat: true,
    sequence_index: 0, revision: 1, created_at: 1756000000456,
    attachments: [{
      id: 'upload-0000000000000001', upload_id: 'upload-0000000000000001',
      name: 'notes.txt', media_type: 'text/plain', size: 5,
      url: '/api/v1/uploads/upload-0000000000000001',
    }],
  }]);
  const rows = reconcileCanonicalMessages([{
    id: 'u-attachment', role: 'user', text: 'review this', source: 'text',
    delivery: 'sending', attachments: [{ name: 'notes.txt', mediaType: 'text/plain', status: 'uploading' }],
  }], [message]);

  assert.deepEqual(rows[0].attachments, [{
    name: 'notes.txt', mediaType: 'text/plain', size: 5, status: 'attached',
    uploadId: 'upload-0000000000000001', url: '/api/v1/uploads/upload-0000000000000001',
  }]);
});

test('a revised assistant message updates its row instead of appending a second', () => {
  let rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_u', role: 'user', text: 'run the tests', sequence_index: 0, client_message_id: 'u-2' }),
    canonical({ id: 'cm_a', role: 'assistant', text: 'The tests are running', sequence_index: 999, revision: 1 }),
  ]);
  // The gateway delivers only the record whose revision changed.
  rows = reconcileCanonicalMessages(rows, [
    canonical({ id: 'cm_a', role: 'assistant', text: 'The tests are running and all 42 pass.', sequence_index: 999, revision: 2 }),
  ]);

  assert.deepEqual(rows.map(row => [row.role, row.text]), [
    ['user', 'run the tests'],
    ['assistant', 'The tests are running and all 42 pass.'],
  ]);
});

test('an out-of-order revision cannot roll a canonical reply backwards', () => {
  let rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_a', role: 'assistant', text: 'The final answer.', sequence_index: 999, revision: 3 }),
  ]);
  rows = reconcileCanonicalMessages(rows, [
    canonical({ id: 'cm_a', role: 'assistant', text: 'The partial answer', sequence_index: 999, revision: 2 }),
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].text, 'The final answer.');
  assert.equal(rows[0].canonicalRevision, 3);
});

test('a same-revision turn-status update still reaches the rendered row', () => {
  let rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_u', role: 'user', text: 'stop this', sequence_index: 0, client_message_id: 'u-stop', revision: 1, turn_status: 'awaiting_reply' }),
  ]);
  rows = reconcileCanonicalMessages(rows, [
    canonical({ id: 'cm_u', role: 'user', text: 'stop this', sequence_index: 0, client_message_id: 'u-stop', revision: 1, turn_status: 'cancelled' }),
  ]);

  assert.equal(rows[0].canonicalRevision, 1);
  assert.equal(rows[0].delivery, 'cancelled');
  assert.equal(rows[0].progress, 'cancelled');
});

test('repeated identical prompts stay separate rows with their own replies', () => {
  const rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_1', role: 'user', text: 'same wording', sequence_index: 0, client_message_id: 'u-a' }),
    canonical({ id: 'cm_2', role: 'assistant', text: 'First reply.', sequence_index: 999 }),
    canonical({ id: 'cm_3', role: 'user', text: 'same wording', sequence_index: 1000, client_message_id: 'u-b' }),
    canonical({ id: 'cm_4', role: 'assistant', text: 'Second reply.', sequence_index: 1999 }),
  ]);

  assert.deepEqual(rows.map(row => row.id), ['u-a', 'cm_2', 'u-b', 'cm_4']);
});

test('messages render in the gateway sequence, not in delivery order', () => {
  const rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_reply', role: 'assistant', text: 'The reply.', sequence_index: 999 }),
    canonical({ id: 'cm_prompt', role: 'user', text: 'The prompt.', sequence_index: 0, client_message_id: 'u-3' }),
  ]);
  assert.deepEqual(rows.map(row => row.text), ['The prompt.', 'The reply.']);
});

test('an authoritative canonical sync replaces stale cache rows and overlays only an unacknowledged send', () => {
  const existing: ConversationMessage[] = [
    { id: 'u-old', role: 'user', text: 'stale cached text', source: 'text', sentAt: 123, audience: 'captain', delivery: 'sent', canonicalId: 'cm_x', sequenceIndex: 0 },
    { id: 'poisoned-duplicate', role: 'assistant', text: 'A duplicate from the terminal era.', source: 'text', audience: 'primary', canonicalId: 'cm_poison', sequenceIndex: 999 },
    { id: 'u-inflight', role: 'user', text: 'just sent', source: 'text', sentAt: 1756000000999, audience: 'captain', delivery: 'sending' },
  ];
  const rows = reconcileCanonicalMessages(existing, [
    canonical({ id: 'cm_x', role: 'user', text: 'an earlier turn', sequence_index: 0, client_message_id: 'u-old', created_at: 1756000000123 }),
  ], { authoritative: true });

  assert.deepEqual(rows.map(row => row.id), ['u-old', 'u-inflight']);
  assert.deepEqual(rows.map(row => row.text), ['an earlier turn', 'just sent']);
  assert.equal(rows[0].sentAt, 1756000000123);
  assert.equal(rows[1].sentAt, 1756000000999);
});

test('a full list read drops a recorded row the gateway no longer returns', () => {
  const first = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_1', role: 'user', text: 'kept', sequence_index: 0, client_message_id: 'u-k' }),
    canonical({ id: 'cm_2', role: 'assistant', text: 'dropped after a reset', sequence_index: 999 }),
  ], { authoritative: true });
  assert.equal(first.length, 2);

  const second = reconcileCanonicalMessages(first, [
    canonical({ id: 'cm_1', role: 'user', text: 'kept', sequence_index: 0, client_message_id: 'u-k' }),
  ], { authoritative: true });
  assert.deepEqual(second.map(row => row.text), ['kept']);

  // A revision delta is not a full list and must not prune anything.
  const delta = reconcileCanonicalMessages(first, [
    canonical({ id: 'cm_2', role: 'assistant', text: 'still here', sequence_index: 999 }),
  ]);
  assert.deepEqual(delta.map(row => row.text), ['kept', 'still here']);
});

test('turn status drives delivery and progress rather than message text', () => {
  const [failed] = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_f', role: 'user', text: 'will not land', sequence_index: 0, client_message_id: 'u-f', turn_status: 'failed' }),
  ]);
  assert.equal(failed.delivery, 'failed');
  assert.equal(failed.progress, 'failed');

  const [cancelled] = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_c', role: 'user', text: 'stopped', sequence_index: 0, client_message_id: 'u-c', turn_status: 'cancelled' }),
  ]);
  assert.equal(cancelled.delivery, 'cancelled');
  assert.equal(cancelled.progress, 'cancelled');

  const [waiting] = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_w', role: 'user', text: 'working', sequence_index: 0, client_message_id: 'u-w', turn_status: 'awaiting_reply' }),
  ]);
  assert.equal(waiting.progress, 'working', 'an unanswered turn is what keeps the thinking state honest');
});

test('internal, status, and malformed records are never renderable messages', () => {
  const normalized = normalizeCanonicalMessages([
    canonical({ id: 'cm_ok', role: 'assistant', text: 'A real reply.', sequence_index: 1 }),
    { id: 'cm_internal', role: 'assistant', type: 'internal', text: 'FIRSTMATE_OP: WAKE_ACK', sequence_index: 2, visible_in_chat: false },
    { id: 'cm_status', role: 'assistant', type: 'status', text: 'Working (2s)', sequence_index: 3 },
    { id: 'cm_bad_role', role: 'system', type: 'conversation', text: 'not a chat role', sequence_index: 4 },
    { id: 'cm_no_seq', role: 'assistant', type: 'conversation', text: 'no sequence' },
    { role: 'assistant', type: 'conversation', text: 'no id', sequence_index: 5 },
    'not an object',
  ]);

  assert.deepEqual(normalized.map(message => message.id), ['cm_ok']);
});

test('tool events are hidden by default and revealed only as canonical tool rows', () => {
  const rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_u', role: 'user', text: 'check the deploy', sequence_index: 0, client_message_id: 'u-t' }),
    canonical({ id: 'cm_t', role: 'assistant', text: 'Running…', type: 'tool', sequence_index: 1, visible_in_chat: false }),
    canonical({ id: 'cm_a', role: 'assistant', text: 'The deploy is healthy.', sequence_index: 999 }),
  ]);

  assert.deepEqual(filterCanonicalMessages(rows, false).map(row => row.text), ['check the deploy', 'The deploy is healthy.']);
  assert.deepEqual(filterCanonicalMessages(rows, true).map(row => row.text), ['check the deploy', 'Running…', 'The deploy is healthy.']);
});

test('a mislabelled harness record still cannot render as agent prose', () => {
  const rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_leak', role: 'assistant', text: '{"jsonrpc":"2.0","result":{"ok":true}}', sequence_index: 0 }),
    canonical({ id: 'cm_pane', role: 'assistant', text: 'pane_id=w1:p9 tab_id=secret', sequence_index: 1 }),
    canonical({ id: 'cm_raw', role: 'assistant', text: '$ cat /tmp/raw-pane-output', sequence_index: 2 }),
    canonical({ id: 'cm_real', role: 'assistant', text: 'A real reply.', sequence_index: 3 }),
    // Captain-authored text is never reclassified by content.
    canonical({ id: 'cm_captain', role: 'user', text: 'explain jsonrpc and pane_id to me', sequence_index: 4, client_message_id: 'u-q' }),
  ]);

  assert.deepEqual(filterCanonicalMessages(rows, true).map(row => row.text), [
    'A real reply.', 'explain jsonrpc and pane_id to me',
  ]);
});

test('the row id is the submission id for user messages and the record id otherwise', () => {
  assert.equal(canonicalRowId(canonical({ id: 'cm_1', role: 'user', text: 'x', sequence_index: 0, client_message_id: 'u-1' })), 'u-1');
  assert.equal(canonicalRowId(canonical({ id: 'cm_1', role: 'user', text: 'x', sequence_index: 0 })), 'cm_1');
  assert.equal(canonicalRowId(canonical({ id: 'cm_2', role: 'assistant', text: 'x', sequence_index: 1, client_message_id: 'u-1' })), 'cm_2');
});

test('an unchanged sync produces no rendered change', () => {
  const list = [canonical({ id: 'cm_1', role: 'assistant', text: 'Steady.', sequence_index: 0 })];
  const first = reconcileCanonicalMessages([], list, { authoritative: true });
  const second = reconcileCanonicalMessages(first, list, { authoritative: true });
  assert.ok(sameRenderedTranscript(first, second));
  assert.ok(!sameRenderedTranscript(first, reconcileCanonicalMessages(first, [
    canonical({ id: 'cm_1', role: 'assistant', text: 'Steady, and finished.', sequence_index: 0 }),
  ])));
});

test('a voice turn recorded by the gateway keeps its voice source', () => {
  const rows = reconcileCanonicalMessages([], [
    canonical({ id: 'cm_v', role: 'user', text: 'what is the fleet doing', sequence_index: 0, client_message_id: 'voice-u-1', source: 'voice' }),
    canonical({ id: 'cm_va', role: 'assistant', text: 'Fleet is quiet.', sequence_index: 999, source: 'voice' }),
  ]);
  assert.deepEqual(rows.map(row => row.source), ['voice', 'voice']);
});
