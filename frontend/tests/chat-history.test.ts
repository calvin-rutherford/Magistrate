import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentHistoryMessage, filterAgentHistory, parseAgentHistory } from '../src/services/ChatHistory';

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

// Mirrors gateway/tests/test_captain_history.py — the two parsers must agree.
test('parser keeps only conversational prose, typing Claude unmarked tool rows as tool activity', () => {
  const output = [
    '● Two more of the four rebases are confirmed clean — merging',
    '  both now.',
    '',
    '  Ran 4 shell commands',
    '',
    '● Stop hook feedback',
    '',
    "● Running all frontend suites (not just CI's subset) plus",
    '  gateway:',
    '',
    '  Searching for 2 patterns',
    '',
    '✽ Combobulating… (9m 12s · ↓ 12.7k tokens)',
    '',
  ].join('\n');

  const messages = parseAgentHistory(output);
  assert.deepEqual(filterAgentHistory(messages, false), [
    { role: 'assistant', kind: 'conversation', text: 'Two more of the four rebases are confirmed clean — merging both now.' },
    { role: 'assistant', kind: 'conversation', text: "Running all frontend suites (not just CI's subset) plus gateway:" },
  ]);
  assert.deepEqual(messages.filter(message => message.kind === 'tool').map(message => message.text), [
    'Ran 4 shell commands',
    'Searching for 2 patterns',
  ]);
});

// Mirrors gateway/tests/test_captain_history.py — the two parsers must agree.
test('parser drops mid-frame chrome and splits an unrecognised tool row off its message', () => {
  const output = [
    '● Five of six merged now — only chat cleanup (#29) remains.',
    '  Jump to bottom (ctrl+End) ↓',
    '',
    '● Stop hook feed 1 new message (ctrl+End) ↓',
    '',
    '● Hardening that, then verifying the real pattern:',
    '',
    '  Running cd "/workspace" && npx tsc --noEmit…',
    '',
    '  ⎿  $ cd "/workspace" && npx tsc --noEmit',
    '',
    '✻ Cogitated for 7s · done 10:43 PM · 2 shells still running',
    '                                          ● high · /effort',
    '',
  ].join('\n');

  const messages = parseAgentHistory(output);
  assert.deepEqual(messages.map(message => [message.kind, message.text.split('\n')[0]]), [
    ['conversation', 'Five of six merged now — only chat cleanup (#29) remains.'],
    ['conversation', 'Hardening that, then verifying the real pattern:'],
    ['tool', 'Running cd "/workspace" && npx tsc --noEmit…'],
  ]);
});
