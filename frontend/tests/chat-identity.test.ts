import assert from 'node:assert/strict';
import test from 'node:test';
import { fallbackMessageId, isLocallyAuthoredId, isTerminalRevision, messageContentKey, messageIdentity, revisionTargetId, terminalRevisionCandidate } from '../src/services/ChatIdentity';

test('a server id is the identity, and the content key is the convergent fallback', () => {
  const withId = { id: 'abc', role: 'assistant', kind: 'conversation', text: 'Done.' };
  assert.equal(messageIdentity(withId), 'id:abc');
  assert.equal(messageIdentity({ role: 'assistant', kind: 'conversation', text: 'Done.' }), 'assistant|conversation|Done.');
  assert.equal(messageContentKey({ role: 'user', text: 'hi' }), 'user|conversation|hi');
  // WebSocket, poll, and history reads of the same id-less row converge.
  assert.equal(fallbackMessageId({ role: 'assistant', text: 'Done.' }), fallbackMessageId({ role: 'assistant', kind: 'conversation', text: 'Done.' }));
});

// Regression for the duplicate agent rows seen in the deployed demo: Herdr
// exposes no durable ids, so the gateway hashes terminal content - and that
// content grows, reflows, and scrolls while a reply renders.
test('a reply that grew, reflowed, or lost its head is one row, not two', () => {
  assert.equal(isTerminalRevision('The tests are running', 'The tests are running and all 42 pass.'), true);
  assert.equal(isTerminalRevision('The tests are running and all 42 pass.', 'and all 42 pass.'), true);
  assert.equal(isTerminalRevision('The tests are\nrunning now', 'The tests are running   now'), true);
  assert.equal(isTerminalRevision('Worker reply for firstmate.', 'Worker reply for firstmate, extended.'), true);
});

test('two genuinely distinct replies stay distinct', () => {
  assert.equal(isTerminalRevision('First reply', 'Second reply'), false);
  assert.equal(isTerminalRevision('Done.', 'Failed.'), false);
  assert.equal(isTerminalRevision('Yes', 'Yes, absolutely, here is the long form answer.'), false, 'a short fragment carries too little signal');
  assert.equal(isTerminalRevision(
    'Here is the deployment summary: the gateway is healthy, the frontend build succeeded, and no alerts fired overnight.',
    'Here is the deployment summary: the database migration failed and two alerts fired during the rollout window.',
  ), false, 'a shared opening is not a revision');
});

test('only the newest agent row may be revised, and only by the newest incoming row', () => {
  const store = [
    { id: 'u1', role: 'user', kind: 'conversation', text: 'run the tests' },
    { id: 'a1', role: 'assistant', kind: 'conversation', text: 'The tests are running' },
    { id: 't1', role: 'assistant', kind: 'tool', text: 'Running 3 commands' },
  ];
  assert.equal(revisionTargetId(store, { role: 'assistant', kind: 'conversation', text: 'The tests are running and all 42 pass.' }), 'a1');
  // A user row is authored locally and is never rewritten by terminal output.
  assert.equal(revisionTargetId([store[0]], { role: 'assistant', kind: 'conversation', text: 'run the tests, please' }), null);
  assert.equal(revisionTargetId(store, { role: 'user', kind: 'conversation', text: 'The tests are running now' }), null);
  assert.equal(revisionTargetId(store, { role: 'assistant', kind: 'tool', text: 'Running 4 commands' }), null);
  const batch = [
    { role: 'assistant', kind: 'conversation', text: 'An older settled reply.' },
    { role: 'user', kind: 'conversation', text: 'next question' },
    { role: 'assistant', kind: 'conversation', text: 'The newest reply.' },
  ];
  assert.equal(terminalRevisionCandidate(batch)?.text, 'The newest reply.');
  assert.equal(terminalRevisionCandidate([{ role: 'assistant', kind: 'tool', text: 'Ran 1 command' }]), null);
});

// Regression for the duplicate captain rows: Herdr catches a harness composer
// mid-keystroke, so each character produced a new content hash and a new row.
test('a growing terminal composer row collapses into one row, but a submitted message never does', () => {
  const composerRow = { id: 'a1b2c3d4e5f6a7b8c9d0', role: 'user', kind: 'conversation', text: "let's start using the pi" };
  assert.equal(revisionTargetId([composerRow], { role: 'user', kind: 'conversation', text: "let's start using the pi harness" }), 'a1b2c3d4e5f6a7b8c9d0');
  // A locally submitted captain message is authoritative and is never rewritten,
  // which is what keeps two identical messages sent at different times distinct.
  const submitted = { id: 'u-1756000000000-abc', role: 'user', kind: 'conversation', text: "let's start using the pi" };
  assert.equal(revisionTargetId([submitted], { role: 'user', kind: 'conversation', text: "let's start using the pi harness" }), null);
  assert.equal(isLocallyAuthoredId('u-1756000000000-abc'), true);
  assert.equal(isLocallyAuthoredId('voice-a-12'), true);
  assert.equal(isLocallyAuthoredId('a1b2c3d4e5f6a7b8c9d0'), false);
  assert.equal(isLocallyAuthoredId('history-1abc'), false);
});
