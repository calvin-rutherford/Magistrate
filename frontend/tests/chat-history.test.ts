import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentHistoryMessage, filterAgentHistory } from '../src/services/ChatHistory';

const history: AgentHistoryMessage[] = [
  { role: 'user', kind: 'conversation', text: 'Please inspect the fleet.' },
  { role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
  { role: 'assistant', kind: 'conversation', text: 'The fleet is healthy.' },
];

test('agent history hides tool calls by default', () => {
  assert.deepEqual(filterAgentHistory(history, false).map(message => message.text), [
    'Please inspect the fleet.',
    'The fleet is healthy.',
  ]);
});

test('agent history includes tool calls only when enabled', () => {
  assert.deepEqual(filterAgentHistory(history, true), history);
});
