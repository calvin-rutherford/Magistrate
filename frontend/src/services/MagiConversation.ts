import type { MagiAttachment, MagiMessage } from './MagiConversationSession';

export type MagiMessageStatus = 'pending' | 'completed' | 'failed' | 'cancelled';

/** Exact provider-native message shape returned by the Gateway. */
export interface MagiMessageRecord {
  id: string;
  conversation_id: string;
  turn_id: string;
  client_message_id: string | null;
  reply_to_message_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  status: MagiMessageStatus;
  source: 'text' | 'voice' | 'magi-native';
  sequence_index: number;
  revision: number;
  attachments: MagiAttachment[];
  created_at: number;
  updated_at: number;
}

const MESSAGE_KEYS = new Set([
  'id', 'conversation_id', 'turn_id', 'client_message_id', 'reply_to_message_id',
  'role', 'content', 'status', 'source', 'sequence_index', 'revision',
  'attachments', 'created_at', 'updated_at',
]);
const ATTACHMENT_KEYS = new Set(['id', 'upload_id', 'name', 'media_type', 'size', 'url']);
const boundedId = (value: unknown, pattern: RegExp): value is string =>
  typeof value === 'string' && value.length <= 128 && pattern.test(value);
const hasUnsafeControl = (value: string): boolean => Array.from(value).some(character => {
  const code = character.codePointAt(0) ?? 0;
  return (code < 32 && code !== 9 && code !== 10 && code !== 13)
    || (code >= 127 && code <= 159) || (code >= 0xd800 && code <= 0xdfff);
});

function normalizeAttachments(raw: unknown): MagiAttachment[] | null {
  if (!Array.isArray(raw) || raw.length > 10) return null;
  const result: MagiAttachment[] = [];
  const seenUploads = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== 'object') return null;
    const value = item as Record<string, unknown>;
    if (Object.keys(value).length !== ATTACHMENT_KEYS.size
      || Object.keys(value).some(key => !ATTACHMENT_KEYS.has(key))) return null;
    const uploadId = typeof value.upload_id === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(value.upload_id)
      ? value.upload_id : null;
    if (!uploadId || seenUploads.has(uploadId)
      || value.id !== uploadId || value.url !== `/api/v1/uploads/${uploadId}`
      || typeof value.name !== 'string' || !value.name || Array.from(value.name).length > 160
      || hasUnsafeControl(value.name)
      || typeof value.media_type !== 'string'
      || !/^[a-z0-9][a-z0-9.+-]{0,62}\/[a-z0-9][a-z0-9.+-]{0,62}$/.test(value.media_type)
      || typeof value.size !== 'number' || !Number.isSafeInteger(value.size)
      || value.size < 0 || value.size > 25 * 1024 * 1024) return null;
    seenUploads.add(uploadId);
    result.push({
      name: value.name,
      mediaType: value.media_type,
      size: value.size,
      status: 'attached',
      uploadId,
      url: `/api/v1/uploads/${uploadId}`,
    });
  }
  return result;
}

/** Fail closed: Native Chat never infers role, source, or identity. */
export function normalizeMagiMessageRecord(raw: unknown): MagiMessageRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== MESSAGE_KEYS.size
    || Object.keys(value).some(key => !MESSAGE_KEYS.has(key))
    || !boundedId(value.id, /^mgm_[A-Za-z0-9_-]{4,124}$/)
    || !boundedId(value.conversation_id, /^mgc_[A-Za-z0-9_-]{4,124}$/)
    || !boundedId(value.turn_id, /^mgt_[A-Za-z0-9_-]{4,124}$/)
    || (value.role !== 'user' && value.role !== 'assistant')
    || !['pending', 'completed', 'failed', 'cancelled'].includes(String(value.status))
    || typeof value.content !== 'string' || Array.from(value.content).length > 200_000
    || hasUnsafeControl(value.content)
    || typeof value.sequence_index !== 'number' || !Number.isSafeInteger(value.sequence_index) || value.sequence_index < 0
    || typeof value.revision !== 'number' || !Number.isSafeInteger(value.revision) || value.revision < 1
    || typeof value.created_at !== 'number' || !Number.isSafeInteger(value.created_at) || value.created_at < 1_000_000_000_000
    || typeof value.updated_at !== 'number' || !Number.isSafeInteger(value.updated_at) || value.updated_at < value.created_at) return null;

  if (value.role === 'user') {
    if (value.status !== 'completed' || (value.source !== 'text' && value.source !== 'voice')
      || !boundedId(value.client_message_id, /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/)
      || value.reply_to_message_id !== null || !value.content.trim()) return null;
  } else if (value.source !== 'magi-native' || value.client_message_id !== null
    || !boundedId(value.reply_to_message_id, /^mgm_[A-Za-z0-9_-]{4,124}$/)
    || (value.status === 'completed' ? !value.content : value.content !== '')) return null;

  const attachments = normalizeAttachments(value.attachments);
  if (attachments === null || (value.role === 'assistant' && attachments.length)) return null;
  return {
    id: value.id,
    conversation_id: value.conversation_id,
    turn_id: value.turn_id,
    client_message_id: value.client_message_id as string | null,
    reply_to_message_id: value.reply_to_message_id as string | null,
    role: value.role,
    content: value.content,
    status: value.status as MagiMessageStatus,
    source: value.source as MagiMessageRecord['source'],
    sequence_index: value.sequence_index,
    revision: value.revision,
    attachments,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

export function normalizeMagiMessageRecords(raw: unknown): MagiMessageRecord[] {
  if (!Array.isArray(raw) || raw.length > 1000) return [];
  const normalized = raw.map(normalizeMagiMessageRecord);
  if (normalized.some(message => message === null)) return [];
  const records = normalized as MagiMessageRecord[];
  const serverIds = new Set<string>();
  const renderIds = new Set<string>();
  const sequences = new Set<number>();
  const turnRoles = new Set<string>();
  const conversations = new Set<string>();
  for (const record of records) {
    const rendered = renderId(record);
    const turnRole = `${record.turn_id}:${record.role}`;
    if (serverIds.has(record.id) || renderIds.has(rendered)
      || sequences.has(record.sequence_index) || turnRoles.has(turnRole)) return [];
    serverIds.add(record.id);
    renderIds.add(rendered);
    sequences.add(record.sequence_index);
    turnRoles.add(turnRole);
    conversations.add(record.conversation_id);
  }
  if (conversations.size > 1) return [];
  for (const assistant of records.filter(record => record.role === 'assistant')) {
    const user = records.find(record => record.role === 'user' && record.turn_id === assistant.turn_id);
    if (user && assistant.reply_to_message_id !== user.id) return [];
  }
  return records.sort((left, right) => left.sequence_index - right.sequence_index);
}

const renderId = (record: MagiMessageRecord): string =>
  record.role === 'user' ? record.client_message_id as string : record.id;

function progressFor(status: MagiMessageStatus): MagiMessage['progress'] {
  if (status === 'pending') return 'working';
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'cancelled';
  return 'complete';
}

function statusForProgress(progress: MagiMessage['progress']): MagiMessageStatus | null {
  if (progress === 'working' || progress === 'queued') return 'pending';
  if (progress === 'complete') return 'completed';
  if (progress === 'failed' || progress === 'cancelled') return progress;
  return null;
}

function isMagiStatusTransitionAllowed(
  previous: MagiMessageStatus,
  next: MagiMessageStatus,
): boolean {
  if (previous === next) return true;
  if (previous === 'pending') return next === 'completed' || next === 'failed' || next === 'cancelled';
  return previous === 'failed' && (next === 'pending' || next === 'completed');
}

function reconciliationDisposition(
  previous: MagiMessage | undefined,
  record: MagiMessageRecord,
): 'accept' | 'stale' | 'conflict' {
  if (!previous) return 'accept';
  const localPending = !previous.serverId && previous.role === 'user'
    && previous.sequenceIndex === undefined;
  if (localPending) {
    return record.role === 'user' && previous.id === record.client_message_id
      && previous.text === record.content
      && previous.source === (record.source === 'voice' ? 'voice' : 'text')
      ? 'accept' : 'conflict';
  }
  if (typeof previous.revision === 'number' && record.revision < previous.revision) {
    return 'stale';
  }
  // A native identity cannot change immutable attribution, provenance, or ordering.
  if (previous.serverId !== record.id || previous.id !== renderId(record)
    || previous.role !== record.role || previous.conversationId !== record.conversation_id
    || previous.clientMessageId !== record.client_message_id
    || previous.replyToServerId !== record.reply_to_message_id
    || previous.turnId !== record.turn_id || previous.sequenceIndex !== record.sequence_index
    || previous.sentAt !== record.created_at
    || previous.source !== (record.source === 'voice' ? 'voice' : 'text')
    || JSON.stringify(previous.attachments || []) !== JSON.stringify(record.attachments)
    || (previous.text !== record.content
      && (record.role === 'user' || previous.revision === record.revision
        || (previous.serverStatus !== 'pending' && previous.serverStatus !== 'failed')))
    || (previous.revision === record.revision && previous.serverStatus !== record.status)
    || (previous.serverStatus !== undefined && previous.revision !== undefined
      && record.revision > previous.revision
      && !isMagiStatusTransitionAllowed(previous.serverStatus, record.status))) {
    return 'conflict';
  }
  return 'accept';
}

function mergeRecord(previous: MagiMessage | undefined, record: MagiMessageRecord, pairStatus: MagiMessageStatus): MagiMessage {
  if (previous && reconciliationDisposition(previous, record) !== 'accept') return previous;
  const status = record.role === 'user' ? pairStatus : record.status;
  return {
    id: renderId(record),
    role: record.role,
    text: record.content,
    sentAt: record.created_at,
    source: record.source === 'voice' ? 'voice' : 'text',
    attachments: record.attachments.length ? record.attachments : undefined,
    progress: progressFor(status),
    delivery: record.role === 'user'
      ? status === 'failed' ? 'failed' : status === 'cancelled' ? 'cancelled' : 'sent'
      : undefined,
    serverId: record.id,
    conversationId: record.conversation_id,
    clientMessageId: record.client_message_id,
    replyToServerId: record.reply_to_message_id,
    serverStatus: record.status,
    revision: record.revision,
    turnId: record.turn_id,
    sequenceIndex: record.sequence_index,
    fromCache: false,
  };
}

/** Detect a server identity mutation while allowing a delayed stale revision. */
export function hasMagiReconciliationConflict(
  existing: MagiMessage[], incoming: MagiMessageRecord[],
): boolean {
  const rows = new Map(existing.map(message => [message.id, message]));
  const serverKeys = new Map(existing.flatMap(message => message.serverId
    ? [[message.serverId, message.id] as const] : []));
  for (const record of incoming) {
    const id = renderId(record);
    const serverKey = serverKeys.get(record.id);
    if (serverKey && serverKey !== id) return true;
    const previous = rows.get(id);
    if (reconciliationDisposition(previous, record) === 'conflict') return true;
    const merged = mergeRecord(previous, record, record.status);
    rows.set(id, merged);
    if (merged.serverId === record.id) serverKeys.set(record.id, id);
  }
  return false;
}

/** Merge HTTP/socket observations solely by server id and monotonic revision. */
export function reconcileMagiMessages(
  existing: MagiMessage[],
  incoming: MagiMessageRecord[],
  { authoritative = false } = {},
): MagiMessage[] {
  const rows = new Map(existing.map(message => [message.id, message]));
  const serverKeys = new Map(existing.flatMap(message => message.serverId ? [[message.serverId, message.id] as const] : []));
  const delivered = new Set<string>();
  const pairStatuses = new Map<string, MagiMessageStatus>();
  existing.forEach(message => {
    if (message.role === 'assistant' && message.turnId) {
      const status = message.serverStatus || statusForProgress(message.progress);
      if (status) pairStatuses.set(message.turnId, status);
    }
  });
  for (const record of incoming) {
    const id = renderId(record);
    const serverKey = serverKeys.get(record.id);
    if (serverKey && serverKey !== id) {
      // The same canonical server row cannot acquire a different render/client id.
      delivered.add(serverKey);
      continue;
    }
    delivered.add(id);
    const previous = rows.get(id);
    const merged = mergeRecord(previous, record, pairStatuses.get(record.turn_id) || record.status);
    rows.set(id, merged);
    if (merged.serverId === record.id) {
      serverKeys.set(record.id, id);
      if (record.role === 'assistant' && merged !== previous) {
        pairStatuses.set(record.turn_id, record.status);
      }
    }
  }
  // A socket completion normally revises only the assistant row. Project that
  // accepted pair status onto its already-present user row without mutating
  // canonical identity or revision.
  for (const [id, row] of rows) {
    if (row.role !== 'user' || !row.turnId) continue;
    const status = pairStatuses.get(row.turnId);
    if (!status) continue;
    rows.set(id, {
      ...row,
      progress: progressFor(status),
      delivery: status === 'failed' ? 'failed' : status === 'cancelled' ? 'cancelled' : 'sent',
    });
  }
  const recorded: MagiMessage[] = [];
  const pending: MagiMessage[] = [];
  for (const row of rows.values()) {
    if (typeof row.sequenceIndex === 'number') {
      // A bounded page is authoritative only for rows it includes. Keep a
      // validated cache row outside that window until history pagination loads
      // its server observation; revision ordering still governs replacements.
      if (!authoritative || delivered.has(row.id) || row.fromCache) recorded.push(row);
    } else if (row.role === 'user' && (row.delivery === 'sending' || row.delivery === 'failed')) pending.push(row);
  }
  recorded.sort((left, right) => (left.sequenceIndex as number) - (right.sequenceIndex as number));
  return [...recorded, ...pending];
}

export function sameMagiTranscript(left: MagiMessage[], right: MagiMessage[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => {
    const other = right[index];
    return row.id === other.id && row.role === other.role && row.text === other.text
      && row.source === other.source && row.delivery === other.delivery
      && row.progress === other.progress && row.sentAt === other.sentAt
      && row.serverId === other.serverId && row.conversationId === other.conversationId
      && row.clientMessageId === other.clientMessageId
      && row.replyToServerId === other.replyToServerId
      && row.serverStatus === other.serverStatus && row.revision === other.revision
      && row.turnId === other.turnId && row.sequenceIndex === other.sequenceIndex
      && row.fromCache === other.fromCache
      && JSON.stringify(row.attachments || []) === JSON.stringify(other.attachments || []);
  });
}
