import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useSyncExternalStore } from 'react';

export const ACTIVITY_SCHEMA = 'activity.v1' as const;

export type CanonicalActivityKind =
  | 'objective.started' | 'objective.progress'
  | 'decision.requested' | 'decision.resolved'
  | 'objective.completed' | 'objective.failed' | 'objective.cancelled'
  | 'supervision.outcome'
  | 'primary.message' | 'primary.final' | 'worker.message' | 'worker.final';
export type CanonicalActivityState =
  | 'active' | 'awaiting-user' | 'resolved'
  | 'completed' | 'failed' | 'cancelled';
export type CanonicalActivityRecoveryState =
  | 'idle' | 'hydrating' | 'recovering' | 'fresh' | 'observability-interrupted';

export interface CanonicalActivityRecord {
  id: string;
  sequence: number;
  deliverySequence: number;
  revision: number;
  kind: CanonicalActivityKind;
  state: CanonicalActivityState;
  importance: 'routine' | 'attention';
  title: string;
  summary: string;
  summaryTruncated: boolean;
  taskId?: string;
  decisionKey?: string;
  objectiveId?: string;
  runId?: string;
  project?: string;
  occurredAt?: number;
  observedAt: number;
  refs: ({ kind: 'pull-request'; url: string } | { kind: 'report'; id: string })[];
  source: { instanceId: string; eventId?: string };
}

export interface CanonicalActivitySummary {
  activeObjectives: number;
  operationCount: number;
  pendingDecisions: number;
}

export interface CanonicalActivitySnapshot {
  records: readonly CanonicalActivityRecord[];
  cursor: number;
  recoveryState: CanonicalActivityRecoveryState;
  cached: boolean;
  summary: CanonicalActivitySummary;
  summaryAuthoritative: boolean;
}

export type CanonicalWorkPhase =
  | 'idle' | 'active' | 'awaiting-user'
  | 'recovering' | 'observability-interrupted';

export interface CanonicalWorkState {
  active: boolean;
  phase: CanonicalWorkPhase;
  operationCount: number;
  pendingDecisions: number;
  objectiveIds: string[];
  runIds: string[];
}

const KINDS = new Set<CanonicalActivityKind>([
  'objective.started', 'objective.progress', 'decision.requested', 'decision.resolved',
  'objective.completed', 'objective.failed', 'objective.cancelled', 'supervision.outcome',
  'primary.message', 'primary.final', 'worker.message', 'worker.final',
]);
const STATES = new Set<CanonicalActivityState>([
  'active', 'awaiting-user', 'resolved', 'completed', 'failed', 'cancelled',
]);
const KIND_STATES: Record<CanonicalActivityKind, ReadonlySet<CanonicalActivityState>> = {
  'objective.started': new Set(['active']),
  'objective.progress': new Set(['active', 'awaiting-user']),
  'decision.requested': new Set(['awaiting-user']),
  'decision.resolved': new Set(['resolved']),
  'objective.completed': new Set(['completed']),
  'objective.failed': new Set(['failed']),
  'objective.cancelled': new Set(['cancelled']),
  'supervision.outcome': new Set(['completed']),
  'primary.message': new Set(['completed']),
  'primary.final': new Set(['completed']),
  'worker.message': new Set(['completed']),
  'worker.final': new Set(['completed']),
};
const RECORD_KEYS = new Set([
  'id', 'sequence', 'delivery_sequence', 'revision', 'kind', 'state', 'importance',
  'title', 'summary', 'summary_truncated', 'task_id', 'decision_key', 'objective_id',
  'run_id', 'project', 'occurred_at', 'observed_at', 'refs', 'source',
]);
const CACHE_PREFIX = 'magistrate.activity.canonical.v1.';
const CACHE_SCHEMA = 'activity-cache.v1';
const MAX_CACHE_RECORDS = 400;
const EMPTY_SUMMARY: CanonicalActivitySummary = {
  activeObjectives: 0, operationCount: 0, pendingDecisions: 0,
};
const ENV_ASSIGNMENT = /(?:^|[^A-Za-z0-9_])(?:export\s+)?[A-Za-z_][A-Za-z0-9_]{0,127}\s*(?:\+\s*)?=/i;
const SENSITIVE_TEXT = /(?:(?:proxy[-_ ]?)?authorization\s*["']?\s*[:=]\s*[^\s,;}]+(?:\s+[^\s,;}]+)?|["']?[A-Za-z0-9_. -]{0,96}(?:secret|pass(?:word|wd)?|pwd|token|auth|key|credential)[A-Za-z0-9_. -]{0,96}["']?\s*[:=]\s*["']?[^\s,;}"']+|\b[A-Z][A-Z0-9_]{1,63}\s*=\s*[a-z][a-z0-9+.-]*:\/\/[^/\s:@]+:[^@\s]+@|\b[a-z][a-z0-9+.-]{0,31}:\/\/[^\s/@]+@[^\s,;]+|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{8,}|\bsk-[A-Za-z0-9]{8,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)/i;
const containsSensitiveText = (value: string): boolean => {
  let decoded = value;
  for (let pass = 0; pass < 3; pass += 1) {
    if (ENV_ASSIGNMENT.test(decoded) || SENSITIVE_TEXT.test(decoded)) return true;
    try {
      const expanded = decodeURIComponent(decoded);
      if (expanded === decoded) return false;
      decoded = expanded;
    } catch {
      // Match Python's tolerant unquote: one malformed escape must not hide a
      // separate percent-encoded assignment marker in persisted source text.
      const expanded = decoded.replace(/%([0-9A-Fa-f]{2})/g, (_match, hex: string) =>
        String.fromCharCode(Number.parseInt(hex, 16)));
      if (expanded === decoded) return false;
      decoded = expanded;
    }
  }
  return ENV_ASSIGNMENT.test(decoded) || SENSITIVE_TEXT.test(decoded);
};
const safeInteger = (value: unknown, minimum = 0): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
const bounded = (value: unknown, maximum: number): value is string =>
  typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
  && !Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || (code >= 127 && code <= 159)
      || (code >= 0xd800 && code <= 0xdfff) || code === 0x2028 || code === 0x2029;
  });
const optionalBounded = (value: unknown, maximum: number): value is string | null | undefined =>
  value === undefined || value === null || bounded(value, maximum);
const safeHttpsUrl = (value: unknown): value is string => {
  if (!bounded(value, 2048)) return false;
  const github = /^https:\/\/github\.com\/([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9-]{0,37}[A-Za-z0-9])\/([A-Za-z0-9._-]{1,100})\/pull\/([1-9][0-9]*)$/.exec(value);
  if (github) return !github[1].includes('--') && github[2] !== '.' && github[2] !== '..';
  const gitlab = /^https:\/\/([a-z0-9.-]{1,253})\/([A-Za-z0-9._/-]+)\/-\/merge_requests\/([1-9][0-9]*)$/.exec(value);
  if (!gitlab || gitlab[1] === 'github.com' || gitlab[1].startsWith('.')
    || gitlab[1].endsWith('.') || gitlab[1].includes('..')) return false;
  const labels = gitlab[1].split('.');
  if (labels.some(label => !label || label.length > 63 || label.startsWith('-') || label.endsWith('-'))) return false;
  const path = gitlab[2];
  const segments = path.split('/');
  return path.length >= 3 && path.length <= 1024 && segments.length >= 2 && segments.length <= 20
    && segments.every(segment => !!segment && segment.length <= 255 && segment !== '.' && segment !== '..'
      && !segment.startsWith('-') && !segment.endsWith('.git') && !segment.endsWith('.atom'));
};

export function normalizeCanonicalActivityRecord(raw: unknown): CanonicalActivityRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== RECORD_KEYS.size
    || Object.keys(value).some(key => !RECORD_KEYS.has(key))) return null;
  if (!bounded(value.id, 128) || !safeInteger(value.sequence, 1)
    || !safeInteger(value.delivery_sequence, 1) || !safeInteger(value.revision, 1)
    || !KINDS.has(value.kind as CanonicalActivityKind)
    || !STATES.has(value.state as CanonicalActivityState)
    || !KIND_STATES[value.kind as CanonicalActivityKind]?.has(value.state as CanonicalActivityState)
    || (value.importance !== 'routine' && value.importance !== 'attention')
    || !bounded(value.title, 240) || !bounded(value.summary, 600)
    || typeof value.summary_truncated !== 'boolean'
    || containsSensitiveText(value.title) || containsSensitiveText(value.summary)
    || !optionalBounded(value.task_id, 200) || !optionalBounded(value.decision_key, 128)
    || !optionalBounded(value.objective_id, 128) || !optionalBounded(value.run_id, 128)
    || !optionalBounded(value.project, 160)
    || (value.occurred_at !== null && value.occurred_at !== undefined && !safeInteger(value.occurred_at))
    || !safeInteger(value.observed_at)
    || !value.source || typeof value.source !== 'object' || !Array.isArray(value.refs)) return null;
  const kind = value.kind as CanonicalActivityKind;
  const decisionKind = kind === 'decision.requested' || kind === 'decision.resolved';
  if (!bounded(value.objective_id, 128)
    || (decisionKind && (!bounded(value.task_id, 200) || !bounded(value.decision_key, 128)))
    || (!decisionKind && value.decision_key !== null)
    || (kind.startsWith('primary.') ? value.task_id !== null : !bounded(value.task_id, 200))
    || [value.task_id, value.decision_key, value.objective_id, value.run_id, value.project]
      .some(candidate => typeof candidate === 'string' && containsSensitiveText(candidate))) return null;
  const source = value.source as Record<string, unknown>;
  if (Object.keys(source).length !== 2
    || Object.keys(source).some(key => key !== 'instance_id' && key !== 'event_id')
    || !bounded(source.instance_id, 128) || !optionalBounded(source.event_id, 200)
    || containsSensitiveText(source.instance_id)
    || (typeof source.event_id === 'string' && containsSensitiveText(source.event_id))) return null;
  if (value.refs.length > 8) return null;
  const refs: CanonicalActivityRecord['refs'] = [];
  for (const candidate of value.refs) {
    if (!candidate || typeof candidate !== 'object') return null;
    const reference = candidate as Record<string, unknown>;
    if (reference.kind === 'pull-request' && Object.keys(reference).length === 2
      && safeHttpsUrl(reference.url)) {
      refs.push({ kind: 'pull-request', url: reference.url });
    } else if (reference.kind === 'report' && Object.keys(reference).length === 2
      && bounded(reference.id, 200)
      && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(reference.id)) {
      refs.push({ kind: 'report', id: reference.id });
    } else return null;
  }
  return {
    id: value.id,
    sequence: value.sequence,
    deliverySequence: value.delivery_sequence,
    revision: value.revision,
    kind,
    state: value.state as CanonicalActivityState,
    importance: value.importance,
    title: value.title,
    summary: value.summary,
    summaryTruncated: value.summary_truncated,
    taskId: value.task_id || undefined,
    decisionKey: value.decision_key || undefined,
    objectiveId: value.objective_id || undefined,
    runId: value.run_id || undefined,
    project: value.project || undefined,
    occurredAt: value.occurred_at ?? undefined,
    observedAt: value.observed_at,
    refs,
    source: { instanceId: source.instance_id, eventId: source.event_id || undefined },
  };
}

const toWireRecord = (record: CanonicalActivityRecord): Record<string, unknown> => ({
  id: record.id,
  sequence: record.sequence,
  delivery_sequence: record.deliverySequence,
  revision: record.revision,
  kind: record.kind,
  state: record.state,
  importance: record.importance,
  title: record.title,
  summary: record.summary,
  summary_truncated: record.summaryTruncated,
  task_id: record.taskId ?? null,
  decision_key: record.decisionKey ?? null,
  objective_id: record.objectiveId ?? null,
  run_id: record.runId ?? null,
  project: record.project ?? null,
  occurred_at: record.occurredAt ?? null,
  observed_at: record.observedAt,
  refs: record.refs,
  source: { instance_id: record.source.instanceId, event_id: record.source.eventId ?? null },
});

const kindFamily = (kind: CanonicalActivityKind): string => {
  if (kind.startsWith('objective.')) return 'objective';
  if (kind.startsWith('decision.')) return 'decision';
  return kind;
};
const stableIdentityMatches = (
  previous: CanonicalActivityRecord, activity: CanonicalActivityRecord,
): boolean => previous.sequence === activity.sequence
  && kindFamily(previous.kind) === kindFamily(activity.kind)
  && previous.taskId === activity.taskId
  && previous.decisionKey === activity.decisionKey
  && previous.objectiveId === activity.objectiveId
  && previous.source.instanceId === activity.source.instanceId
  && previous.source.eventId === activity.source.eventId;
const sameRevisionContent = (
  previous: CanonicalActivityRecord, activity: CanonicalActivityRecord,
): boolean => {
  const { deliverySequence: _previousDelivery, ...previousContent } = previous;
  const { deliverySequence: _activityDelivery, ...activityContent } = activity;
  return JSON.stringify(previousContent) === JSON.stringify(activityContent);
};

let principalId: string | null = null;
let deliveryCursor = 0;
let summaryCursor = 0;
let summary: CanonicalActivitySummary = EMPTY_SUMMARY;
let summaryAuthoritative = false;
let recoveryState: CanonicalActivityRecoveryState = 'idle';
let restoredFromCache = false;
let snapshotBaseline = false;
let writeChain: Promise<void> = Promise.resolve();
let principalTransition: Promise<void> = Promise.resolve();
const records = new Map<string, CanonicalActivityRecord>();
const listeners = new Set<() => void>();
let published: CanonicalActivitySnapshot = {
  records: [], cursor: 0, recoveryState: 'idle', cached: false,
  summary: EMPTY_SUMMARY, summaryAuthoritative: false,
};

const storageKey = (principal: string): string => `${CACHE_PREFIX}${encodeURIComponent(principal)}`;
const sortedRecords = (): CanonicalActivityRecord[] =>
  [...records.values()].sort((left, right) => left.sequence - right.sequence);
const cacheRecords = (): CanonicalActivityRecord[] => {
  const ordered = sortedRecords();
  const focus = ordered.filter(activity => (
    (activity.kind === 'objective.started' || activity.kind === 'objective.progress'
      || activity.kind === 'decision.requested')
    && (activity.state === 'active' || activity.state === 'awaiting-user')
  )).slice(-200);
  const focusedIds = new Set(focus.map(activity => activity.id));
  const recent = [...ordered].reverse().filter(activity => !focusedIds.has(activity.id))
    .slice(0, MAX_CACHE_RECORDS - focus.length);
  return [...focus, ...recent].sort((left, right) => left.sequence - right.sequence);
};
const publish = (): void => {
  published = {
    records: sortedRecords(), cursor: deliveryCursor, recoveryState,
    cached: restoredFromCache, summary: { ...summary }, summaryAuthoritative,
  };
  listeners.forEach(listener => listener());
};
const persist = (): void => {
  const owner = principalId;
  if (!owner) return;
  const payload = JSON.stringify({
    schema_version: CACHE_SCHEMA,
    principal: owner,
    cursor: deliveryCursor,
    summary_cursor: summaryCursor,
    summary_authoritative: summaryAuthoritative,
    summary: {
      active_objectives: summary.activeObjectives,
      operation_count: summary.operationCount,
      pending_decisions: summary.pendingDecisions,
    },
    records: cacheRecords().map(toWireRecord),
  });
  writeChain = writeChain.catch(() => {}).then(async () => {
    if (principalId !== owner) return;
    await AsyncStorage.setItem(storageKey(owner), payload);
  }).catch(() => {});
};

/** Clear memory synchronously before any protected screen for another account can paint. */
export function setCanonicalActivityPrincipal(principal: string | null): void {
  const nextPrincipal = principal !== null && bounded(principal, 128) ? principal : null;
  if (principalId === nextPrincipal) return;
  const pendingWrite = writeChain;
  principalId = nextPrincipal;
  deliveryCursor = 0;
  summaryCursor = 0;
  summary = EMPTY_SUMMARY;
  summaryAuthoritative = false;
  recoveryState = nextPrincipal ? 'hydrating' : 'idle';
  restoredFromCache = false;
  snapshotBaseline = false;
  records.clear();
  publish();
  const transitionPrincipal = nextPrincipal;
  principalTransition = principalTransition.catch(() => {}).then(async () => {
    await pendingWrite.catch(() => {});
    if (principalId !== transitionPrincipal) return;
    try {
      const keys = await AsyncStorage.getAllKeys();
      if (principalId !== transitionPrincipal) return;
      const keep = transitionPrincipal ? storageKey(transitionPrincipal) : null;
      const stale = keys.filter(key => key.startsWith(CACHE_PREFIX) && key !== keep);
      if (stale.length) await AsyncStorage.multiRemove(stale);
    } catch { /* durable cache failure cannot weaken synchronous account isolation */ }
  });
}

export async function settleCanonicalActivityPrincipal(): Promise<void> {
  await principalTransition;
}

export async function hydrateCanonicalActivity(): Promise<boolean> {
  const owner = principalId;
  if (!owner) return false;
  await principalTransition.catch(() => {});
  if (principalId !== owner) return false;
  let raw: string | null = null;
  try { raw = await AsyncStorage.getItem(storageKey(owner)); } catch { /* unavailable cache */ }
  if (principalId !== owner) return false;
  if (!raw) {
    recoveryState = 'recovering';
    publish();
    return false;
  }
  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    const wireSummary = payload.summary as Record<string, unknown> | undefined;
    if (payload.schema_version !== CACHE_SCHEMA || payload.principal !== owner
      || !safeInteger(payload.cursor) || !safeInteger(payload.summary_cursor)
      || typeof payload.summary_authoritative !== 'boolean'
      || !Array.isArray(payload.records) || payload.records.length > MAX_CACHE_RECORDS
      || !wireSummary || !safeInteger(wireSummary.active_objectives)
      || !safeInteger(wireSummary.operation_count) || !safeInteger(wireSummary.pending_decisions)) {
      throw new Error('invalid activity cache');
    }
    const normalized = payload.records.map(normalizeCanonicalActivityRecord);
    if (normalized.some(record => record === null)) throw new Error('invalid activity record');
    const staged = new Map<string, CanonicalActivityRecord>();
    const sequences = new Set<number>();
    for (const activity of normalized as CanonicalActivityRecord[]) {
      if (activity.deliverySequence > (payload.cursor as number) || staged.has(activity.id)
        || sequences.has(activity.sequence)) throw new Error('invalid activity cache identity');
      staged.set(activity.id, activity);
      sequences.add(activity.sequence);
    }
    if (principalId !== owner) return false;
    records.clear();
    staged.forEach((activity, id) => records.set(id, activity));
    deliveryCursor = payload.cursor as number;
    summaryCursor = payload.summary_cursor as number;
    summary = {
      activeObjectives: wireSummary.active_objectives as number,
      operationCount: wireSummary.operation_count as number,
      pendingDecisions: wireSummary.pending_decisions as number,
    };
    summaryAuthoritative = payload.summary_authoritative as boolean;
    restoredFromCache = true;
    snapshotBaseline = true;
    recoveryState = 'recovering';
    publish();
    return true;
  } catch {
    try { await AsyncStorage.removeItem(storageKey(owner)); } catch { /* best effort */ }
    if (principalId === owner) {
      recoveryState = 'recovering';
      publish();
    }
    return false;
  }
}

export function getCanonicalActivityCursor(): number { return deliveryCursor; }
export function getCanonicalActivityRecords(): CanonicalActivityRecord[] { return sortedRecords(); }
export function getCanonicalActivitySnapshot(): CanonicalActivitySnapshot { return published; }
export function useCanonicalActivity(): CanonicalActivitySnapshot {
  const subscribe = useCallback((listener: () => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, []);
  return useSyncExternalStore(subscribe, getCanonicalActivitySnapshot, getCanonicalActivitySnapshot);
}

export function markCanonicalActivityRecovering(): void {
  if (!principalId || recoveryState === 'recovering') return;
  recoveryState = 'recovering';
  publish();
}
export function markCanonicalActivityFresh(): void {
  if (!principalId || recoveryState === 'fresh') return;
  recoveryState = 'fresh';
  restoredFromCache = false;
  publish();
  persist();
}
export function markCanonicalActivityInterrupted(): void {
  if (!principalId || recoveryState === 'observability-interrupted') return;
  recoveryState = 'observability-interrupted';
  publish();
}

export function canonicalActivityResponseIsDegraded(raw: unknown): boolean {
  if (!raw || typeof raw !== 'object') return true;
  const value = raw as Record<string, unknown>;
  if (value.reconciliation !== 'available' && value.reconciliation !== 'not-requested') return true;
  if (!Array.isArray(value.sources)) return false;
  return value.sources.some(source => !source || typeof source !== 'object'
    || (source as Record<string, unknown>).state !== 'available');
}

export function ingestCanonicalActivityPage(raw: unknown): boolean {
  if (!principalId || !raw || typeof raw !== 'object') return false;
  const page = raw as Record<string, unknown>;
  const wireSummary = page.summary as Record<string, unknown> | undefined;
  if (page.schema_version !== ACTIVITY_SCHEMA || !Array.isArray(page.records)
    || page.records.length > 200 || typeof page.has_more !== 'boolean'
    || !safeInteger(page.next_cursor) || !safeInteger(page.latest_cursor)
    || page.latest_cursor < page.next_cursor
    || (wireSummary !== undefined && (!safeInteger(wireSummary.active_objectives)
      || !safeInteger(wireSummary.operation_count)
      || !safeInteger(wireSummary.pending_decisions)))) return false;
  const normalized = page.records.map(normalizeCanonicalActivityRecord);
  if (normalized.some(record => record === null)) return false;
  const delivered = normalized as CanonicalActivityRecord[];
  for (let index = 1; index < delivered.length; index += 1) {
    if (delivered[index].deliverySequence !== delivered[index - 1].deliverySequence + 1) return false;
  }
  if (delivered.length > 0 && delivered.at(-1)?.deliverySequence !== page.next_cursor) return false;
  const unseen = delivered.filter(activity => activity.deliverySequence > deliveryCursor);
  if (unseen.length > 0 && unseen[0].deliverySequence !== deliveryCursor + 1) return false;
  if (unseen.length === 0 && page.next_cursor > deliveryCursor) return false;
  const staged = new Map(records);
  for (const activity of delivered.filter(candidate => candidate.deliverySequence <= deliveryCursor)) {
    const previous = staged.get(activity.id);
    if (!previous) {
      if (snapshotBaseline) continue;
      return false;
    }
    if (!stableIdentityMatches(previous, activity)) return false;
    if (activity.revision === previous.revision) {
      if (!sameRevisionContent(previous, activity)) return false;
    } else if (activity.revision > previous.revision) {
      staged.set(activity.id, { ...activity, deliverySequence: previous.deliverySequence });
    }
  }
  for (const activity of unseen) {
    const previous = staged.get(activity.id);
    if (previous && (!stableIdentityMatches(previous, activity)
      || activity.revision < previous.revision)) return false;
    if (previous && activity.revision === previous.revision
      && !sameRevisionContent(previous, activity)) return false;
    staged.set(activity.id, activity);
  }
  const sequences = new Set<number>();
  for (const activity of staged.values()) {
    if (sequences.has(activity.sequence)) return false;
    sequences.add(activity.sequence);
  }
  records.clear();
  staged.forEach((activity, id) => records.set(id, activity));
  deliveryCursor = Math.max(deliveryCursor, page.next_cursor);
  if (wireSummary && (page.latest_cursor as number) >= summaryCursor) {
    summaryCursor = page.latest_cursor as number;
    summary = {
      activeObjectives: wireSummary.active_objectives as number,
      operationCount: wireSummary.operation_count as number,
      pendingDecisions: wireSummary.pending_decisions as number,
    };
    summaryAuthoritative = true;
  } else if (!wireSummary && (page.next_cursor as number) > summaryCursor) {
    summaryAuthoritative = false;
  }
  publish();
  persist();
  return true;
}

export interface CanonicalActivitySnapshotPage {
  nextBefore?: number;
  hasMore: boolean;
  snapshotCursor: number;
}

export function ingestCanonicalActivitySnapshot(raw: unknown): CanonicalActivitySnapshotPage | null {
  if (!principalId || !raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const wireSummary = value.summary as Record<string, unknown> | undefined;
  if (value.schema_version !== ACTIVITY_SCHEMA || !Array.isArray(value.records)
    || !Array.isArray(value.focus_records) || value.records.length > 200
    || value.focus_records.length > 200 || typeof value.focus_truncated !== 'boolean'
    || !safeInteger(value.snapshot_cursor) || !safeInteger(value.latest_sequence)
    || (value.next_before !== null && value.next_before !== undefined && !safeInteger(value.next_before, 1))
    || typeof value.has_more !== 'boolean' || !wireSummary
    || !safeInteger(wireSummary.active_objectives)
    || !safeInteger(wireSummary.operation_count)
    || !safeInteger(wireSummary.pending_decisions)) return null;
  const normalized = [...value.records, ...value.focus_records].map(normalizeCanonicalActivityRecord);
  if (normalized.some(record => record === null)) return null;
  const delivered = normalized as CanonicalActivityRecord[];
  const staged = new Map(records);
  const authoritativeAtCursor = (value.snapshot_cursor as number) >= deliveryCursor;
  if (authoritativeAtCursor && value.focus_truncated === false) {
    const represented = new Set(delivered.map(activity => activity.id));
    for (const [id, activity] of staged) {
      const isRecoverableFocus = (activity.kind === 'objective.started'
        || activity.kind === 'objective.progress' || activity.kind === 'decision.requested')
        && (activity.state === 'active' || activity.state === 'awaiting-user');
      if (isRecoverableFocus && !represented.has(id)) staged.delete(id);
    }
  }
  for (const activity of delivered) {
    if (activity.deliverySequence > (value.snapshot_cursor as number)) return null;
    const previous = staged.get(activity.id);
    if (previous && !stableIdentityMatches(previous, activity)) return null;
    if (previous && activity.revision < previous.revision) continue;
    if (previous && activity.revision === previous.revision) {
      if (!sameRevisionContent(previous, activity)) return null;
      staged.set(activity.id, {
        ...previous, deliverySequence: Math.max(previous.deliverySequence, activity.deliverySequence),
      });
    } else staged.set(activity.id, activity);
  }
  const sequences = new Map<number, string>();
  for (const activity of staged.values()) {
    const owner = sequences.get(activity.sequence);
    if (owner && owner !== activity.id) return null;
    sequences.set(activity.sequence, activity.id);
  }
  records.clear();
  staged.forEach((activity, id) => records.set(id, activity));
  deliveryCursor = Math.max(deliveryCursor, value.snapshot_cursor as number);
  if ((value.snapshot_cursor as number) >= summaryCursor) {
    summaryCursor = value.snapshot_cursor as number;
    summary = {
      activeObjectives: wireSummary.active_objectives as number,
      operationCount: wireSummary.operation_count as number,
      pendingDecisions: wireSummary.pending_decisions as number,
    };
    summaryAuthoritative = true;
  }
  snapshotBaseline = true;
  publish();
  persist();
  return {
    nextBefore: value.next_before as number | undefined,
    hasMore: value.has_more,
    snapshotCursor: value.snapshot_cursor as number,
  };
}

export function deriveCanonicalWorkState(
  activity: CanonicalActivitySnapshot,
  messages: readonly {
    canonicalId?: string; objectiveId?: string; runId?: string; lifecycleState?: string;
    progress?: string; decisionKey?: string;
  }[],
): CanonicalWorkState {
  const objectiveIds = new Set<string>();
  const runIds = new Set<string>();
  let messageActive = false;
  let messageAwaiting = false;
  let recordActive = false;
  let recordAwaiting = false;
  for (const message of messages) {
    if (!message.canonicalId) continue;
    const active = message.lifecycleState === 'active'
      || (!message.lifecycleState && (message.progress === 'working' || message.progress === 'streaming'));
    const awaiting = message.lifecycleState === 'awaiting-user';
    if (active || awaiting) {
      messageActive = true;
      if (message.objectiveId) objectiveIds.add(message.objectiveId);
      if (message.runId) runIds.add(message.runId);
    }
    if (awaiting) messageAwaiting = true;
  }
  for (const record of activity.records) {
    if ((record.kind === 'objective.started' || record.kind === 'objective.progress')
      && (record.state === 'active' || record.state === 'awaiting-user')) {
      recordActive = true;
      if (record.objectiveId) objectiveIds.add(record.objectiveId);
      if (record.runId) runIds.add(record.runId);
      if (record.state === 'awaiting-user') recordAwaiting = true;
    }
  }
  const active = messageActive || (activity.summaryAuthoritative
    ? activity.summary.activeObjectives > 0 : recordActive);
  const awaitingUser = messageAwaiting || (activity.summaryAuthoritative
    ? activity.summary.pendingDecisions > 0 : recordAwaiting);
  const inferredOperationCount = activity.records.filter(record =>
    !!record.objectiveId && objectiveIds.has(record.objectiveId)
    && !record.kind.startsWith('objective.') && !record.kind.startsWith('decision.')).length;
  let phase: CanonicalWorkPhase = 'idle';
  if (active) {
    if (activity.recoveryState === 'observability-interrupted') phase = 'observability-interrupted';
    else if (activity.recoveryState === 'hydrating' || activity.recoveryState === 'recovering') phase = 'recovering';
    else if (awaitingUser) phase = 'awaiting-user';
    else phase = 'active';
  }
  return {
    active,
    phase,
    operationCount: activity.summaryAuthoritative
      ? activity.summary.operationCount : inferredOperationCount,
    pendingDecisions: activity.summaryAuthoritative
      ? activity.summary.pendingDecisions
      : activity.records.filter(record => record.kind === 'decision.requested'
        && record.state === 'awaiting-user').length,
    objectiveIds: [...objectiveIds],
    runIds: [...runIds],
  };
}

export function decisionAttentionItemId(decisionKey: string | undefined): string | null {
  if (!decisionKey || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(decisionKey)) return null;
  return `captain-question-${decisionKey}`;
}
