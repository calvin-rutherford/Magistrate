import assert from 'node:assert/strict';
import test from 'node:test';
import { AgentHistoryMessage, filterAgentHistory, isHarnessArtifact, isRawTerminalArtifact, isRenderableToolCall, parseAgentHistory, sanitizeAgentHistory, sanitizeTerminalHistory, toolCallPreview } from '../src/services/ChatHistory';

const history: AgentHistoryMessage[] = [
  { role: 'user', kind: 'conversation', text: 'Please inspect the fleet.' },
  { role: 'assistant', kind: 'tool', text: 'Ran 3 commands' },
  { role: 'assistant', kind: 'conversation', text: 'The fleet is healthy.' },
];

test('Pi markerless plain text fails closed instead of exposing ambiguous harness rows', () => {
  const output = ['User question from Pi', '', 'Planning next step', '', '$ npm run test', 'Ran 3 commands'].join('\n');
  assert.deepEqual(parseAgentHistory(output), []);
});

test('Pi ANSI boxes preserve user/agent roles while suppressing reasoning, calm, transport, and chrome', () => {
  const reset = '\x1b[0m';
  const userBg = '\x1b[48;5;59m';
  const toolBg = '\x1b[48;5;22m';
  const output = [
    `${reset}${userBg}                                                           ${reset}`,
    `${reset}${userBg} User question from Pi                                  ${reset}`,
    `${reset}${userBg}                                                           ${reset}`,
    '',
    ` ${reset}\x1b[1m\x1b[3m\x1b[38;5;244mPlanning private work${reset}`,
    '',
    `${reset}${toolBg} $ npm run test                                            ${reset}`,
    `${reset}${toolBg} Took 0.4s                                                 ${reset}`,
    '',
    ' Agent response from Pi with',
    '   a wrapped line.',
    '',
    ' /calm animation status',
    '',
    ' {"jsonrpc":"2.0","result":{"ok":true}}',
    '',
    '───────────────────────────────────────────────────────────',
    '~/firstmate (main)',
  ].join('\r\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'user', kind: 'conversation', text: 'User question from Pi' },
    { role: 'assistant', kind: 'tool', text: '$ npm run test Took 0.4s' },
    { role: 'assistant', kind: 'conversation', text: 'Agent response from Pi with a wrapped line.' },
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

test('normalized history rejects harness metadata even when transport mislabeled it as conversation', () => {
  const incoming: AgentHistoryMessage[] = [
    { role: 'user', kind: 'conversation', text: 'Keep this real question.' },
    { role: 'user', kind: 'conversation', text: 'Can you explain /calm and jsonrpc?' },
    { role: 'assistant', kind: 'conversation', text: 'Actual agent answer.' },
    { role: 'assistant', kind: 'conversation', text: 'FIRSTMATE_OP: v1 watcher wake' },
    { role: 'assistant', kind: 'conversation', text: '/calm animation status' },
    { role: 'assistant', kind: 'conversation', text: '{"jsonrpc":"2.0","result":{"ok":true}}' },
    { role: 'assistant', kind: 'conversation', text: '$ cat /tmp/raw-terminal' },
    { role: 'assistant', kind: 'tool', text: 'raw pane_id=w1:p2 runtime metadata' },
  ];
  assert.equal(isHarnessArtifact(incoming[3].text), true);
  assert.equal(isRawTerminalArtifact(incoming[6].text), true);
  assert.deepEqual(sanitizeAgentHistory(incoming).map(message => message.text), ['Keep this real question.', 'Can you explain /calm and jsonrpc?', 'Actual agent answer.']);
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

// Regression for the deployed-demo report after #61/#63: real Pi snapshots box
// their tool envelopes exactly like a user turn, so every unrecognised box was
// promoted to a highlighted user message.
test('a Pi tool envelope in a user-coloured box is typed as tool activity, never a user turn', () => {
  const reset = '\x1b[0m';
  const box = '\x1b[48;5;59m';
  const output = [
    `${reset}${box} edit gateway/app/notifications.py                          ${reset}`,
    `${reset}${box}                                                            ${reset}`,
    `${reset}${box} ... 340     elif parsed.path == "/pr-detail":               ${reset}`,
    `${reset}${box} 341         target_type = "pull-request"                    ${reset}`,
    '',
    `${reset}${box} read frontend/src/api/client.ts:425-539                     ${reset}`,
    '',
    ' The deploy is healthy.',
  ].join('\r\n');
  const parsed = parseAgentHistory(output);
  assert.deepEqual(parsed.map(message => [message.role, message.kind]), [
    ['assistant', 'tool'], ['assistant', 'tool'], ['assistant', 'conversation'],
  ]);
  assert.equal(parsed.filter(message => message.role === 'user').length, 0);
});

// The envelope rule needs BOTH a real path and a line range or numbered body,
// so a captain turn that only mentions a file is not reclassified. (A turn
// opening with a verb the legacy tool pattern already claims - 'Read', 'Ran',
// 'Edited' - was typed as tool activity before this change and still is.)
test('a captain turn that merely names a file stays a captain turn', () => {
  const reset = '\x1b[0m';
  const box = '\x1b[48;5;59m';
  const output = [
    `${reset}${box} edit src/main.py                                            ${reset}`,
    '',
    `${reset}${box} check gateway/app/db.py and tell me what it does            ${reset}`,
    '',
    ' Here is what it does.',
  ].join('\r\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'user', kind: 'conversation', text: 'edit src/main.py' },
    { role: 'user', kind: 'conversation', text: 'check gateway/app/db.py and tell me what it does' },
    { role: 'assistant', kind: 'conversation', text: 'Here is what it does.' },
  ]);
});

test('a spinner row and a truncated cwd row are not agent prose', () => {
  assert.equal(isHarnessArtifact('⠦ Working...'), true);
  assert.equal(isHarnessArtifact('~/.treehouse/Magistrate-7ab3fc/1/Magistrate (fm/magistra...'), true);
  assert.equal(isHarnessArtifact('model: claude-opus-5'), true);
  assert.equal(isHarnessArtifact('session_id: 5f2c'), true);
  assert.equal(isHarnessArtifact('The deploy finished; the model reported no errors.'), false);
});

test('a Firstmate-to-worker prompt on a marked row is excluded, not shown as a user message', () => {
  const output = [
    '› FIRSTMATE_OP: v1 launch-brief: you are a crewmate',
    '',
    '⏺ Worker acknowledgement for Firstmate.',
  ].join('\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'assistant', kind: 'conversation', text: 'Worker acknowledgement for Firstmate.' },
  ]);
});

test('terminal sanitization retains an excluded record as a control boundary', () => {
  const incoming: AgentHistoryMessage[] = [
    { role: 'user', kind: 'conversation', text: 'captain prompt' },
    { role: 'assistant', kind: 'conversation', text: 'The captain reply.' },
    { role: 'user', kind: 'conversation', text: 'FIRSTMATE_OP: inspect this' },
    { role: 'assistant', kind: 'tool', text: '$ cat /tmp/worker-output' },
    { role: 'assistant', kind: 'conversation', text: 'edit gateway/app/db.py\n\n... 12     import os' },
    { role: 'assistant', kind: 'conversation', text: 'pane_id=w1:p9' },
  ];
  assert.deepEqual(sanitizeTerminalHistory(incoming).map(message => [message.role, message.kind]), [
    ['user', 'conversation'], ['assistant', 'conversation'], ['user', 'control'],
    ['assistant', 'tool'], ['assistant', 'control'], ['assistant', 'control'],
  ]);
  // Control records can never reach a renderer, whichever role they carry.
  assert.deepEqual(filterAgentHistory(sanitizeTerminalHistory(incoming), true).map(message => message.text), [
    'captain prompt', 'The captain reply.', '$ cat /tmp/worker-output',
  ]);
});

test('a tool envelope preview exposes the bounded verb and never its path', () => {
  assert.equal(toolCallPreview('edit gateway/app/notifications.py\n\n... 340     elif x:'), 'Edit');
  assert.equal(toolCallPreview('read frontend/src/api/client.ts:425-539'), 'Read');
  assert.equal(isRenderableToolCall({ kind: 'tool', text: 'read frontend/src/api/client.ts:425-539' }), true);
  assert.equal(isRawTerminalArtifact('$ cat /tmp/output'), true);
});

// The captain pane can be showing Claude's /usage panel when a snapshot is read.
// Its tab bar, block meters, and gauge rows are boxed exactly like a user turn
// and were rendered as highlighted captain messages.
test('a harness usage overlay is not conversation', () => {
  const reset = '\x1b[0m';
  const box = '\x1b[48;5;59m';
  const output = [
    `${reset}${box} Settings  Status   Config   Usage   Stats                 ${reset}`,
    '',
    ' your usage, not a breakdown                            ↑',
    '',
    ' 74% of your usage was at >150k context',
    '',
    `${reset}${box} ███████████████████████████▌                              ${reset}`,
    '',
    ' 47% used ↓',
    '',
    `${reset}${box} redeploy the demo once the chat fix lands                 ${reset}`,
    '',
    ' Aye captain, queued behind the chat fix.',
  ].join('\r\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'user', kind: 'conversation', text: 'redeploy the demo once the chat fix lands' },
    { role: 'assistant', kind: 'conversation', text: 'Aye captain, queued behind the chat fix.' },
  ]);
});

// Regression for the duplicate captain rows in the deployed demo: Claude frames
// its composer with '───' rules, and a Herdr snapshot catches that box
// mid-keystroke. Every keystroke otherwise minted a new highlighted user row.
test('the framed composer row is unsubmitted input, not a captain turn', () => {
  const output = [
    '› an actually submitted captain turn',
    '',
    '⏺ The agent reply.',
    '',
    '───────────────────────────────────────────',
    "❯  let's start using the pi harness and gpt 5.6 luna and",
    '  sol depending on the job. only use opus 5 for the',
    '───────────────────────────────────────────',
    '  ⏵⏵ auto mode on (shift+tab to cycle)',
  ].join('\n');
  assert.deepEqual(parseAgentHistory(output), [
    { role: 'user', kind: 'conversation', text: 'an actually submitted captain turn' },
    { role: 'assistant', kind: 'conversation', text: 'The agent reply.' },
  ]);
});
