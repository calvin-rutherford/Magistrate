import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentHistoryMessage, filterAgentHistory, isRenderableToolCall, parseAgentHistory, toolCallPreview } from '../src/services/ChatHistory';

const history: AgentHistoryMessage[] = [
  { role: 'user', kind: 'conversation', text: 'Please inspect the fleet.' },
  { role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
  { role: 'assistant', kind: 'conversation', text: 'The fleet is healthy.' },
];

test('Pi markerless transcript rows remain available without exposing command output', () => {
  const output = [
    'User question from Pi',
    '',
    'Assistant response from Pi with',
    '  a wrapped line.',
    '',
    '[Magistrate execution: harness=pi; model=gpt-5.6-luna; provider=openai-codex; variant=default; profile=pi:default]',
    'User question from routing',
    '',
    '$ npm run test',
    'Ran 3 commands',
  ].join('\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'assistant', kind: 'conversation', text: 'User question from Pi' },
    { role: 'assistant', kind: 'conversation', text: 'Assistant response from Pi with a wrapped line.' },
    { role: 'assistant', kind: 'conversation', text: 'User question from routing' },
  ]);
});

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
test('tool visibility keeps only compact provider-style tool previews', () => {
  const messages: AgentHistoryMessage[] = [
    { role: 'assistant', kind: 'conversation', text: 'The deployment is healthy.' },
    { role: 'assistant', kind: 'tool', text: 'Ran 2 commands' },
    { role: 'assistant', kind: 'tool', text: 'high · /effort' },
    { role: 'assistant', kind: 'tool', text: 'raw terminal output' },
    { role: 'assistant', kind: 'tool', text: 'Running cd /workspace && npm test…' },
  ];
  assert.equal(isRenderableToolCall(messages[1]), true);
  assert.equal(toolCallPreview(messages[4].text), 'Running…');
  assert.equal(isRenderableToolCall(messages[2]), false);
  assert.equal(isRenderableToolCall(messages[3]), false);
  assert.deepEqual(filterAgentHistory(messages, true).map(message => message.text), ['The deployment is healthy.', 'Ran 2 commands', 'Running cd /workspace && npm test…']);
});

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
