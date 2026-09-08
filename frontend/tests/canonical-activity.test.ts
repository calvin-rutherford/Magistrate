import assert from 'node:assert/strict';
import test from 'node:test';
import {
  getCanonicalActivityCursor,
  getCanonicalActivityRecords,
  ingestCanonicalActivityPage,
  normalizeCanonicalActivityRecord,
  setCanonicalActivityPrincipal,
} from '../src/services/CanonicalActivity';

const record = (overrides: Record<string, unknown> = {}) => ({
  id: 'ca_activity_1',
  sequence: 1,
  delivery_sequence: 1,
  revision: 1,
  kind: 'objective.progress',
  state: 'active',
  importance: 'routine',
  title: 'SOAK objective',
  summary: 'Actual durable progress.',
  summary_truncated: false,
  task_id: 'soak-task',
  decision_key: null,
  objective_id: 'obj_soak',
  run_id: 'run_soak_1',
  project: 'Magistrate',
  occurred_at: null,
  observed_at: 1_788_840_000_000,
  refs: [],
  source: { instance_id: 'firstmate:secondmate-a', event_id: 'worker-event-1' },
  ...overrides,
});

const page = (records: unknown[], nextCursor: number, latestCursor = nextCursor) => ({
  schema_version: 'activity.v1',
  records,
  next_cursor: nextCursor,
  latest_cursor: latestCursor,
  has_more: false,
});

test('canonical activity appends, revises by stable id, and advances a change cursor', () => {
  setCanonicalActivityPrincipal('activity-user-a');
  assert.ok(ingestCanonicalActivityPage(page([record()], 1)));
  assert.equal(getCanonicalActivityCursor(), 1);
  assert.deepEqual(getCanonicalActivityRecords().map(item => [item.id, item.state, item.revision]), [
    ['ca_activity_1', 'active', 1],
  ]);

  assert.ok(ingestCanonicalActivityPage(page([record({
    delivery_sequence: 2,
    revision: 2,
    kind: 'objective.completed',
    state: 'completed',
    summary: 'Objective completed.',
  })], 2)));
  assert.equal(getCanonicalActivityRecords().length, 1);
  // A slower HTTP page may overlap an already-consumed socket row; validated
  // overlap is harmless and cannot roll the cursor back.
  assert.ok(ingestCanonicalActivityPage(page([record()], 1, 2)));
  assert.equal(getCanonicalActivityCursor(), 2);
  assert.deepEqual(
    [getCanonicalActivityRecords()[0].state, getCanonicalActivityRecords()[0].revision],
    ['completed', 2],
  );
  assert.ok(!ingestCanonicalActivityPage(page([record({
    delivery_sequence: 3, revision: 2, kind: 'objective.failed', state: 'failed',
  })], 3)), 'one stable revision cannot be rewritten');
  assert.equal(getCanonicalActivityCursor(), 2);
  assert.equal(getCanonicalActivityRecords()[0].state, 'completed');
  for (const mutation of [
    { sequence: 2 },
    { task_id: 'another-task' },
    { objective_id: 'obj_other' },
    { source: { instance_id: 'firstmate:other', event_id: 'worker-event-1' } },
  ]) {
    assert.ok(!ingestCanonicalActivityPage(page([record({
      delivery_sequence: 3, revision: 3, kind: 'objective.completed', state: 'completed',
      ...mutation,
    })], 3)), 'stable activity identity cannot mutate across revisions');
  }
  assert.equal(getCanonicalActivityCursor(), 2);
});

test('principal change clears non-durable activity memory and resets replay position', () => {
  setCanonicalActivityPrincipal('activity-user-b');
  assert.equal(getCanonicalActivityCursor(), 0);
  assert.deepEqual(getCanonicalActivityRecords(), []);
  assert.ok(ingestCanonicalActivityPage(page([record({ id: 'ca_b', source: { instance_id: 'firstmate:main', event_id: null } })], 1)));
  setCanonicalActivityPrincipal(null);
  assert.equal(getCanonicalActivityCursor(), 0);
  assert.deepEqual(getCanonicalActivityRecords(), []);
  assert.ok(!ingestCanonicalActivityPage(page([record()], 1)), 'signed-out activity is never retained');
});

test('malformed fields, credentials, controls, and cursor gaps fail closed without advancement', () => {
  setCanonicalActivityPrincipal('activity-user-invalid');
  for (const candidate of [
    record({ kind: 'worker.prose' }),
    record({ kind: 'objective.completed', state: 'active' }),
    record({ objective_id: null }),
    record({ decision_key: 'inferred-choice' }),
    record({ unexpected: 'field' }),
    record({ summary: 'terminal\u001b[31m' }),
    record({ summary: 'OPENAI_API_KEY=sk-1234567890' }),
    record({ summary: 'database_url=postgres://owner:password@db.example/internal' }),
    record({ summary: 'DB_PASS="opaque value with spaces" must not persist' }),
    record({ summary: 'SAFE+=opaque-value must not persist' }),
    record({ summary: 'token%3Dopaque-value%ZZ' }),
    record({ summary: 'MiXeD + = opaque-value must not persist' }),
    record({ summary: 'Connection postgres://owner:opaque-pass@db.example/internal' }),
    record({ summary: 'Connection ssh://private-user@host.example/internal' }),
    record({ summary: 'Authorization: Basic dXNlcjpwYXNz' }),
    record({ summary: '"client_secret":"opaquevalue"' }),
    record({ summary: 'Azure AD Client Secret: opaquevalue' }),
    record({ summary: 'eyJabcdefghijk.eyJabcdefghijk.signature123' }),
    record({ refs: [{ kind: 'pull-request', url: 'https://user:secret@example.com/pull/1' }] }),
    record({ refs: [{ kind: 'pull-request', url: 'https://github.com/acme/repo/pull/1?token=secret' }] }),
    record({ refs: [{ kind: 'pull-request', url: 'https://github.com/acme/repo/pull/1?' }] }),
    record({ refs: [{ kind: 'pull-request', url: 'https://github.com/acme/repo/pull/1#' }] }),
    record({ refs: [{ kind: 'pull-request', url: 'https://github.com/token%253Dghp_1234567890/repo/pull/1' }] }),
    record({ refs: [{ kind: 'pull-request', url: 'https://example.com/safe-looking/path' }] }),
    record({ source: { instance_id: '', event_id: 'x' } }),
  ]) assert.equal(normalizeCanonicalActivityRecord(candidate), null);

  assert.ok(!ingestCanonicalActivityPage(page([record({ delivery_sequence: 2 })], 2)));
  assert.ok(!ingestCanonicalActivityPage(page([
    record(), record({ id: 'ca_duplicate_sequence', delivery_sequence: 2 }),
  ], 2)), 'two stable records cannot claim one insertion sequence');
  assert.equal(getCanonicalActivityCursor(), 0);
  assert.deepEqual(getCanonicalActivityRecords(), []);
});

test('safe references and more than ten ordered activity rows survive bounded catch-up', () => {
  setCanonicalActivityPrincipal('activity-user-many');
  const rows = Array.from({ length: 12 }, (_, index) => record({
    id: `ca_many_${index + 1}`,
    sequence: index + 1,
    delivery_sequence: index + 1,
    summary: `Progress ${index + 1}.`,
    ...(index === 0 ? { kind: 'worker.message' as const, state: 'completed' as const } : {}),
    refs: index === 11 ? [{ kind: 'pull-request', url: 'https://github.com/example/project/pull/12' }] : [],
  }));
  assert.ok(ingestCanonicalActivityPage(page(rows, 12)));
  assert.equal(getCanonicalActivityCursor(), 12);
  assert.equal(getCanonicalActivityRecords().length, 12);
  assert.deepEqual(getCanonicalActivityRecords().at(-1)?.refs, [
    { kind: 'pull-request', url: 'https://github.com/example/project/pull/12' },
  ]);
});
