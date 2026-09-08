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
      // Match Python's tolerant unquote at the persistence boundary: one bad
      // escape must not hide a separate percent-encoded assignment marker.
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
    kind: value.kind as CanonicalActivityKind,
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

let principalId: string | null = null;
let deliveryCursor = 0;
const records = new Map<string, CanonicalActivityRecord>();

/** In-memory only; the Gateway ledger is the durable authority. */
export function setCanonicalActivityPrincipal(principal: string | null): void {
  const nextPrincipal = principal !== null && bounded(principal, 128) ? principal : null;
  if (principalId === nextPrincipal) return;
  principalId = nextPrincipal;
  deliveryCursor = 0;
  records.clear();
}

export function getCanonicalActivityCursor(): number {
  return deliveryCursor;
}

export function getCanonicalActivityRecords(): CanonicalActivityRecord[] {
  return [...records.values()].sort((left, right) => left.sequence - right.sequence);
}

export function ingestCanonicalActivityPage(raw: unknown): boolean {
  if (!principalId || !raw || typeof raw !== 'object') return false;
  const page = raw as Record<string, unknown>;
  if (page.schema_version !== ACTIVITY_SCHEMA || !Array.isArray(page.records)
    || page.records.length > 200 || typeof page.has_more !== 'boolean'
    || !safeInteger(page.next_cursor) || !safeInteger(page.latest_cursor)
    || page.latest_cursor < page.next_cursor) return false;
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
  const kindFamily = (kind: CanonicalActivityKind): string => {
    if (kind.startsWith('objective.')) return 'objective';
    if (kind.startsWith('decision.')) return 'decision';
    return kind;
  };
  const stableIdentityMatches = (previous: CanonicalActivityRecord, activity: CanonicalActivityRecord): boolean =>
    previous.sequence === activity.sequence
      && kindFamily(previous.kind) === kindFamily(activity.kind)
      && previous.taskId === activity.taskId
      && previous.decisionKey === activity.decisionKey
      && previous.objectiveId === activity.objectiveId
      && previous.source.instanceId === activity.source.instanceId
      && previous.source.eventId === activity.source.eventId;
  for (const activity of delivered.filter(candidate => candidate.deliverySequence <= deliveryCursor)) {
    const previous = staged.get(activity.id);
    if (!previous || !stableIdentityMatches(previous, activity)) return false;
    if (activity.revision === previous.revision) {
      const { deliverySequence: _previousDelivery, ...previousContent } = previous;
      const { deliverySequence: _activityDelivery, ...activityContent } = activity;
      if (JSON.stringify(previousContent) !== JSON.stringify(activityContent)) return false;
    } else if (activity.revision > previous.revision) {
      // Gateway replay returns the current projection for an older change row.
      // Accept a causally stable newer revision without moving the cursor past
      // the delivery sequence actually observed; an older revision is ignored.
      staged.set(activity.id, { ...activity, deliverySequence: previous.deliverySequence });
    }
  }
  for (const activity of unseen) {
    const previous = staged.get(activity.id);
    if (previous && (!stableIdentityMatches(previous, activity)
      || activity.revision < previous.revision)) return false;
    if (previous && activity.revision === previous.revision) {
      const { deliverySequence: _previousDelivery, ...previousContent } = previous;
      const { deliverySequence: _activityDelivery, ...activityContent } = activity;
      if (JSON.stringify(previousContent) !== JSON.stringify(activityContent)) return false;
      staged.set(activity.id, activity);
      continue;
    }
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
  return true;
}
