import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useSyncExternalStore } from 'react';

export interface ConversationAttachment {
  name: string;
  mediaType: string;
  size?: number;
  /**
   * Real processing state, never optimistic. 'uploading' until the gateway
   * confirms storage, 'stored' once it has, 'attached' once the prompt carrying
   * it was accepted, and 'failed' when the upload was rejected.
   */
  status?: 'uploading' | 'stored' | 'attached' | 'failed';
  /** Server-issued upload id; absent while the upload is still local. */
  uploadId?: string;
}

const ATTACHMENT_STATES = new Set(['uploading', 'stored', 'attached', 'failed']);

/** Provenance supplied by the gateway/provider; never inferred from prose. */
export interface ConversationSource {
  id: string;
  title: string;
  url: string;
  publisher?: string;
  retrievedAt?: string;
  quote?: string;
  page?: string | number;
}

export type ConversationProgress = 'queued' | 'working' | 'streaming' | 'complete' | 'failed' | 'cancelled';

export interface ConversationMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  // Locally submitted messages keep the wall-clock time captured at the send
  // action. Herdr snapshots have no reliable time, so discovered messages may
  // omit it; callers must never manufacture one during hydration or refresh.
  sentAt?: number;
  source: 'text' | 'voice';
  kind?: 'conversation' | 'tool';
  attachments?: ConversationAttachment[];
  /** Safe, bounded labels for structured tool results attached to this reply. */
  toolResults?: string[];
  /** Structured provenance only; arbitrary URLs in text are not promoted here. */
  sources?: ConversationSource[];
  /** Provider-labelled summary only; raw reasoning is never stored or rendered. */
  thinkingSummary?: { provider: string; text: string };
  /** A future backend may opt a run into idempotent regeneration. */
  runId?: string;
  regenerateSafe?: boolean;
  progress?: ConversationProgress;
  /** Explicit conversation boundary; terminal-derived rows without it are not restored. */
  audience?: 'captain' | 'primary';
  delivery?: 'sending' | 'sent' | 'failed' | 'cancelled';
  /**
   * Identity issued by the gateway's canonical conversation record (see
   * CanonicalConversation.ts). Present on every captain row; absent on a row
   * still in flight and on terminal-derived worker-pane rows.
   */
  canonicalId?: string;
  turnId?: string;
  sequenceIndex?: number;
}

// The active thread is kept in memory for reactive rendering and mirrored as
// normalized messages in AsyncStorage. Terminal scrollback is never persisted;
// Chat and Voice Mode share the 'captain' target in-session.
const messagesByTarget = new Map<string, ConversationMessage[]>();
const listenersByTarget = new Map<string, Set<() => void>>();
const EMPTY_MESSAGES: ConversationMessage[] = [];
// v2 is the canonical-record era. The v1 mirror was written from mutable
// terminal history and can hold duplicated or contaminated rows, so it is
// deleted rather than migrated: the gateway is the transcript now, and
// resurfacing v1 state would reintroduce exactly the rows this replaced.
const STORAGE_PREFIX = 'magistrate.chat.messages.v2.';
const LEGACY_STORAGE_PREFIXES = ['magistrate.chat.messages.'];
const discardLegacyStorage = (target: string) => {
  const suffix = encodeURIComponent(target);
  void Promise.all(LEGACY_STORAGE_PREFIXES.map(prefix => AsyncStorage.removeItem(prefix + suffix))).catch(() => {});
};
const persist = (target: string, messages: ConversationMessage[]) => {
  void AsyncStorage.setItem(STORAGE_PREFIX + encodeURIComponent(target), JSON.stringify(messages)).catch(() => {});
};

export async function hydrateConversationMessages(target: string): Promise<ConversationMessage[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_PREFIX + encodeURIComponent(target));
    discardLegacyStorage(target);
    if (!raw) return getConversationMessages(target);
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return getConversationMessages(target);
    const normalized = parsed.filter(item => {
      if (!item || typeof item.id !== 'string' || (item.role !== 'user' && item.role !== 'assistant') || typeof item.text !== 'string') return false;
      // PR #57 persisted every typed terminal-history row. Restore only rows
      // known to have crossed the captain/primary boundary, while retaining
      // locally-created messages from older app versions.
      const trustedLegacyId = item.role === 'user' ? /^(?:u-|voice-u-)/.test(item.id) : /^(?:a-|voice-a-)/.test(item.id);
      // A canonical row carries a server-issued id, so a tool event restored
      // with one is a real recorded event rather than a parsed terminal row.
      const canonical = typeof item.canonicalId === 'string' && item.canonicalId.length > 0;
      if (item.kind === 'tool' && !canonical) return false;
      return item.audience === (item.role === 'user' ? 'captain' : 'primary') || trustedLegacyId || canonical;
    }).map(item => {
      const value = item as Record<string, unknown>;
      const attachments = Array.isArray(value.attachments) ? value.attachments.filter(attachment => {
        if (!attachment || typeof attachment !== 'object') return false;
        const candidate = attachment as Record<string, unknown>;
        return typeof candidate.name === 'string' && candidate.name.length > 0 && candidate.name.length <= 160
          && typeof candidate.mediaType === 'string' && candidate.mediaType.length <= 128
          && (candidate.size === undefined || (typeof candidate.size === 'number' && Number.isSafeInteger(candidate.size) && candidate.size >= 0 && candidate.size <= 25 * 1024 * 1024));
      }).map(attachment => {
        const candidate = attachment as Record<string, unknown>;
        // A restored attachment whose state we cannot verify is not claimed as
        // processed; the unknown state renders without a success label.
        const status = ATTACHMENT_STATES.has(String(candidate.status)) ? candidate.status as ConversationAttachment['status'] : undefined;
        const uploadId = typeof candidate.uploadId === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(candidate.uploadId) ? candidate.uploadId : undefined;
        return { name: candidate.name as string, mediaType: candidate.mediaType as string, size: candidate.size as number | undefined, status, uploadId };
      }) : undefined;
      const sentAt = typeof value.sentAt === 'number' && Number.isFinite(value.sentAt) && value.sentAt >= 0 ? value.sentAt : undefined;
      const toolResults = Array.isArray(value.toolResults)
        ? value.toolResults.filter(result => typeof result === 'string' && result.length > 0 && result.length <= 48).slice(0, 6) as string[]
        : undefined;
      const sources = Array.isArray(value.sources) ? value.sources.filter(source => {
        if (!source || typeof source !== 'object') return false;
        const candidate = source as Record<string, unknown>;
        if (typeof candidate.id !== 'string' || candidate.id.length < 1 || candidate.id.length > 120 || typeof candidate.title !== 'string' || !candidate.title.trim() || candidate.title.length > 240 || typeof candidate.url !== 'string' || !/^https?:\/\//i.test(candidate.url)) return false;
        return !candidate.publisher || (typeof candidate.publisher === 'string' && candidate.publisher.length <= 120);
      }).slice(0, 20).map(source => {
        const candidate = source as Record<string, unknown>;
        return { id: candidate.id as string, title: candidate.title as string, url: candidate.url as string, publisher: candidate.publisher as string | undefined, retrievedAt: typeof candidate.retrievedAt === 'string' ? candidate.retrievedAt : undefined, quote: typeof candidate.quote === 'string' ? candidate.quote.slice(0, 500) : undefined, page: typeof candidate.page === 'string' || typeof candidate.page === 'number' ? candidate.page : undefined };
      }) : undefined;
      const thinkingSummary = value.thinkingSummary && typeof value.thinkingSummary === 'object' ? (() => { const candidate = value.thinkingSummary as Record<string, unknown>; return typeof candidate.provider === 'string' && typeof candidate.text === 'string' ? { provider: candidate.provider.slice(0, 48), text: candidate.text.slice(0, 280) } : undefined; })() : undefined;
      const progress = ['queued', 'working', 'streaming', 'complete', 'failed', 'cancelled'].includes(String(value.progress)) ? value.progress as ConversationMessage['progress'] : undefined;
      const role = value.role as 'user' | 'assistant';
      return { ...value, sentAt, source: value.source === 'voice' ? 'voice' : 'text', kind: value.kind === 'tool' ? 'tool' : 'conversation', attachments, toolResults, sources, thinkingSummary, runId: typeof value.runId === 'string' && value.runId.length <= 160 ? value.runId : undefined, regenerateSafe: value.regenerateSafe === true, progress, audience: role === 'user' ? 'captain' : 'primary', delivery: value.delivery === 'failed' ? 'failed' : value.delivery === 'sending' ? 'sending' : value.delivery === 'sent' ? 'sent' : value.delivery === 'cancelled' ? 'cancelled' : undefined } as ConversationMessage;
    });
    const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
    const currentById = new Map(current.map(message => [message.id, message]));
    const hydrated = normalized.map(stored => {
      const live = currentById.get(stored.id);
      if (!live) return stored;
      currentById.delete(stored.id);
      // An optimistic message can change while AsyncStorage is loading, so its
      // mutable delivery fields win. The send timestamp is immutable local
      // knowledge, though: an early canonical read carries only second-level
      // server time and must not overwrite the exact persisted send instant.
      return { ...stored, ...live, sentAt: stored.sentAt ?? live.sentAt };
    });
    const merged = [...hydrated, ...current.filter(message => currentById.has(message.id))];
    messagesByTarget.set(target, merged);
    persist(target, merged);
    emit(target);
    return merged;
  } catch { return getConversationMessages(target); }
}

function emit(target: string) {
  listenersByTarget.get(target)?.forEach(listener => listener());
}

export function appendConversationMessage(target: string, message: ConversationMessage) {
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  // Multiple ChatCanvas instances (or a WebSocket plus polling) can observe
  // the same stable Herdr id. Keep distinct local ids, including repeated
  // identical user text, while making delivery idempotent by message id.
  if (current.some(existing => existing.id === message.id)) return;
  const next = [...current, message];
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function prependConversationMessages(target: string, messages: ConversationMessage[]) {
  if (!messages.length) return;
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  const existing = new Set(current.map(message => message.id));
  const next = [...messages.filter(message => !existing.has(message.id)), ...current];
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function resetConversationMessages(target: string, messages: ConversationMessage[] = EMPTY_MESSAGES) {
  messagesByTarget.set(target, messages); persist(target, messages); emit(target);
}

export function updateConversationMessage(target: string, id: string, text: string, sentAt = Date.now()) {
  updateConversationMessageState(target, id, { text, sentAt });
}

export function updateConversationMessageState(target: string, id: string, update: Partial<Pick<ConversationMessage, 'text' | 'sentAt' | 'delivery' | 'attachments' | 'toolResults' | 'sources' | 'thinkingSummary' | 'runId' | 'regenerateSafe' | 'progress' | 'audience' | 'canonicalId' | 'turnId' | 'sequenceIndex'>>) {
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  const next = current.map(message => message.id === id ? { ...message, ...update } : message);
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function getConversationMessages(target: string): ConversationMessage[] {
  return messagesByTarget.get(target) || EMPTY_MESSAGES;
}

export function useConversationMessages(target: string): ConversationMessage[] {
  const subscribe = useCallback((listener: () => void) => {
    const listeners = listenersByTarget.get(target) || new Set<() => void>();
    listeners.add(listener);
    listenersByTarget.set(target, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) listenersByTarget.delete(target);
    };
  }, [target]);
  const getSnapshot = useCallback(() => messagesByTarget.get(target) || EMPTY_MESSAGES, [target]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
