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
import type { ConversationAttachment, ConversationMessage } from './ConversationSession';
import { MAGI_MAX_FALLBACK_TEXT_CHARS, MagiResponseV1, normalizeMagiResponse } from './MagiResponse';

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
  lifecycle_state?: 'active' | 'awaiting-user' | 'completed' | 'failed' | 'cancelled';
  lifecycle_revision?: number;
  decision_key?: string | null;
  objective_id?: string;
  run_id?: string;
  assistant_kind?: 'response' | 'progress' | 'decision' | 'outcome';
  content_source?: 'structured' | 'terminal-fallback';
  structured_content?: MagiResponseV1;
  structured_revision?: number;
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
  const boundedIdentity = (candidate: unknown): candidate is string => typeof candidate === 'string'
    && candidate.length > 0 && Array.from(candidate).length <= 128
    && !Array.from(candidate).some(character => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || (code >= 127 && code <= 159)
        || (code >= 0xd800 && code <= 0xdfff) || code === 0x2028 || code === 0x2029;
    });
  if (!boundedIdentity(value.id)) return null;
  if (value.role !== 'user' && value.role !== 'assistant') return null;
  if (!isCanonicalType(value.type) || !RENDERABLE_TYPES.has(value.type)
    || (value.type === 'conversation'
      ? value.visible_in_chat !== true
      : value.role !== 'assistant' || value.visible_in_chat !== false)
    || !['text', 'voice', 'terminal', 'magi-event'].includes(String(value.source))
    || (value.type === 'tool' && value.source !== 'terminal')
    || (value.role === 'user' && value.source !== 'text' && value.source !== 'voice')
    || (value.content_source === 'structured' && value.source !== 'magi-event')) return null;
  const structuredRevision = typeof value.structured_revision === 'number'
    && Number.isSafeInteger(value.structured_revision) && value.structured_revision >= 1
    ? value.structured_revision : undefined;
  const structuredContent = value.role === 'assistant' && value.type === 'conversation'
    && value.content_source === 'structured' && structuredRevision
    ? normalizeMagiResponse(value.structured_content) : null;
  if (value.role === 'assistant' && value.type === 'conversation'
    && value.content_source !== undefined
    && value.content_source !== 'structured' && value.content_source !== 'terminal-fallback') return null;
  const maxText = value.role === 'assistant' && value.type === 'conversation'
    ? MAGI_MAX_FALLBACK_TEXT_CHARS : value.role === 'user' ? 100_000 : 20_000;
  if (typeof value.text !== 'string' || !value.text.trim() || Array.from(value.text).length > maxText) return null;
  if (typeof value.sequence_index !== 'number' || !Number.isSafeInteger(value.sequence_index) || value.sequence_index < 0) return null;
  if (!boundedIdentity(value.turn_id)) return null;
  if (typeof value.revision !== 'number' || !Number.isSafeInteger(value.revision) || value.revision < 1) return null;
  if (typeof value.created_at !== 'number' || !Number.isSafeInteger(value.created_at)
    || value.created_at < 1_000_000_000_000) return null;
  if (value.client_message_id !== undefined && value.client_message_id !== null
    && !boundedIdentity(value.client_message_id)) return null;
  if (value.role === 'assistant' && value.client_message_id !== undefined
    && value.client_message_id !== null) return null;
  if (value.turn_status !== undefined
    && !['awaiting_reply', 'streaming', 'answered', 'cancelled', 'failed'].includes(String(value.turn_status))) return null;
  const lifecycleState = ['active', 'awaiting-user', 'completed', 'failed', 'cancelled'].includes(String(value.lifecycle_state))
    ? value.lifecycle_state as CanonicalMessage['lifecycle_state'] : undefined;
  const lifecycleRevision = typeof value.lifecycle_revision === 'number'
    && Number.isSafeInteger(value.lifecycle_revision) && value.lifecycle_revision >= 1
    ? value.lifecycle_revision : undefined;
  const lifecycleProvided = value.lifecycle_state !== undefined || value.lifecycle_revision !== undefined
    || value.decision_key !== undefined || value.objective_id !== undefined || value.run_id !== undefined;
  const decisionKey = boundedIdentity(value.decision_key) ? value.decision_key : null;
  const assistantKind = value.role === 'assistant' && ['response', 'progress', 'decision', 'outcome'].includes(String(value.assistant_kind))
    ? value.assistant_kind as CanonicalMessage['assistant_kind'] : undefined;
  if (lifecycleProvided && (
    !lifecycleState || !lifecycleRevision || !boundedIdentity(value.objective_id)
    || !boundedIdentity(value.run_id)
    || (lifecycleState === 'awaiting-user' ? !decisionKey : value.decision_key !== null)
  )) return null;
  if ((value.assistant_kind !== undefined && !assistantKind)
    || (value.role !== 'assistant' && value.assistant_kind !== undefined)) return null;
  return {
    id: value.id,
    turn_id: typeof value.turn_id === 'string' ? value.turn_id : undefined,
    client_message_id: typeof value.client_message_id === 'string' ? value.client_message_id : null,
    role: value.role,
    type: value.type,
    text: value.text,
    visible_in_chat: value.visible_in_chat === true,
    sequence_index: value.sequence_index,
    revision: value.revision,
    source: value.source === 'voice' ? 'voice' : 'text',
    attachments: normalizeCanonicalAttachments(value.attachments),
    created_at: value.created_at,
    turn_status: typeof value.turn_status === 'string' ? value.turn_status : undefined,
    lifecycle_state: lifecycleState,
    lifecycle_revision: lifecycleRevision,
    decision_key: decisionKey,
    objective_id: boundedIdentity(value.objective_id) ? value.objective_id : undefined,
    run_id: boundedIdentity(value.run_id) ? value.run_id : undefined,
    assistant_kind: assistantKind,
    content_source: structuredContent ? 'structured' : value.role === 'assistant' && value.type === 'conversation' ? 'terminal-fallback' : undefined,
    structured_content: structuredContent || undefined,
    structured_revision: structuredContent ? structuredRevision : undefined,
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
  message.lifecycle_state === 'cancelled' || message.turn_status === 'cancelled' ? 'cancelled'
    : message.lifecycle_state === 'failed' || message.turn_status === 'failed' ? 'failed'
      : message.lifecycle_state === 'awaiting-user' ? 'complete'
        : message.lifecycle_state === 'active'
          ? (message.role === 'assistant' ? 'streaming' : 'working')
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
  // A higher content revision may grow prose or replace terminal fallback with
  // a semantic response. It still cannot move the stable row to another turn,
  // sequence, causal objective, role, kind, timestamp, or attachment identity.
  if (!previous?.fromCanonicalCache && previous?.canonicalId === message.id
    && typeof previous.canonicalRevision === 'number' && (
    previous.role !== message.role
    || previous.kind !== (message.type === 'tool' ? 'tool' : 'conversation')
    || previous.sequenceIndex !== message.sequence_index
    || previous.turnId !== message.turn_id
    || previous.sentAt !== message.created_at
    || previous.objectiveId !== message.objective_id
    || previous.runId !== message.run_id
    || previous.assistantKind !== message.assistant_kind
    || JSON.stringify(previous.attachments || []) !== JSON.stringify(message.attachments || [])
  )) return previous;
  // Turn/lifecycle status may advance without rewriting a message revision, but
  // prose and structured content at that revision cannot.
  if (!previous?.fromCanonicalCache && previous?.canonicalId === message.id
    && typeof previous.canonicalRevision === 'number'
    && previous.canonicalRevision === message.revision
    && (
      previous.text !== message.text
      || previous.structuredRevision !== message.structured_revision
      || previous.contentSource !== message.content_source
      || JSON.stringify(previous.structuredContent) !== JSON.stringify(message.structured_content)
    )) return previous;
  if (!previous?.fromCanonicalCache && previous?.canonicalId === message.id
    && typeof previous.lifecycleRevision === 'number'
    && previous.lifecycleRevision === message.lifecycle_revision
    && (previous.lifecycleState !== message.lifecycle_state
      || previous.decisionKey !== (message.decision_key || undefined))) return previous;
  // HTTP polling and the socket race by design. Once revision N is rendered,
  // a delayed revision N-1 must not roll the same canonical row backwards.
  // Compare only within one canonical generation: after a server-side reset a
  // reused client submission id can legitimately point at a new message id.
  if (!previous?.fromCanonicalCache && previous?.canonicalId === message.id
    && typeof previous.canonicalRevision === 'number'
    && (typeof message.revision !== 'number'
      || message.revision < previous.canonicalRevision)) return previous;
  const staleLifecycle = !previous?.fromCanonicalCache && previous?.canonicalId === message.id
    && typeof previous.lifecycleRevision === 'number'
    && (typeof message.lifecycle_revision !== 'number'
      || message.lifecycle_revision < previous.lifecycleRevision);
  const lifecycleMessage = staleLifecycle ? {
    ...message,
    lifecycle_state: previous.lifecycleState,
    lifecycle_revision: previous.lifecycleRevision,
    decision_key: previous.decisionKey || null,
  } : message;
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
    progress: progressFor(lifecycleMessage),
    canonicalId: message.id,
    canonicalRevision: message.revision ?? previous?.canonicalRevision,
    turnId: message.turn_id,
    sequenceIndex: message.sequence_index,
    objectiveId: message.objective_id ?? previous?.objectiveId,
    runId: message.run_id ?? previous?.runId,
    lifecycleState: lifecycleMessage.lifecycle_state,
    lifecycleRevision: lifecycleMessage.lifecycle_revision,
    decisionKey: lifecycleMessage.decision_key || undefined,
    assistantKind: message.assistant_kind ?? previous?.assistantKind,
    structuredContent: message.structured_content,
    contentSource: message.content_source,
    structuredRevision: message.structured_revision,
    fromCanonicalCache: undefined,
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
      && row.sequenceIndex === other.sequenceIndex
      && row.objectiveId === other.objectiveId
      && row.runId === other.runId
      && row.lifecycleState === other.lifecycleState
      && row.lifecycleRevision === other.lifecycleRevision
      && row.decisionKey === other.decisionKey
      && row.assistantKind === other.assistantKind
      && row.contentSource === other.contentSource
      && row.structuredRevision === other.structuredRevision
      && JSON.stringify(row.structuredContent) === JSON.stringify(other.structuredContent);
  });
}
