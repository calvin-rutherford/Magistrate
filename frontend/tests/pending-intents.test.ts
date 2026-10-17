import assert from 'node:assert/strict';
import test from 'node:test';
import {
  consumePendingIntent,
  enqueuePendingIntent,
  parsePendingIntent,
  pendingIntentPath,
  isDecisionNotification,
} from '../src/services/PendingIntentRouter';

test('parses the versioned owner routes and preserves exact targets', () => {
  assert.deepEqual(parsePendingIntent('/voice?autostart=true'), {
    version: 1, targetType: 'voice', route: '/voice', params: { autostart: 'true' },
  });
  assert.equal(pendingIntentPath(parsePendingIntent('/chat?agentId=crew-7')!), '/chat?agentId=crew-7');
  assert.equal(pendingIntentPath(parsePendingIntent('/attention?item=decision%3A7')!), '/attention?item=decision%3A7');
  assert.equal(pendingIntentPath(parsePendingIntent('/pr-detail?number=42')!), '/pr-detail?number=42');
  assert.equal(pendingIntentPath(parsePendingIntent('/chat?shortcut=running')!), '/chat?shortcut=running');
  assert.equal(pendingIntentPath(parsePendingIntent('/attention?overview=true')!), '/attention?overview=true');
});

test('rejects malformed, external, unsupported, and unsafe targets', () => {
  assert.equal(parsePendingIntent('/voice?autostart=false'), null);
  assert.equal(parsePendingIntent('/attention'), null);
  assert.equal(parsePendingIntent('/chat?agentId=../runner'), null);
  assert.equal(parsePendingIntent('/pr-detail?number=0'), null);
  assert.equal(parsePendingIntent('https://other.example/attention?item=secret'), null);
  assert.equal(parsePendingIntent('magistrate:/unsupported'), null);
  assert.equal(enqueuePendingIntent({ intent_version: 2, route: '/attention?item=wrong-version' }), false);
});

test('each explicit shortcut invocation routes once, while later invocations are not suppressed', () => {
  const first = '/voice?autostart=true&entry=launch-1';
  assert.equal(enqueuePendingIntent(first), true);
  assert.equal(enqueuePendingIntent(first), false);
  assert.equal(pendingIntentPath(consumePendingIntent()!), first);
  assert.equal(enqueuePendingIntent(first), false);
  assert.equal(enqueuePendingIntent('/voice?autostart=true&entry=launch-2'), true);
  consumePendingIntent();
});

test('decision envelope is exact, expired envelopes remain read-only navigation and malformed bindings fail closed', () => {
  const decision = { schema_version: 'magistrate.decision-notification.v1' as const, decision_id: 'dec-1', task_id: 'task-1', run_id: 'run-1', request_id: 'request-1', scope: 'project-1', revision: 3, expires_at: 1 };
  const route = '/attention?item=question-2&decision=dec-1&revision=3';
  const payload = { intent_version: 1, target_type: 'attention', target_id: 'question-2', item_id: 'question-2', decision, route, delivery_id: 'delivery-1' };
  assert.equal(isDecisionNotification(decision), true);
  assert.equal(isDecisionNotification({ ...decision, run_id: '../foreign' }), false);
  assert.equal(enqueuePendingIntent({ ...payload, target_id: 'foreign' }), false);
  assert.equal(enqueuePendingIntent({ ...payload, decision: { ...decision, revision: 2 } }), false);
  assert.equal(enqueuePendingIntent(payload), true);
  assert.equal(pendingIntentPath(consumePendingIntent()!), route);
  assert.equal(enqueuePendingIntent(payload), false);
  assert.equal(enqueuePendingIntent({ ...payload, decision: { ...decision, revision: 4 }, route: route.replace('revision=3', 'revision=4'), delivery_id: 'delivery-2' }), true);
  consumePendingIntent();
  assert.equal(parsePendingIntent('/attention?item=a&item=b'), null);
  assert.equal(parsePendingIntent('/voice?autostart=true#untrusted'), null);
});

test('queues an unauthenticated intent and consumes duplicate delivery once', () => {
  const url = '/attention?item=deat-test-001';
  assert.equal(enqueuePendingIntent(url), true);
  assert.equal(enqueuePendingIntent(url), false);
  assert.deepEqual(consumePendingIntent(), parsePendingIntent(url));
  assert.equal(enqueuePendingIntent(url), false);
  assert.equal(consumePendingIntent(), null);
});
