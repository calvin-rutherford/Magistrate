/**
 * The client half of the canonical conversation contract.
 *
 * The gateway owns the captain transcript (see gateway/app/conversation_store.py
 * and CHAT_ARCHITECTURE_FIX.md). Every message it delivers has a durable id, a
 * turn, a type, and a sequence index, so the client's whole job is:
 *
 *   append when a new canonical message arrives, update when one changes.
 *
 * There is deliberately no text matching, no optimistic counting, no prompt
 * boundary heuristic, and no replay reconciliation here. Those existed only
 * because terminal snapshots had no stable identity; identity now comes from
 * the server. The one local link that remains is `client_message_id`: the
 * composer's own submission id, which is how an optimistic row is recognised as
 * the same message the server recorded rather than matched on its text.
 */
import { ConversationAttachment, ConversationMessage } from './ConversationSession';

export const CANONICAL_MESSAGE_TYPES = ['conversation', 'tool', 'internal', 'status'] as const;
export type CanonicalMessageType = (typeof CANONICAL_MESSAGE_TYPES)[number];

/** Types that may ever reach the transcript; internal/status are transport-only. */
const RENDERABLE_TYPES = new Set<CanonicalMessageType>(['conversation', 'tool']);

export interface CanonicalMessage {
  id: string;
  turn_id?: string;
  client_message_id?: string | null;
  role: 'user' | 'assistant';
  type: CanonicalMessageType;
  text: string;
  visible_in_chat?: boolean;
  sequence_index: number;
  revision?: number;
  source?: string;
  /** Authenticated references only; bytes remain in the bounded upload store. */
  attachments?: ConversationAttachment[];
  /** Gateway-authored Unix epoch milliseconds. */
  created_at?: number;
  turn_status?: string;
}

export interface CanonicalConversation {
  target: string;
  conversation_id?: string;
  messages: CanonicalMessage[];
}

const isCanonicalType = (value: unknown): value is CanonicalMessageType =>
  typeof value === 'string' && (CANONICAL_MESSAGE_TYPES as readonly string[]).includes(value);

const normalizeCanonicalAttachments = (raw: unknown): ConversationAttachment[] | undefined => {
  if (!Array.isArray(raw)) return undefined;
  return raw.slice(0, 10).flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const value = item as Record<string, unknown>;
    const uploadId = typeof value.upload_id === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(value.upload_id) ? value.upload_id : null;
    if (!uploadId || value.id !== uploadId || value.url !== `/api/v1/uploads/${uploadId}`
      || typeof value.name !== 'string' || !value.name || value.name.length > 160
      || typeof value.media_type !== 'string' || !value.media_type || value.media_type.length > 128
      || typeof value.size !== 'number' || !Number.isSafeInteger(value.size) || value.size < 0 || value.size > 25 * 1024 * 1024) return [];
    return [{
      name: value.name,
      mediaType: value.media_type,
      size: value.size,
      status: 'attached' as const,
      uploadId,
      url: `/api/v1/uploads/${uploadId}`,
    }];
  });
};

/**
 * Fail-closed validation of one delivered record. An unrecognised role, type,
 * or missing identity is dropped rather than guessed at: a row we cannot
 * address is a row we must not render.
 */
export function normalizeCanonicalMessage(raw: unknown): CanonicalMessage | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.id !== 'string' || !value.id || value.id.length > 128) return null;
  if (value.role !== 'user' && value.role !== 'assistant') return null;
  if (!isCanonicalType(value.type) || !RENDERABLE_TYPES.has(value.type)) return null;
  if (typeof value.text !== 'string' || !value.text.trim()) return null;
  if (typeof value.sequence_index !== 'number' || !Number.isFinite(value.sequence_index)) return null;
  return {
    id: value.id,
    turn_id: typeof value.turn_id === 'string' ? value.turn_id : undefined,
    client_message_id: typeof value.client_message_id === 'string' ? value.client_message_id : null,
    role: value.role,
    type: value.type,
    text: value.text,
    visible_in_chat: value.visible_in_chat === true,
    sequence_index: value.sequence_index,
    revision: typeof value.revision === 'number' && Number.isSafeInteger(value.revision) && value.revision >= 1 ? value.revision : undefined,
    source: value.source === 'voice' ? 'voice' : 'text',
    attachments: normalizeCanonicalAttachments(value.attachments),
    created_at: typeof value.created_at === 'number' && Number.isSafeInteger(value.created_at) && value.created_at >= 1_000_000_000_000 ? value.created_at : undefined,
    turn_status: typeof value.turn_status === 'string' ? value.turn_status : undefined,
  };
}

export function normalizeCanonicalMessages(raw: unknown): CanonicalMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(normalizeCanonicalMessage)
    .filter((message): message is CanonicalMessage => message !== null)
    .sort((left, right) => left.sequence_index - right.sequence_index);
}

/**
 * The transcript row id for a canonical message. A user message keeps the
 * composer's submission id, which is what makes the optimistic bubble and the
 * server's record one row instead of two.
 */
export function canonicalRowId(message: CanonicalMessage): string {
  return message.role === 'user' && message.client_message_id ? message.client_message_id : message.id;
}

const userDelivery = (turnStatus?: string): ConversationMessage['delivery'] =>
  turnStatus === 'failed' ? 'failed' : turnStatus === 'cancelled' ? 'cancelled' : 'sent';

const progressFor = (message: CanonicalMessage): ConversationMessage['progress'] =>
  message.turn_status === 'cancelled' ? 'cancelled'
    : message.turn_status === 'failed' ? 'failed'
      : message.turn_status === 'awaiting_reply' && message.role === 'user' ? 'working'
        : message.turn_status === 'streaming' ? (message.role === 'assistant' ? 'streaming' : 'working')
          : 'complete';

/**
 * A row this device created that the server has not acknowledged: a send still
 * in flight, or one that was rejected. Keeping it is what lets the composer show
 * its own message immediately and show a real failure afterwards.
 */
function isUnacknowledgedLocalRow(message: ConversationMessage): boolean {
  return message.role === 'user' && (message.delivery === 'sending' || message.delivery === 'failed');
}

function mergeRow(previous: ConversationMessage | undefined, message: CanonicalMessage): ConversationMessage {
  // HTTP polling and the socket race by design. Once revision N is rendered,
  // a delayed revision N-1 must not roll the same canonical row backwards.
  // Compare only within one canonical generation: after a server-side reset a
  // reused client submission id can legitimately point at a new message id.
  if (previous?.canonicalId === message.id
    && typeof previous.canonicalRevision === 'number'
    && typeof message.revision === 'number'
    && message.revision < previous.canonicalRevision) return previous;
  // The gateway owns time once it acknowledges a row. The composer's Date.now()
  // exists only on the optimistic placeholder and is replaced by this
  // millisecond-precision canonical timestamp.
  const sentAt = message.created_at;
  return {
    ...previous,
    id: canonicalRowId(message),
    role: message.role,
    kind: message.type === 'tool' ? 'tool' : 'conversation',
    text: message.text,
    sentAt,
    source: message.source === 'voice' ? 'voice' : 'text',
    attachments: message.attachments ?? previous?.attachments,
    audience: message.role === 'user' ? 'captain' : 'primary',
    delivery: message.role === 'user' ? userDelivery(message.turn_status) : previous?.delivery,
    progress: progressFor(message),
    canonicalId: message.id,
    canonicalRevision: message.revision ?? previous?.canonicalRevision,
    turnId: message.turn_id,
    sequenceIndex: message.sequence_index,
  };
}

/**
 * Merge canonical messages into the current transcript.
 *
 * `incoming` may be a full list or only the records whose revision changed, so
 * a row is addressed by id and ordered by the gateway's sequence index rather
 * than by its position in the delivered batch. UI-only fields outside the
 * canonical contract may be carried over from the row already rendered, but
 * identity, ordering, source, status, text, attachments, and time all come from
 * the gateway record once that row exists.
 *
 * A row that carries no canonical sequence and is not an in-flight local send
 * is dropped: that is what stops a stale or contaminated cache from surviving
 * the first sync. Pass `authoritative` for a full list read, where a recorded
 * row the server no longer returns has genuinely left the record.
 */
export function reconcileCanonicalMessages(
  existing: ConversationMessage[],
  incoming: CanonicalMessage[],
  { authoritative = false } = {},
): ConversationMessage[] {
  const rows = new Map(existing.map(message => [message.id, message]));
  const delivered = new Set<string>();
  for (const message of incoming) {
    const id = canonicalRowId(message);
    delivered.add(id);
    rows.set(id, mergeRow(rows.get(id), message));
  }
  const recorded: ConversationMessage[] = [];
  const pending: ConversationMessage[] = [];
  for (const row of rows.values()) {
    if (typeof row.sequenceIndex === 'number') {
      if (!authoritative || delivered.has(row.id)) recorded.push(row);
    } else if (isUnacknowledgedLocalRow(row)) pending.push(row);
  }
  recorded.sort((left, right) => (left.sequenceIndex as number) - (right.sequenceIndex as number));
  return [...recorded, ...pending];
}

/** True when the two transcripts would render identically. */
export function sameRenderedTranscript(left: ConversationMessage[], right: ConversationMessage[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => {
    const other = right[index];
    return row.id === other.id && row.text === other.text && row.kind === other.kind
      && row.delivery === other.delivery && row.progress === other.progress
      && row.sentAt === other.sentAt
      && row.canonicalId === other.canonicalId
      && row.canonicalRevision === other.canonicalRevision
      && row.sequenceIndex === other.sequenceIndex;
  });
}
