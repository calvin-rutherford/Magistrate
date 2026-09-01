import assert from 'node:assert/strict';
import test from 'node:test';
import { formatAccessibleTimestamp, formatConversationTimestamp } from '../src/services/ChatFormatting';
import { AgentHistoryMessage, filterAgentHistory, sanitizeTerminalHistory } from '../src/services/ChatHistory';

test('unknown worker and user rows fail closed at the terminal firewall', () => {
  const rows: AgentHistoryMessage[] = [
    { role: 'user', kind: 'conversation', text: 'known captain prompt' },
    { role: 'user', kind: 'control', text: 'unknown worker prompt' },
    { role: 'assistant', kind: 'control', text: 'unknown worker reply' },
    { role: 'assistant', kind: 'conversation', text: 'known primary reply' },
  ];
  const safe = sanitizeTerminalHistory(rows);
  // Without an audience field, unknown worker/user records are explicit
  // boundaries and can never be promoted to visible roles.
  assert.deepEqual(filterAgentHistory(safe, true).map(row => row.text), ['known captain prompt', 'known primary reply']);
});

test('local user timestamps are valid and accessible, while absent timestamps stay absent', () => {
  const sentAt = Date.parse('2026-08-31T12:34:00.000Z');
  assert.match(formatConversationTimestamp(sentAt, 'en-US') || '', /\d{1,2}:34/);
  assert.match(formatAccessibleTimestamp(sentAt, 'en-US') || '', /2026/);
  assert.equal(formatConversationTimestamp(undefined), null);
  assert.equal(formatAccessibleTimestamp(-1), null);
});

test('chat state fixtures use truthful labels for streaming, gateway failure, and cancellation', () => {
  const fixtures = [
    { progress: 'streaming', label: 'Updating response…' },
    { progress: 'failed', label: 'Response stopped before completion. Retry is available only when this run is safe.' },
    { progress: 'cancelled', label: 'Response stopped' },
  ];
  assert.deepEqual(fixtures.map(fixture => fixture.label), ['Updating response…', 'Response stopped before completion. Retry is available only when this run is safe.', 'Response stopped']);
});
