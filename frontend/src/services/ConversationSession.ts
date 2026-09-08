import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useSyncExternalStore } from 'react';
import { MAGI_MAX_FALLBACK_TEXT_CHARS, MagiResponseV1, normalizeMagiResponse } from './MagiResponse';
import {
  setCanonicalActivityPrincipal, settleCanonicalActivityPrincipal,
} from './CanonicalActivity';

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
  /** Authenticated gateway reference to the bounded upload store. */
  url?: string;
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
  // A local send starts with Date.now(); a canonical captain row replaces it
  // with gateway epoch milliseconds. Terminal-derived worker rows may omit time.
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
  /** Stable causal identities; both remain distinct from the conversation turn. */
  objectiveId?: string;
  runId?: string;
  lifecycleState?: 'active' | 'awaiting-user' | 'completed' | 'failed' | 'cancelled';
  lifecycleRevision?: number;
  decisionKey?: string;
  assistantKind?: 'response' | 'progress' | 'decision' | 'outcome';
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
  /** Monotonic gateway revision used to reject out-of-order poll/socket delivery. */
  canonicalRevision?: number;
  turnId?: string;
  sequenceIndex?: number;
  /** Validated semantic document; absent means render the canonical text fallback. */
  structuredContent?: MagiResponseV1;
  contentSource?: 'structured' | 'terminal-fallback';
  /** Monotonic producer revision, separate from the canonical row revision. */
  structuredRevision?: number;
  /** Process-local marker: restored cache rows yield to the first Gateway row. */
  fromCanonicalCache?: boolean;
}

// The active thread is kept in memory for reactive rendering and mirrored as
// normalized messages in AsyncStorage. Terminal scrollback is never persisted;
// Chat and Voice Mode share the 'captain' target in-session.
const messagesByTarget = new Map<string, ConversationMessage[]>();
const listenersByTarget = new Map<string, Set<() => void>>();
const EMPTY_MESSAGES: ConversationMessage[] = [];
let activePrincipalId: string | null = null;
const principalTargetKey = (target: string, principal = activePrincipalId) =>
  `${principal || 'unauthenticated'}\0${target}`;
const activeMessages = (target: string) => messagesByTarget.get(principalTargetKey(target)) || EMPTY_MESSAGES;
const setActiveMessages = (target: string, messages: ConversationMessage[]) => {
  messagesByTarget.set(principalTargetKey(target), messages);
};
// Captain persistence is a cache, not a second transcript. Canonical rows are
// keyed by gateway id and unacknowledged local submissions live in a separate
// map keyed by client_message_id. Terminal-era v1/v2 captain arrays are deleted
// rather than merged back into the gateway record. Worker panes remain on the
// transitional v2 array until they receive durable upstream identity.
const CAPTAIN_TARGET = 'captain';
const WORKER_STORAGE_PREFIX = 'magistrate.chat.messages.v2.';
const CANONICAL_CACHE_PREFIX = 'magistrate.chat.canonical.v1.';
const PENDING_CACHE_PREFIX = 'magistrate.chat.pending.v1.';
const LEGACY_STORAGE_PREFIXES = ['magistrate.chat.messages.', WORKER_STORAGE_PREFIX];
const writesByTarget = new Map<string, Promise<void>>();
let principalTransition: Promise<void> = Promise.resolve();
// `encodeURIComponent` leaves dots unescaped, so use a delimiter it always
// escapes inside either component; prefix matching must not confuse `a` with
// principal `a.b` during stale-account eviction.
const principalStoragePrefix = (prefix: string, principal: string) =>
  `${prefix}${encodeURIComponent(principal)}|`;
const storageKey = (prefix: string, principal: string, target: string) =>
  `${principalStoragePrefix(prefix, principal)}${encodeURIComponent(target)}`;
const legacyStorageKey = (prefix: string, target: string) => prefix + encodeURIComponent(target);
const isPendingLocalMessage = (message: ConversationMessage) =>
  !message.canonicalId && message.role === 'user'
  && (message.delivery === 'sending' || message.delivery === 'failed');
const discardLegacyStorage = (target: string, includeV2 = false) => {
  const prefixes = includeV2 ? [...LEGACY_STORAGE_PREFIXES, CANONICAL_CACHE_PREFIX, PENDING_CACHE_PREFIX] : LEGACY_STORAGE_PREFIXES.slice(0, 1);
  void Promise.all(prefixes.map(prefix => AsyncStorage.removeItem(legacyStorageKey(prefix, target)))).catch(() => {});
};
const boundedIdentity = (value: unknown, maximum = 128): value is string =>
  typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
  && !Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || (code >= 127 && code <= 159)
      || (code >= 0xd800 && code <= 0xdfff) || code === 0x2028 || code === 0x2029;
  });
const validPrincipalId = (value: unknown): value is string => boundedIdentity(value);

/**
 * Set the server-validated principal before protected routes mount.
 *
 * Memory is cleared synchronously. Persisted state is principal-qualified, and
 * logout/expiry/account change waits for old writes before removing every old
 * principal key. Unqualified v1 caches are deleted, never migrated.
 */
export async function setConversationPrincipal(principal: string | null): Promise<void> {
  if (principal !== null && !validPrincipalId(principal)) throw new Error('Invalid conversation principal.');
  if (activePrincipalId === principal && principal !== null) return;
  const previous = activePrincipalId;
  const pendingWrites = [...writesByTarget.values()];
  activePrincipalId = principal;
  setCanonicalActivityPrincipal(principal);
  messagesByTarget.clear();
  listenersByTarget.forEach(listeners => listeners.forEach(listener => listener()));
  const transition = principalTransition.catch(() => {}).then(async () => {
    await Promise.all([
      ...pendingWrites.map(write => write.catch(() => {})),
      settleCanonicalActivityPrincipal().catch(() => {}),
    ]);
    if (activePrincipalId !== principal) return;
    try {
      const keys = await AsyncStorage.getAllKeys();
      if (activePrincipalId !== principal) return;
      const scopedCachePrefixes = [CANONICAL_CACHE_PREFIX, PENDING_CACHE_PREFIX];
      const activeCachePrefixes = principal ? scopedCachePrefixes.map(
        prefix => principalStoragePrefix(prefix, principal),
      ) : [];
      const previousCachePrefixes = previous ? scopedCachePrefixes.map(
        prefix => principalStoragePrefix(prefix, previous),
      ) : [];
      const unqualifiedCaptainKeys = [
        ...LEGACY_STORAGE_PREFIXES,
        CANONICAL_CACHE_PREFIX,
        PENDING_CACHE_PREFIX,
      ].map(prefix => legacyStorageKey(prefix, CAPTAIN_TARGET));
      const remove = keys.filter(key => {
        const scoped = scopedCachePrefixes.some(prefix => key.startsWith(prefix));
        const belongsToActivePrincipal = activeCachePrefixes.some(prefix => key.startsWith(prefix));
        return unqualifiedCaptainKeys.includes(key)
          || previousCachePrefixes.some(prefix => key.startsWith(prefix))
          || (scoped && !belongsToActivePrincipal);
      });
      if (remove.length && activePrincipalId === principal) await AsyncStorage.multiRemove(remove);
    } catch { /* cache eviction cannot make local logout fail */ }
  });
  principalTransition = transition;
  await transition;
}

export function getConversationPrincipal(): string | null {
  return activePrincipalId;
}

const normalizeCachedAttachments = (raw: unknown, canonical: boolean): ConversationAttachment[] | undefined => {
  if (!Array.isArray(raw)) return undefined;
  const attachments = raw.slice(0, 10).flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const value = item as Record<string, unknown>;
    if (typeof value.name !== 'string' || !value.name || value.name.length > 160
      || typeof value.mediaType !== 'string' || !value.mediaType || value.mediaType.length > 128
      || (value.size !== undefined && (typeof value.size !== 'number' || !Number.isSafeInteger(value.size) || value.size < 0 || value.size > 25 * 1024 * 1024))) return [];
    const status = ATTACHMENT_STATES.has(String(value.status)) ? value.status as ConversationAttachment['status'] : undefined;
    const uploadId = typeof value.uploadId === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(value.uploadId) ? value.uploadId : undefined;
    const url = uploadId && value.url === `/api/v1/uploads/${uploadId}` ? value.url as string : undefined;
    if (canonical && (status !== 'attached' || !uploadId || !url)) return [];
    return [{ name: value.name, mediaType: value.mediaType, size: value.size as number | undefined, status, uploadId, url }];
  });
  return attachments.length ? attachments : undefined;
};
const normalizeCachedCanonicalMessage = (raw: unknown, cacheKey: string): ConversationMessage | null => {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const assistantConversation = value.role === 'assistant' && value.kind === 'conversation';
  const validStructuredRevision = typeof value.structuredRevision === 'number'
    && Number.isSafeInteger(value.structuredRevision) && value.structuredRevision >= 1;
  const structuredContent = assistantConversation && value.contentSource === 'structured' && validStructuredRevision
    ? normalizeMagiResponse(value.structuredContent) : null;
  // Invalid/unknown structured JSON still retains a bounded plain-text
  // fallback. Other cache rows keep the smaller historical bound.
  const maxText = assistantConversation
    ? MAGI_MAX_FALLBACK_TEXT_CHARS : value.role === 'user' ? 100_000 : 20_000;
  if (typeof value.canonicalId !== 'string' || value.canonicalId !== cacheKey || !boundedIdentity(value.canonicalId)
    || !boundedIdentity(value.id, 160)
    || (value.role !== 'user' && value.role !== 'assistant')
    || (value.role === 'assistant' && value.id !== value.canonicalId)
    || typeof value.text !== 'string' || !value.text.trim() || Array.from(value.text).length > maxText
    || (value.kind !== 'conversation' && value.kind !== 'tool')
    || (value.kind === 'tool' && value.role !== 'assistant')
    || (value.source !== 'text' && value.source !== 'voice')
    || typeof value.sequenceIndex !== 'number' || !Number.isSafeInteger(value.sequenceIndex) || value.sequenceIndex < 0
    || typeof value.canonicalRevision !== 'number' || !Number.isSafeInteger(value.canonicalRevision) || value.canonicalRevision < 1
    || !boundedIdentity(value.turnId)
    || typeof value.sentAt !== 'number' || !Number.isSafeInteger(value.sentAt) || value.sentAt < 1_000_000_000_000) return null;
  const progress = ['queued', 'working', 'streaming', 'complete', 'failed', 'cancelled'].includes(String(value.progress)) ? value.progress as ConversationProgress : undefined;
  const delivery = value.role === 'user' && ['sent', 'failed', 'cancelled'].includes(String(value.delivery)) ? value.delivery as ConversationMessage['delivery'] : undefined;
  const structuredRevision = structuredContent ? value.structuredRevision as number : undefined;
  const lifecycleState = ['active', 'awaiting-user', 'completed', 'failed', 'cancelled'].includes(String(value.lifecycleState))
    ? value.lifecycleState as ConversationMessage['lifecycleState'] : undefined;
  const lifecycleRevision = typeof value.lifecycleRevision === 'number' && Number.isSafeInteger(value.lifecycleRevision) && value.lifecycleRevision >= 1
    ? value.lifecycleRevision : undefined;
  const assistantKind = value.role === 'assistant' && ['response', 'progress', 'decision', 'outcome'].includes(String(value.assistantKind))
    ? value.assistantKind as ConversationMessage['assistantKind'] : undefined;
  const lifecycleProvided = value.lifecycleState !== undefined || value.lifecycleRevision !== undefined
    || value.decisionKey !== undefined || value.objectiveId !== undefined || value.runId !== undefined;
  const decisionKey = boundedIdentity(value.decisionKey) ? value.decisionKey : undefined;
  if (lifecycleProvided && (
    !lifecycleState || !lifecycleRevision || !boundedIdentity(value.objectiveId)
    || !boundedIdentity(value.runId)
    || (lifecycleState === 'awaiting-user' ? !decisionKey : value.decisionKey !== undefined)
  )) return null;
  if ((value.assistantKind !== undefined && !assistantKind)
    || (value.role !== 'assistant' && value.assistantKind !== undefined)) return null;
  return {
    id: value.id,
    role: value.role,
    text: value.text,
    sentAt: value.sentAt,
    source: value.source === 'voice' ? 'voice' : 'text',
    kind: value.kind,
    attachments: normalizeCachedAttachments(value.attachments, true),
    progress,
    audience: value.role === 'user' ? 'captain' : 'primary',
    delivery,
    canonicalId: value.canonicalId,
    canonicalRevision: value.canonicalRevision,
    turnId: value.turnId,
    sequenceIndex: value.sequenceIndex,
    objectiveId: boundedIdentity(value.objectiveId) ? value.objectiveId : undefined,
    runId: boundedIdentity(value.runId) ? value.runId : undefined,
    lifecycleState,
    lifecycleRevision,
    decisionKey,
    assistantKind,
    structuredContent: structuredContent || undefined,
    contentSource: structuredContent ? 'structured' : assistantConversation ? 'terminal-fallback' : undefined,
    structuredRevision,
    fromCanonicalCache: true,
  };
};
const normalizeCachedPendingMessage = (raw: unknown, cacheKey: string): ConversationMessage | null => {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.id !== 'string' || value.id !== cacheKey || !/^(?:u-|voice-u-)[A-Za-z0-9_-]*$/.test(value.id)
    || value.role !== 'user' || typeof value.text !== 'string' || !value.text.trim()
    || Array.from(value.text).length > 100_000
    || (value.source !== 'text' && value.source !== 'voice')
    || value.canonicalId !== undefined || (value.delivery !== 'sending' && value.delivery !== 'failed')) return null;
  return {
    id: value.id,
    role: 'user',
    text: value.text,
    sentAt: typeof value.sentAt === 'number' && Number.isSafeInteger(value.sentAt) && value.sentAt >= 0 ? value.sentAt : undefined,
    source: value.source === 'voice' ? 'voice' : 'text',
    kind: 'conversation',
    attachments: normalizeCachedAttachments(value.attachments, false),
    progress: value.delivery === 'failed' ? 'failed' : value.progress === 'queued' ? 'queued' : 'working',
    audience: 'captain',
    delivery: value.delivery,
  };
};
const persist = (target: string, messages: ConversationMessage[]) => {
  const principal = activePrincipalId;
  if (target === CAPTAIN_TARGET && !principal) return;
  const scope = target === CAPTAIN_TARGET ? principalTargetKey(target, principal) : target;
  const previous = writesByTarget.get(scope) || Promise.resolve();
  const write = previous.catch(() => {}).then(async () => {
    if (target !== CAPTAIN_TARGET) {
      await AsyncStorage.setItem(legacyStorageKey(WORKER_STORAGE_PREFIX, target), JSON.stringify(messages));
      return;
    }
    const captainPrincipal = principal as string;
    const canonical = Object.fromEntries(messages
      .filter(message => typeof message.canonicalId === 'string' && message.canonicalId)
      .map(message => [message.canonicalId as string, message]));
    const pending = Object.fromEntries(messages
      .filter(isPendingLocalMessage)
      .map(message => [message.id, message]));
    await Promise.all([
      AsyncStorage.setItem(storageKey(CANONICAL_CACHE_PREFIX, captainPrincipal, target), JSON.stringify({ schema_version: 'conversation-cache.v1', principal_id: captainPrincipal, messages: canonical })),
      AsyncStorage.setItem(storageKey(PENDING_CACHE_PREFIX, captainPrincipal, target), JSON.stringify({ schema_version: 'conversation-pending.v1', principal_id: captainPrincipal, messages: pending })),
    ]);
  });
  writesByTarget.set(scope, write);
  void write.finally(() => { if (writesByTarget.get(scope) === write) writesByTarget.delete(scope); }).catch(() => {});
};

/**
 * The last locally observed canonical snapshot is a startup/reconnect cache,
 * never a second authority. It is restored before the network read so a
 * transient outage cannot turn a known conversation into an empty screen; a
 * successful Gateway list read still prunes/replaces it authoritatively.
 *
 * Both maps are parsed fail-closed. In particular, only typed canonical rows
 * with durable identity/revision/sequence metadata can enter the trusted
 * snapshot, and terminal-era arrays are still deleted rather than migrated.
 */
export async function loadCachedCaptainConversation(target: string): Promise<{ canonical: ConversationMessage[]; pending: ConversationMessage[] }> {
  const principal = activePrincipalId;
  if (target !== CAPTAIN_TARGET || !principal) return { canonical: [], pending: [] };
  try {
    await writesByTarget.get(principalTargetKey(target, principal))?.catch(() => {});
    const [canonicalRaw, pendingRaw] = await Promise.all([
      AsyncStorage.getItem(storageKey(CANONICAL_CACHE_PREFIX, principal, target)),
      AsyncStorage.getItem(storageKey(PENDING_CACHE_PREFIX, principal, target)),
    ]);
    if (activePrincipalId !== principal) return { canonical: [], pending: [] };
    discardLegacyStorage(target, true);
    const canonicalPayload = canonicalRaw ? JSON.parse(canonicalRaw) as Record<string, unknown> : null;
    const pendingPayload = pendingRaw ? JSON.parse(pendingRaw) as Record<string, unknown> : null;
    const canonicalMap = canonicalPayload?.schema_version === 'conversation-cache.v1'
      && canonicalPayload.principal_id === principal
      && canonicalPayload.messages && typeof canonicalPayload.messages === 'object'
      ? canonicalPayload.messages as Record<string, unknown> : {};
    const pendingMap = pendingPayload?.schema_version === 'conversation-pending.v1'
      && pendingPayload.principal_id === principal
      && pendingPayload.messages && typeof pendingPayload.messages === 'object'
      ? pendingPayload.messages as Record<string, unknown> : {};
    const canonical = Object.entries(canonicalMap)
      .flatMap(([key, value]) => { const normalized = normalizeCachedCanonicalMessage(value, key); return normalized ? [normalized] : []; })
      .sort((left, right) => (left.sequenceIndex as number) - (right.sequenceIndex as number));
    const pending = Object.entries(pendingMap)
      .flatMap(([key, value]) => { const normalized = normalizeCachedPendingMessage(value, key); return normalized ? [normalized] : []; });
    return activePrincipalId === principal ? { canonical, pending } : { canonical: [], pending: [] };
  } catch { return { canonical: [], pending: [] }; }
}

export async function hydrateConversationMessages(target: string): Promise<ConversationMessage[]> {
  if (target === CAPTAIN_TARGET) return getConversationMessages(target);
  try {
    const raw = await AsyncStorage.getItem(legacyStorageKey(WORKER_STORAGE_PREFIX, target));
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
        const url = typeof candidate.url === 'string' && /^\/api\/v1\/uploads\/[A-Za-z0-9_-]{16,64}$/.test(candidate.url) ? candidate.url : undefined;
        return { name: candidate.name as string, mediaType: candidate.mediaType as string, size: candidate.size as number | undefined, status, uploadId, url };
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
      return { ...value, sentAt, source: value.source === 'voice' ? 'voice' : 'text', kind: value.kind === 'tool' ? 'tool' : 'conversation', attachments, toolResults, sources, thinkingSummary, runId: typeof value.runId === 'string' && value.runId.length <= 160 ? value.runId : undefined, regenerateSafe: value.regenerateSafe === true, progress, audience: role === 'user' ? 'captain' : 'primary', delivery: value.delivery === 'failed' ? 'failed' : value.delivery === 'sending' ? 'sending' : value.delivery === 'sent' ? 'sent' : value.delivery === 'cancelled' ? 'cancelled' : undefined, structuredContent: undefined, contentSource: undefined, structuredRevision: undefined } as ConversationMessage;
    });
    const current = activeMessages(target);
    const currentById = new Map(current.map(message => [message.id, message]));
    const hydrated = normalized.map(stored => {
      const live = currentById.get(stored.id);
      if (!live) return stored;
      currentById.delete(stored.id);
      // An optimistic worker message can change while AsyncStorage is loading.
      // Its in-memory fields are newer; retain a stored timestamp only when the
      // live row does not carry one.
      return { ...stored, ...live, sentAt: live.sentAt ?? stored.sentAt };
    });
    const merged = [...hydrated, ...current.filter(message => currentById.has(message.id))];
    setActiveMessages(target, merged);
    persist(target, merged);
    emit(target);
    return merged;
  } catch { return getConversationMessages(target); }
}

function emit(target: string) {
  listenersByTarget.get(target)?.forEach(listener => listener());
}

export function appendConversationMessage(target: string, message: ConversationMessage) {
  const current = activeMessages(target);
  // Multiple ChatCanvas instances (or a WebSocket plus polling) can observe
  // the same stable Herdr id. Keep distinct local ids, including repeated
  // identical user text, while making delivery idempotent by message id.
  if (current.some(existing => existing.id === message.id)) return;
  const next = [...current, message];
  setActiveMessages(target, next); persist(target, next); emit(target);
}

export function prependConversationMessages(target: string, messages: ConversationMessage[]) {
  if (!messages.length) return;
  const current = activeMessages(target);
  const existing = new Set(current.map(message => message.id));
  const next = [...messages.filter(message => !existing.has(message.id)), ...current];
  setActiveMessages(target, next); persist(target, next); emit(target);
}

export function resetConversationMessages(target: string, messages: ConversationMessage[] = EMPTY_MESSAGES) {
  setActiveMessages(target, messages); persist(target, messages); emit(target);
}

export function updateConversationMessage(target: string, id: string, text: string, sentAt = Date.now()) {
  updateConversationMessageState(target, id, { text, sentAt });
}

export function updateConversationMessageState(target: string, id: string, update: Partial<Pick<ConversationMessage, 'text' | 'sentAt' | 'delivery' | 'attachments' | 'toolResults' | 'sources' | 'thinkingSummary' | 'objectiveId' | 'runId' | 'lifecycleState' | 'lifecycleRevision' | 'decisionKey' | 'assistantKind' | 'regenerateSafe' | 'progress' | 'audience' | 'canonicalId' | 'canonicalRevision' | 'turnId' | 'sequenceIndex' | 'structuredContent' | 'contentSource' | 'structuredRevision'>>) {
  const current = activeMessages(target);
  const next = current.map(message => message.id === id ? { ...message, ...update } : message);
  setActiveMessages(target, next); persist(target, next); emit(target);
}

export function getConversationMessages(target: string): ConversationMessage[] {
  return activeMessages(target);
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
  const getSnapshot = useCallback(() => activeMessages(target), [target]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
