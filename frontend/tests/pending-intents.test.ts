import assert from 'node:assert/strict';
import test from 'node:test';
import {
  consumePendingIntent,
  enqueuePendingIntent,
  parsePendingIntent,
  pendingIntentPath,
} from '../src/services/PendingIntentRouter';

test('parses the versioned owner routes and preserves exact targets', () => {
  assert.deepEqual(parsePendingIntent('/voice?autostart=true'), {
    version: 1, targetType: 'voice', route: '/voice', params: { autostart: 'true' },
  });
  assert.equal(pendingIntentPath(parsePendingIntent('/chat?agentId=crew-7')!), '/chat?agentId=crew-7');
  assert.equal(pendingIntentPath(parsePendingIntent('/attention?item=decision%3A7')!), '/attention?item=decision%3A7');
  assert.equal(pendingIntentPath(parsePendingIntent('/pr-detail?number=42')!), '/pr-detail?number=42');
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

test('queues an unauthenticated intent and consumes duplicate delivery once', () => {
  const url = '/attention?item=deat-test-001';
  assert.equal(enqueuePendingIntent(url), true);
  assert.equal(enqueuePendingIntent(url), false);
  assert.deepEqual(consumePendingIntent(), parsePendingIntent(url));
  assert.equal(enqueuePendingIntent(url), false);
  assert.equal(consumePendingIntent(), null);
});
