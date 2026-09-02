import assert from 'node:assert/strict';
import test from 'node:test';
import { CanonicalMessage, normalizeCanonicalMessages, reconcileCanonicalMessages } from '../src/services/CanonicalConversation';
import { filterCanonicalMessages } from '../src/services/ChatHistory';

const canonical = (overrides: Partial<CanonicalMessage> & Pick<CanonicalMessage, 'id' | 'role' | 'text' | 'sequence_index'>): CanonicalMessage => ({
  type: 'conversation', visible_in_chat: true, revision: 1,
  turn_status: 'streaming', created_at: 1_788_313_923_331,
  ...overrides,
});

/**
 * Fast semantic gate for every Chat PR, including presentation-only changes.
 * It exercises the poll + WebSocket contract without a browser: canonical ids
 * and roles must survive duplicate/revised delivery, one real reply must remain,
 * and even a mislabelled Pi footer must fail closed before rendering.
 */
test('captain semantic smoke: one prompt, one stable reply, no harness metadata', () => {
  const initial = normalizeCanonicalMessages([
    canonical({ id: 'cm_user', role: 'user', text: 'Hello', sequence_index: 0, client_message_id: 'smoke-user' }),
    canonical({ id: 'cm_reply', role: 'assistant', text: 'Hello, captain.', sequence_index: 999 }),
    canonical({
      id: 'cm_footer', role: 'assistant',
      text: '↑200k ↓12k R5.6M $3.002 (sub) 34.9%/272k (auto) gpt-5.6-s',
      sequence_index: 1000,
    }),
    // Role corruption is rejected at the canonical boundary.
    { id: 'cm_system', role: 'system', type: 'conversation', text: 'harness internal', sequence_index: 1001 },
  ]);

  let rows = reconcileCanonicalMessages([], initial, { authoritative: true });
  // Duplicate poll/socket delivery and a growing reply update the same identity.
  rows = reconcileCanonicalMessages(rows, [
    canonical({ id: 'cm_reply', role: 'assistant', text: 'Hello, captain.', sequence_index: 999 }),
  ]);
  rows = reconcileCanonicalMessages(rows, [
    canonical({ id: 'cm_reply', role: 'assistant', text: 'Hello, captain. All systems are ready.', sequence_index: 999, revision: 2 }),
  ]);

  const rendered = filterCanonicalMessages(rows, false);
  assert.deepEqual(rendered.map(row => [row.id, row.role, row.text]), [
    ['smoke-user', 'user', 'Hello'],
    ['cm_reply', 'assistant', 'Hello, captain. All systems are ready.'],
  ]);
  assert.equal(rendered.filter(row => row.role === 'assistant').length, 1, 'assistant reply must exist exactly once');
  assert.equal(rendered[1].canonicalId, 'cm_reply', 'canonical reply identity must not change');
  assert.equal(rendered[1].canonicalRevision, 2);
});
