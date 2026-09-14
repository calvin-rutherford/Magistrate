import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSyncExternalStore } from 'react';
import {
  setCanonicalActivityPrincipal, settleCanonicalActivityPrincipal,
} from './CanonicalActivity';

export interface MagiAttachment {
  name: string;
  mediaType: string;
  size?: number;
  status?: 'uploading' | 'stored' | 'attached' | 'failed';
  uploadId?: string;
  url?: string;
}

export type MagiMessageProgress = 'queued' | 'working' | 'complete' | 'failed' | 'cancelled';

/** Render state for the one provider-native Magi conversation. */
export interface MagiMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  sentAt?: number;
  source: 'text' | 'voice';
  attachments?: MagiAttachment[];
  progress?: MagiMessageProgress;
  delivery?: 'sending' | 'sent' | 'failed' | 'cancelled';
  serverId?: string;
  conversationId?: string;
  clientMessageId?: string | null;
  replyToServerId?: string | null;
  serverStatus?: 'pending' | 'completed' | 'failed' | 'cancelled';
  revision?: number;
  turnId?: string;
  sequenceIndex?: number;
  /** Cache rows yield to the first authoritative Gateway observation. */
  fromCache?: boolean;
}

const CACHE_PREFIX = 'magistrate.magi.messages.v1.';
const PENDING_PREFIX = 'magistrate.magi.pending.v1.';
const RETIRED_PREFIXES = [
  'magistrate.chat.messages.',
  'magistrate.chat.messages.v2.',
  'magistrate.chat.canonical.v1.',
  'magistrate.chat.pending.v1.',
];
const ATTACHMENT_STATES = new Set(['uploading', 'stored', 'attached', 'failed']);
const ATTACHMENT_KEYS = new Set(['name', 'mediaType', 'size', 'status', 'uploadId', 'url']);
const CACHED_MESSAGE_KEYS = new Set([
  'id', 'role', 'text', 'sentAt', 'source', 'attachments', 'progress', 'delivery',
  'serverId', 'conversationId', 'clientMessageId', 'replyToServerId',
  'serverStatus', 'revision', 'turnId', 'sequenceIndex', 'fromCache',
]);
const PENDING_MESSAGE_KEYS = new Set([
  'id', 'role', 'text', 'sentAt', 'source', 'attachments', 'progress', 'delivery',
]);
const listeners = new Set<() => void>();
let messages: MagiMessage[] = [];
let principalId: string | null = null;
let pendingWrite: Promise<void> = Promise.resolve();
let principalTransition: Promise<void> = Promise.resolve();

const bounded = (value: unknown, maximum = 160): value is string =>
  typeof value === 'string' && value.length > 0 && Array.from(value).length <= maximum
  && !Array.from(value).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || (code >= 127 && code <= 159)
      || (code >= 0xd800 && code <= 0xdfff) || code === 0x2028 || code === 0x2029;
  });
const unsafeMessageText = (value: string): boolean => Array.from(value).some(character => {
  const code = character.codePointAt(0) ?? 0;
  return (code < 32 && code !== 9 && code !== 10 && code !== 13)
    || (code >= 127 && code <= 159) || (code >= 0xd800 && code <= 0xdfff);
});
const validPrincipal = (value: unknown): value is string => bounded(value, 128);
const storageKey = (prefix: string, principal: string) => `${prefix}${encodeURIComponent(principal)}`;
const isPending = (message: MagiMessage) => !message.serverId && message.role === 'user'
  && (message.delivery === 'sending' || message.delivery === 'failed');

function emit(): void { listeners.forEach(listener => listener()); }

function normalizeAttachments(raw: unknown, authoritative: boolean): MagiAttachment[] | null {
  if (!Array.isArray(raw) || raw.length > 10) return null;
  const attachments: MagiAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') return null;
    const value = item as Record<string, unknown>;
    const keys = Object.keys(value);
    if (keys.some(key => !ATTACHMENT_KEYS.has(key))
      || (authoritative && (keys.length !== ATTACHMENT_KEYS.size
        || ![...ATTACHMENT_KEYS].every(key => key in value)))
      || !bounded(value.name) || !bounded(value.mediaType, 128)
      || (authoritative
        && !/^[a-z0-9][a-z0-9.+-]{0,62}\/[a-z0-9][a-z0-9.+-]{0,62}$/.test(value.mediaType))
      || (value.size !== undefined && (typeof value.size !== 'number'
        || !Number.isSafeInteger(value.size) || value.size < 0 || value.size > 25 * 1024 * 1024))) return null;
    const status = ATTACHMENT_STATES.has(String(value.status))
      ? value.status as MagiAttachment['status'] : undefined;
    const uploadId = typeof value.uploadId === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(value.uploadId)
      ? value.uploadId : undefined;
    const url = uploadId && value.url === `/api/v1/uploads/${uploadId}` ? value.url as string : undefined;
    if ((authoritative && (status !== 'attached' || !uploadId || !url))
      || (!authoritative && !status)) return null;
    attachments.push({ name: value.name, mediaType: value.mediaType, size: value.size as number | undefined, status, uploadId, url });
  }
  if (new Set(attachments.map(attachment => attachment.uploadId).filter(Boolean)).size
    !== attachments.filter(attachment => attachment.uploadId).length) return null;
  return attachments;
}

function normalizeCachedMessage(raw: unknown, key: string): MagiMessage | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(name => !CACHED_MESSAGE_KEYS.has(name))
    || value.serverId !== key || typeof value.serverId !== 'string'
    || value.serverId.length > 128 || !/^mgm_[A-Za-z0-9_-]{4,124}$/.test(value.serverId)
    || !bounded(value.id, 128) || (value.role !== 'user' && value.role !== 'assistant')
    || typeof value.text !== 'string' || Array.from(value.text).length > 200_000
    || unsafeMessageText(value.text)
    || (value.source !== 'text' && value.source !== 'voice')
    || typeof value.conversationId !== 'string' || value.conversationId.length > 128
    || !/^mgc_[A-Za-z0-9_-]{4,124}$/.test(value.conversationId)
    || typeof value.turnId !== 'string' || value.turnId.length > 128
    || !/^mgt_[A-Za-z0-9_-]{4,124}$/.test(value.turnId)
    || !['pending', 'completed', 'failed', 'cancelled'].includes(String(value.serverStatus))
    || typeof value.sentAt !== 'number' || !Number.isSafeInteger(value.sentAt)
    || value.sentAt < 1_000_000_000_000
    || typeof value.revision !== 'number' || !Number.isSafeInteger(value.revision) || value.revision < 1
    || typeof value.sequenceIndex !== 'number' || !Number.isSafeInteger(value.sequenceIndex)
    || value.sequenceIndex < 0) return null;
  if (value.role === 'user') {
    if (value.id !== value.clientMessageId || typeof value.clientMessageId !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(value.clientMessageId)
      || value.replyToServerId !== null || value.serverStatus !== 'completed'
      || Array.from(value.text).length > 100_000 || !value.text.trim()) return null;
  } else if (value.id !== value.serverId || value.clientMessageId !== null
    || typeof value.replyToServerId !== 'string'
    || !/^mgm_[A-Za-z0-9_-]{4,124}$/.test(value.replyToServerId)
    || value.source !== 'text') return null;
  const attachments = normalizeAttachments(value.attachments, true);
  if (attachments === null) return null;
  if (!['working', 'complete', 'failed', 'cancelled'].includes(String(value.progress))) return null;
  const progress = value.progress as MagiMessageProgress;
  if (value.role === 'assistant' && (
    value.delivery !== undefined || attachments.length > 0
    || (value.serverStatus === 'completed' ? !value.text : value.text !== '')
    || (value.serverStatus === 'pending' ? progress !== 'working'
      : value.serverStatus === 'completed' ? progress !== 'complete'
      : progress !== value.serverStatus)
  )) return null;
  const delivery = value.role === 'user' && ['sent', 'failed', 'cancelled'].includes(String(value.delivery))
    ? value.delivery as MagiMessage['delivery'] : undefined;
  if (value.role === 'user' && (
    !delivery || (progress === 'failed' ? delivery !== 'failed'
      : progress === 'cancelled' ? delivery !== 'cancelled' : delivery !== 'sent')
  )) return null;
  return {
    id: value.id,
    role: value.role,
    text: value.text,
    sentAt: value.sentAt,
    source: value.source,
    attachments: attachments.length ? attachments : undefined,
    progress,
    delivery,
    serverId: value.serverId,
    conversationId: value.conversationId,
    clientMessageId: value.clientMessageId as string | null,
    replyToServerId: value.replyToServerId as string | null,
    serverStatus: value.serverStatus as MagiMessage['serverStatus'],
    revision: value.revision,
    turnId: value.turnId,
    sequenceIndex: value.sequenceIndex,
    fromCache: true,
  };
}

function normalizePendingMessage(raw: unknown, key: string): MagiMessage | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(name => !PENDING_MESSAGE_KEYS.has(name))
    || value.id !== key || key.length > 128
    || !/^(?:u-|voice-u-)[A-Za-z0-9][A-Za-z0-9_-]{5,119}$/.test(key)
    || value.role !== 'user' || typeof value.text !== 'string' || !value.text.trim()
    || Array.from(value.text).length > 100_000 || unsafeMessageText(value.text)
    || (value.source !== 'text' && value.source !== 'voice')
    || value.serverId !== undefined || (value.delivery !== 'sending' && value.delivery !== 'failed')) return null;
  const attachments = normalizeAttachments(value.attachments, false);
  if (attachments === null
    || (value.delivery === 'failed' ? value.progress !== 'failed'
      : value.progress !== 'queued' && value.progress !== 'working')) return null;
  return {
    id: key,
    role: 'user',
    text: value.text,
    sentAt: typeof value.sentAt === 'number' && Number.isSafeInteger(value.sentAt) && value.sentAt >= 0
      ? value.sentAt : undefined,
    source: value.source,
    attachments: attachments.length ? attachments : undefined,
    progress: value.delivery === 'failed' ? 'failed' : value.progress === 'queued' ? 'queued' : 'working',
    delivery: value.delivery,
  };
}

async function removeRetiredCaches(): Promise<void> {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const retired = keys.filter(key => RETIRED_PREFIXES.some(prefix => key.startsWith(prefix)));
    if (retired.length) await AsyncStorage.multiRemove(retired);
  } catch { /* A retired cache cannot become conversation authority. */ }
}

function persist(next: MagiMessage[]): void {
  const principal = principalId;
  if (!principal) return;
  const write = pendingWrite.catch(() => {}).then(async () => {
    if (principalId !== principal) return;
    const authoritative = Object.fromEntries(next.filter(row => row.serverId)
      .map(row => [row.serverId as string, { ...row, attachments: row.attachments || [] }]));
    const pending = Object.fromEntries(next.filter(isPending)
      .map(row => [row.id, { ...row, attachments: row.attachments || [] }]));
    await Promise.all([
      AsyncStorage.setItem(storageKey(CACHE_PREFIX, principal), JSON.stringify({
        schema_version: 'magi-conversation-cache.v1', principal_id: principal, messages: authoritative,
      })),
      AsyncStorage.setItem(storageKey(PENDING_PREFIX, principal), JSON.stringify({
        schema_version: 'magi-conversation-pending.v1', principal_id: principal, messages: pending,
      })),
    ]);
  });
  pendingWrite = write;
  void write.catch(() => {});
}

/** Clear memory synchronously before an auth principal changes. */
export async function setMagiConversationPrincipal(principal: string | null): Promise<void> {
  if (principal !== null && !validPrincipal(principal)) throw new Error('Invalid Magi conversation principal.');
  if (principalId === principal && principal !== null) return;
  const previous = principalId;
  const priorWrite = pendingWrite;
  principalId = principal;
  messages = [];
  setCanonicalActivityPrincipal(principal);
  emit();
  const transition = principalTransition.catch(() => {}).then(async () => {
    await Promise.all([priorWrite.catch(() => {}), settleCanonicalActivityPrincipal().catch(() => {})]);
    if (principalId !== principal) return;
    try {
      const keys = await AsyncStorage.getAllKeys();
      const previousKeys = previous ? [storageKey(CACHE_PREFIX, previous), storageKey(PENDING_PREFIX, previous)] : [];
      const otherPrincipalKeys = keys.filter(key => (key.startsWith(CACHE_PREFIX) || key.startsWith(PENDING_PREFIX))
        && (!principal || (key !== storageKey(CACHE_PREFIX, principal) && key !== storageKey(PENDING_PREFIX, principal))));
      const retired = keys.filter(key => RETIRED_PREFIXES.some(prefix => key.startsWith(prefix)));
      const remove = [...new Set([...previousKeys, ...otherPrincipalKeys, ...retired])];
      if (remove.length && principalId === principal) await AsyncStorage.multiRemove(remove);
    } catch { /* Cache eviction cannot block logout. */ }
  });
  principalTransition = transition;
  await transition;
}

export function getMagiConversationPrincipal(): string | null { return principalId; }

export async function loadCachedMagiConversation(): Promise<{ authoritative: MagiMessage[]; pending: MagiMessage[] }> {
  const principal = principalId;
  if (!principal) return { authoritative: [], pending: [] };
  try {
    await pendingWrite.catch(() => {});
    const [cachedRaw, pendingRaw] = await Promise.all([
      AsyncStorage.getItem(storageKey(CACHE_PREFIX, principal)),
      AsyncStorage.getItem(storageKey(PENDING_PREFIX, principal)),
    ]);
    void removeRetiredCaches();
    if (principalId !== principal) return { authoritative: [], pending: [] };
    const cachedPayload = cachedRaw ? JSON.parse(cachedRaw) as Record<string, unknown> : null;
    const pendingPayload = pendingRaw ? JSON.parse(pendingRaw) as Record<string, unknown> : null;
    if (cachedRaw && (!cachedPayload || typeof cachedPayload !== 'object' || Array.isArray(cachedPayload))) {
      throw new Error('Invalid canonical Magi cache payload.');
    }
    if (pendingRaw && (!pendingPayload || typeof pendingPayload !== 'object' || Array.isArray(pendingPayload))) {
      throw new Error('Invalid pending Magi cache payload.');
    }
    if (cachedPayload && (cachedPayload.schema_version !== 'magi-conversation-cache.v1'
      || cachedPayload.principal_id !== principal || !cachedPayload.messages
      || typeof cachedPayload.messages !== 'object' || Array.isArray(cachedPayload.messages)
      || Object.keys(cachedPayload).some(key => !['schema_version', 'principal_id', 'messages'].includes(key)))) {
      throw new Error('Invalid canonical Magi cache.');
    }
    if (pendingPayload && (pendingPayload.schema_version !== 'magi-conversation-pending.v1'
      || pendingPayload.principal_id !== principal || !pendingPayload.messages
      || typeof pendingPayload.messages !== 'object' || Array.isArray(pendingPayload.messages)
      || Object.keys(pendingPayload).some(key => !['schema_version', 'principal_id', 'messages'].includes(key)))) {
      throw new Error('Invalid pending Magi cache.');
    }
    const cachedMap = cachedPayload
      ? cachedPayload.messages as Record<string, unknown> : {};
    const pendingMap = pendingPayload
      ? pendingPayload.messages as Record<string, unknown> : {};
    if (Object.keys(cachedMap).length > 1_000 || Object.keys(pendingMap).length > 10) {
      throw new Error('Magi cache exceeds its bounded capacity.');
    }
    const authoritative = Object.entries(cachedMap).flatMap(([key, value]) => {
      const normalized = normalizeCachedMessage(value, key);
      return normalized ? [normalized] : [];
    }).sort((left, right) => (left.sequenceIndex as number) - (right.sequenceIndex as number));
    if (authoritative.length !== Object.keys(cachedMap).length) throw new Error('Invalid canonical Magi cache row.');
    const identityOwners = new Map<string, string>();
    const sequences = new Set<number>();
    const turnRoles = new Set<string>();
    const conversations = new Set<string>();
    for (const row of authoritative) {
      const turnRole = `${row.turnId}:${row.role}`;
      const owner = row.serverId as string;
      if (sequences.has(row.sequenceIndex as number) || turnRoles.has(turnRole)
        || [row.id, owner].some(identity => identityOwners.has(identity)
          && identityOwners.get(identity) !== owner)) {
        throw new Error('Conflicting canonical Magi cache identity.');
      }
      identityOwners.set(row.id, owner);
      identityOwners.set(owner, owner);
      sequences.add(row.sequenceIndex as number);
      turnRoles.add(turnRole);
      conversations.add(row.conversationId as string);
    }
    if (conversations.size > 1) throw new Error('Cross-conversation Magi cache.');
    for (const assistant of authoritative.filter(row => row.role === 'assistant')) {
      const user = authoritative.find(row => row.role === 'user' && row.turnId === assistant.turnId);
      if (user && assistant.replyToServerId !== user.serverId) {
        throw new Error('Cross-attributed canonical Magi cache.');
      }
    }
    const pending = Object.entries(pendingMap).flatMap(([key, value]) => {
      const normalized = normalizePendingMessage(value, key);
      return normalized ? [normalized] : [];
    });
    if (pending.length !== Object.keys(pendingMap).length
      || new Set(pending.map(row => row.id)).size !== pending.length
      || pending.some(row => identityOwners.has(row.id))) throw new Error('Invalid pending Magi cache row.');
    return principalId === principal ? { authoritative, pending } : { authoritative: [], pending: [] };
  } catch {
    await Promise.all([
      AsyncStorage.removeItem(storageKey(CACHE_PREFIX, principal)).catch(() => {}),
      AsyncStorage.removeItem(storageKey(PENDING_PREFIX, principal)).catch(() => {}),
    ]);
    return { authoritative: [], pending: [] };
  }
}

export function appendMagiMessage(message: MagiMessage): void {
  if (messages.some(existing => existing.id === message.id)) return;
  messages = [...messages, message];
  persist(messages);
  emit();
}

export function resetMagiMessages(next: MagiMessage[] = []): void {
  messages = next;
  persist(messages);
  emit();
}

export function updateMagiMessage(id: string, update: Partial<MagiMessage>): void {
  messages = messages.map(message => message.id === id ? { ...message, ...update } : message);
  persist(messages);
  emit();
}

export function getMagiMessages(): MagiMessage[] { return messages; }

export function useMagiMessages(): MagiMessage[] {
  return useSyncExternalStore(
    listener => { listeners.add(listener); return () => listeners.delete(listener); },
    getMagiMessages,
    getMagiMessages,
  );
}
