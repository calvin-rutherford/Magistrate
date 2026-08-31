/**
 * The single identity/reconciliation contract for chat messages.
 *
 * Every delivery path - optimistic send, persisted history, Gateway history,
 * WebSocket, polling, delayed response, retry/regenerate, reload, reconnect,
 * cancellation - must derive identity here so the same event converges on one
 * row. Herdr exposes terminal text rather than durable ids, so the gateway
 * derives ids by hashing content (see `get_agent_history` in
 * gateway/app/herdr_client.py). That content mutates while a reply renders:
 * it grows line by line, reflows at a new terminal width, and loses its head
 * once the window scrolls. Treating each hash as a new message therefore
 * duplicates one reply, so identity alone is not enough - a revision of an
 * already-rendered row must update that row instead of appending a new one.
 */

export type IdentifiableMessage = { id?: string; role: string; kind?: string; text: string };

/** Content key: two reads of identical text converge even without server ids. */
export function messageContentKey(message: IdentifiableMessage): string {
  return `${message.role}|${message.kind || 'conversation'}|${message.text}`;
}

/**
 * The canonical dedupe identity. A server id wins because it distinguishes two
 * legitimately identical turns sent at different times; the content key is the
 * bounded fallback that still converges WebSocket and polling on one row when
 * an older gateway omits ids.
 */
export function messageIdentity(message: IdentifiableMessage): string {
  return message.id ? `id:${message.id}` : messageContentKey(message);
}

/** Stable local id for a terminal-derived row that arrived without one. */
export function fallbackMessageId(message: IdentifiableMessage): string {
  let hash = 2166136261;
  const key = messageContentKey(message);
  for (let index = 0; index < key.length; index += 1) hash = Math.imul(hash ^ key.charCodeAt(index), 16777619);
  return `history-${(hash >>> 0).toString(36)}`;
}

const normalizeForComparison = (value: string) => value.replace(/\s+/g, ' ').trim();

/** Shortest text that carries enough signal to claim two reads are one row. */
const MIN_REVISION_OVERLAP = 12;
/** Share of the shorter read that must agree before it counts as one row. */
const REVISION_AGREEMENT = 0.8;

const sharedPrefixLength = (a: string, b: string) => {
  let index = 0;
  while (index < a.length && index < b.length && a[index] === b[index]) index += 1;
  return index;
};
const sharedSuffixLength = (a: string, b: string) => {
  let index = 0;
  while (index < a.length && index < b.length && a[a.length - 1 - index] === b[b.length - 1 - index]) index += 1;
  return index;
};

/**
 * True when `next` is the same terminal row as `previous`, re-read after it
 * grew, reflowed, or scrolled its head out of the snapshot. Those mutations
 * leave either containment or a long shared prefix/suffix, and the agreement
 * threshold scales with length so two genuinely distinct replies that merely
 * open or close alike stay distinct.
 */
export function isTerminalRevision(previous: string, next: string): boolean {
  const before = normalizeForComparison(previous);
  const after = normalizeForComparison(next);
  if (!before || !after) return false;
  if (before === after) return true;
  const [shorter, longer] = before.length <= after.length ? [before, after] : [after, before];
  if (shorter.length < MIN_REVISION_OVERLAP) return false;
  if (longer.includes(shorter)) return true;
  const agreed = Math.max(sharedPrefixLength(before, after), sharedSuffixLength(before, after));
  return agreed >= Math.max(MIN_REVISION_OVERLAP, Math.floor(shorter.length * REVISION_AGREEMENT));
}

/**
 * Ids for rows whose text is already authoritative: the composer's own
 * submissions and a synchronous gateway reply (`run-<runId>` when the gateway
 * issued a run id, else the local `a-` fallback). Terminal output must never
 * rewrite these - it would replace the captain's submitted text or the
 * gateway's complete reply with a reflowed terminal rendering, and it is what
 * keeps two identical messages sent at different times two separate rows.
 */
const LOCALLY_AUTHORED_ID = /^(?:u-|a-|q-|run-|voice-u-|voice-a-)/;
export function isLocallyAuthoredId(id: string): boolean {
  return LOCALLY_AUTHORED_ID.test(id);
}

/**
 * The id of an existing row that `incoming` revises, or null when it is a new
 * message. Only the newest matching row is considered: an older turn is settled
 * conversation and must never be rewritten by later terminal output.
 */
export function revisionTargetId<T extends { id: string; role: string; kind?: string; text: string }>(
  existing: T[],
  incoming: IdentifiableMessage,
): string | null {
  if ((incoming.kind || 'conversation') !== 'conversation') return null;
  for (let index = existing.length - 1; index >= 0; index -= 1) {
    const candidate = existing[index];
    if ((candidate.kind || 'conversation') === 'tool') continue;
    if (candidate.role !== incoming.role) return null;
    // A terminal-derived row can be a still-growing composer line; a locally
    // authored one is a submitted message and is never revised.
    if (isLocallyAuthoredId(candidate.id)) return null;
    return isTerminalRevision(candidate.text, incoming.text) ? candidate.id : null;
  }
  return null;
}

/**
 * The one row in a snapshot batch that may revise an already-rendered reply:
 * its newest conversational agent row. Older rows in the same batch are
 * settled conversation and must never rewrite a newer one.
 */
export function terminalRevisionCandidate<T extends IdentifiableMessage>(messages: T[]): T | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if ((message.kind || 'conversation') === 'conversation') return message;
  }
  return null;
}
