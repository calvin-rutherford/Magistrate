import { useSyncExternalStore } from 'react';

export const PENDING_INTENT_VERSION = 1 as const;

type VoiceIntent = { version: 1; targetType: 'voice'; route: '/voice'; params: { autostart: 'true' } };
type AttentionIntent = { version: 1; targetType: 'attention'; route: '/attention'; params: { item: string } };
type AgentIntent = { version: 1; targetType: 'agent'; route: '/chat'; params: { agentId: string } };
type PullRequestIntent = { version: 1; targetType: 'pull-request'; route: '/pr-detail'; params: { number: string } };
export type PendingIntent = VoiceIntent | AttentionIntent | AgentIntent | PullRequestIntent;
export interface PendingIntentPayload {
  intent_version: number;
  target_type?: string;
  target_id?: string;
  item_id?: string;
  route: string;
}

const EMPTY: PendingIntent | null = null;
let pendingIntent: PendingIntent | null = EMPTY;
const listeners = new Set<() => void>();
const handledKeys = new Set<string>();
const MAX_HANDLED_KEYS = 32;

function emit(): void { listeners.forEach(listener => listener()); }

function safeParameter(value: string | null, pattern: RegExp): string | null {
  if (!value || value.length > 128 || !pattern.test(value)) return null;
  return value;
}

/** Parse only app-owned, version-1 routes. Malformed/external URLs are ignored. */
export function parsePendingIntent(rawUrl: string | null | undefined): PendingIntent | null {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) return null;
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
  if (!path.startsWith('/')) return null;

  if (path === '/voice' && parsed.searchParams.get('autostart') === 'true') {
    return { version: PENDING_INTENT_VERSION, targetType: 'voice', route: '/voice', params: { autostart: 'true' } };
  }
  if (path === '/attention') {
    const item = safeParameter(parsed.searchParams.get('item'), /^[A-Za-z0-9._:-]+$/);
    return item ? { version: PENDING_INTENT_VERSION, targetType: 'attention', route: '/attention', params: { item } } : null;
  }
  if (path === '/chat') {
    const agentId = safeParameter(parsed.searchParams.get('agentId'), /^[A-Za-z0-9._:-]+$/);
    return agentId ? { version: PENDING_INTENT_VERSION, targetType: 'agent', route: '/chat', params: { agentId } } : null;
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
  return `${intent.version}:${intent.targetType}:${intent.route}:${new URLSearchParams(intent.params).toString()}`;
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
  const key = pendingIntentKey(intent);
  if (pendingIntent && pendingIntentKey(pendingIntent) === key) return false;
  if (handledKeys.has(key)) return false;
  pendingIntent = intent;
  emit();
  return true;
}

export function getPendingIntent(): PendingIntent | null { return pendingIntent; }

export function consumePendingIntent(): PendingIntent | null {
  const consumed = pendingIntent;
  if (!consumed) return null;
  handledKeys.add(pendingIntentKey(consumed));
  while (handledKeys.size > MAX_HANDLED_KEYS) handledKeys.delete(handledKeys.values().next().value as string);
  pendingIntent = null;
  emit();
  return consumed;
}

export function clearPendingIntent(): void {
  if (!pendingIntent) return;
  pendingIntent = null;
  emit();
}

export function subscribePendingIntent(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePendingIntent(): PendingIntent | null {
  return useSyncExternalStore(subscribePendingIntent, getPendingIntent, getPendingIntent);
}
