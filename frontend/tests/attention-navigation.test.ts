import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

type Compare = (decision: string | string[] | undefined, revision: string | string[] | undefined, item: any) => any;

function contract(): { compareDecisionDestination: Compare; currentDecisionDestination: (item: any) => any; decisionDestinationAllowsAction: (value: any) => boolean } {
  const output = ts.transpileModule(
    readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  const exports: Record<string, any> = {};
  const inert = new Proxy({}, { get: () => () => undefined });
  const modules: Record<string, any> = {
    '@react-native-async-storage/async-storage': { default: inert, __esModule: true },
    react: { useSyncExternalStore: () => undefined },
    'react-native': { Platform: { OS: 'web' } },
  };
  vm.runInNewContext(output, {
    exports,
    require: (name: string) => modules[name] || inert,
    process: { env: {} }, URL, URLSearchParams, FormData, Headers, Request, Response,
    fetch: () => Promise.reject(new Error('network forbidden in unit test')),
    setTimeout, clearTimeout, console,
  });
  return exports as any;
}

const item = (overrides: Record<string, unknown> = {}) => ({
  id: 'captain-question-decision-1', provider: 'firstmate', title: 'Choose a path',
  subtitle: 'Review the current choice.', url: '/attention?item=captain-question-decision-1',
  target_id: 'decision-1', revision: '2', context: { decision_id: 'decision-1', decision_revision: 2 },
  ...overrides,
});

test('a pinned notification is current only when identity and revision both match', () => {
  const { compareDecisionDestination } = contract();
  const value = compareDecisionDestination('decision-1', '2', item());
  assert.equal(value.status, 'current');
  assert.equal(value.decisionId, 'decision-1');
  assert.equal(value.revision, 2);
});

test('stale, mismatched, missing, conflicting, and duplicate context fail closed', () => {
  const { compareDecisionDestination, decisionDestinationAllowsAction } = contract();
  for (const result of [
    compareDecisionDestination('decision-1', '1', item()),
    compareDecisionDestination('decision-2', '2', item()),
    compareDecisionDestination('decision-1', undefined, item()),
    compareDecisionDestination('decision-1', '2', null),
    compareDecisionDestination('decision-1', '2', item({ context: { decision_id: 'other', decision_revision: 2 } })),
    compareDecisionDestination(['decision-1', 'decision-1'], '2', item()),
  ]) assert.equal(decisionDestinationAllowsAction(result), false);
  assert.equal(compareDecisionDestination('decision-1', '1', item()).status, 'stale');
  assert.equal(compareDecisionDestination('decision-2', '2', item()).status, 'mismatch');
});

test('an ordinary queue visit is unpinned and does not itself execute anything', () => {
  const { compareDecisionDestination } = contract();
  assert.equal(compareDecisionDestination(undefined, undefined, item()).status, 'unpinned');
});

test('canonical answer actions require decision identity and revision, never a legacy task action', () => {
  const { currentDecisionDestination } = contract();
  assert.equal(currentDecisionDestination(item()).status, 'current');
  const legacy = item({ target_id: 'task-1', revision: '1', context: { task_id: 'task-1', decision_key: 'deploy-1' } });
  assert.equal(currentDecisionDestination(legacy).status, 'unavailable');
});
