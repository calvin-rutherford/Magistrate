import { useSyncExternalStore } from 'react';

export const PENDING_INTENT_VERSION = 1 as const;

type VoiceIntent = { version: 1; targetType: 'voice'; route: '/voice'; params: { autostart: 'true'; entry?: string } };
type AttentionIntent = { version: 1; targetType: 'attention'; route: '/attention'; params: { item: string; decision?: string; revision?: string } };
type AttentionOverviewIntent = { version: 1; targetType: 'attention-overview'; route: '/attention'; params: { overview: 'true' } };
type AgentIntent = { version: 1; targetType: 'agent'; route: '/chat'; params: { agentId: string } };
type MagiQueryIntent = { version: 1; targetType: 'magi-query'; route: '/chat'; params: { shortcut: 'running' } };
type PullRequestIntent = { version: 1; targetType: 'pull-request'; route: '/pr-detail'; params: { number: string } };
export type PendingIntent = (VoiceIntent | AttentionIntent | AttentionOverviewIntent | AgentIntent | MagiQueryIntent | PullRequestIntent) & { deliveryId?: string };

/** Navigation context only. Never a confirmation or permission to execute. */
export interface DecisionNotification {
  schema_version: 'magistrate.decision-notification.v1';
  decision_id: string;
  task_id: string;
  run_id: string;
  request_id: string;
  scope: string;
  revision: number;
  expires_at: number; // Unix milliseconds, from the durable runtime request.
}
const OPAQUE_ID = /^[A-Za-z0-9._:-]{1,128}$/;
export function isDecisionNotification(value: unknown): value is DecisionNotification {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  return data.schema_version === 'magistrate.decision-notification.v1'
    && ['decision_id', 'task_id', 'run_id', 'request_id', 'scope'].every(key => typeof data[key] === 'string' && OPAQUE_ID.test(data[key] as string))
    && Number.isSafeInteger(data.revision) && (data.revision as number) > 0 && (data.revision as number) <= 2_147_483_647
    && Object.keys(data).every(key => ['schema_version', 'decision_id', 'task_id', 'run_id', 'request_id', 'scope', 'revision', 'expires_at'].includes(key))
    && Number.isSafeInteger(data.expires_at) && (data.expires_at as number) > 0;
}
export interface PendingIntentPayload {
  intent_version: number;
  target_type?: string;
  target_id?: string;
  item_id?: string;
  delivery_id?: string;
  decision?: DecisionNotification;
  route: string;
}

const EMPTY: PendingIntent | null = null;
let pendingIntent: PendingIntent | null = EMPTY;
const listeners = new Set<() => void>();
const handledKeys = new Map<string, number>();
const LEGACY_DUPLICATE_WINDOW_MS = 2_000;
const consumedListeners = new Set<(intent: PendingIntent) => void>();
const MAX_HANDLED_KEYS = 32;

function emit(): void { listeners.forEach(listener => listener()); }

function safeParameter(value: string | null, pattern: RegExp): string | null {
  if (!value || value.length > 128 || !pattern.test(value)) return null;
  return value;
}

/** Parse only app-owned, version-1 routes. Malformed/external URLs are ignored. */
export function parsePendingIntent(rawUrl: string | null | undefined): PendingIntent | null {
  if (typeof rawUrl !== 'string' || !rawUrl.trim() || rawUrl.length > 2048) return null;
  let parsed: URL;
  try { parsed = new URL(rawUrl, 'magistrate://app'); } catch { return null; }
  if (!['magistrate:', 'http:', 'https:'].includes(parsed.protocol)) return null;
  if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
    if (typeof window === 'undefined' || parsed.origin !== window.location.origin) return null;
  }

  // Native links are commonly magistrate:/voice, while older shortcut links
  // may be magistrate://chat. Normalize both forms without accepting hosts
  // from arbitrary external URLs.
  const path = parsed.protocol === 'magistrate:' && parsed.hostname && parsed.hostname !== 'app'
    ? `/${parsed.hostname}${parsed.pathname === '/' ? '' : parsed.pathname}`
    : parsed.pathname;
  if (!path.startsWith('/') || parsed.username || parsed.password || parsed.hash) return null;
  if ([...parsed.searchParams.keys()].some(key => parsed.searchParams.getAll(key).length !== 1)) return null;

  if (path === '/voice' && parsed.searchParams.get('autostart') === 'true') {
    const entry = parsed.searchParams.get('entry');
    if (entry !== null && !safeParameter(entry, OPAQUE_ID)) return null;
    return { version: PENDING_INTENT_VERSION, targetType: 'voice', route: '/voice', params: { autostart: 'true', ...(entry ? { entry } : {}) } };
  }
  if (path === '/attention') {
    const item = safeParameter(parsed.searchParams.get('item'), /^[A-Za-z0-9._:-]+$/);
    if (item) {
      const decision = parsed.searchParams.get('decision');
      const revision = parsed.searchParams.get('revision');
      if (decision !== null || revision !== null) {
        if (!safeParameter(decision, OPAQUE_ID) || !revision || !/^[1-9][0-9]{0,9}$/.test(revision)) return null;
        return { version: PENDING_INTENT_VERSION, targetType: 'attention', route: '/attention', params: { item, decision: decision!, revision } };
      }
      return { version: PENDING_INTENT_VERSION, targetType: 'attention', route: '/attention', params: { item } };
    }
    return parsed.searchParams.get('overview') === 'true'
      ? { version: PENDING_INTENT_VERSION, targetType: 'attention-overview', route: '/attention', params: { overview: 'true' } }
      : null;
  }
  if (path === '/chat') {
    const agentId = safeParameter(parsed.searchParams.get('agentId'), /^[A-Za-z0-9._:-]+$/);
    if (agentId) return { version: PENDING_INTENT_VERSION, targetType: 'agent', route: '/chat', params: { agentId } };
    return parsed.searchParams.get('shortcut') === 'running'
      ? { version: PENDING_INTENT_VERSION, targetType: 'magi-query', route: '/chat', params: { shortcut: 'running' } }
      : null;
  }
  if (path === '/pr-detail') {
    const number = parsed.searchParams.get('number');
    return number && /^[1-9][0-9]{0,8}$/.test(number)
      ? { version: PENDING_INTENT_VERSION, targetType: 'pull-request', route: '/pr-detail', params: { number } }
      : null;
  }
  return null;
}

export function pendingIntentKey(intent: PendingIntent): string {
  return `${intent.version}:${intent.targetType}:${intent.route}:${new URLSearchParams(intent.params).toString()}:${intent.deliveryId || ''}`;
}

export function pendingIntentPath(intent: PendingIntent): string {
  return `${intent.route}?${new URLSearchParams(intent.params).toString()}`;
}

/** Queue one intent until the authenticated root can safely navigate to it. */
export function enqueuePendingIntent(input: string | PendingIntentPayload | null | undefined): boolean {
  if (input && typeof input === 'object' && input.intent_version !== PENDING_INTENT_VERSION) return false;
  const rawUrl = typeof input === 'string' || input == null ? input : input.route;
  const intent = parsePendingIntent(rawUrl);
  if (!intent) return false;
  if (input && typeof input === 'object') {
    const targetId = intent.targetType === 'attention' ? intent.params.item
      : intent.targetType === 'agent' ? intent.params.agentId
      : intent.targetType === 'pull-request' ? intent.params.number : undefined;
    if (input.target_type && input.target_type !== intent.targetType && !(input.target_type === 'attention' && intent.targetType === 'attention-overview')) return false;
    if (targetId && input.target_id && input.target_id !== targetId) return false;
    if (input.item_id && intent.targetType === 'attention' && input.item_id !== intent.params.item) return false;
    if (input.decision !== undefined) {
      if (!isDecisionNotification(input.decision) || intent.targetType !== 'attention'
        || intent.params.decision !== input.decision.decision_id || intent.params.revision !== String(input.decision.revision)) return false;
    }
    if (input.delivery_id !== undefined) {
      if (!safeParameter(input.delivery_id, OPAQUE_ID)) return false;
      intent.deliveryId = input.delivery_id;
    }
  }
  const key = pendingIntentKey(intent);
  if (pendingIntent && pendingIntentKey(pendingIntent) === key) return false;
  if ((handledKeys.get(key) || 0) > Date.now()) return false;
  pendingIntent = intent;
  emit();
  return true;
}

export function getPendingIntent(): PendingIntent | null { return pendingIntent; }

export function consumePendingIntent(): PendingIntent | null {
  const consumed = pendingIntent;
  if (!consumed) return null;
  const hasIdentity = consumed.deliveryId || (consumed.targetType === 'voice' && consumed.params.entry);
  handledKeys.set(pendingIntentKey(consumed), hasIdentity ? Infinity : Date.now() + LEGACY_DUPLICATE_WINDOW_MS);
  while (handledKeys.size > MAX_HANDLED_KEYS) handledKeys.delete(handledKeys.keys().next().value as string);
  pendingIntent = null;
  consumedListeners.forEach(listener => listener(consumed));
  emit();
  return consumed;
}

export function clearPendingIntent(): void {
  if (!pendingIntent) return;
  pendingIntent = null;
  emit();
}

/** OS notification responses are cleared only after the authenticated root consumes them. */
export function subscribeConsumedIntent(listener: (intent: PendingIntent) => void): () => void {
  consumedListeners.add(listener);
  return () => { consumedListeners.delete(listener); };
}

export function subscribePendingIntent(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePendingIntent(): PendingIntent | null {
  return useSyncExternalStore(subscribePendingIntent, getPendingIntent, getPendingIntent);
}
