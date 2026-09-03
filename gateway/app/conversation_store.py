"""The canonical conversation record for captain chat.

The terminal is not the chat database. Herdr exposes a mutable terminal
snapshot: the same logical reply grows line by line, reflows at a new width,
and eventually scrolls its head out of the buffer. Deriving the visible
transcript from that snapshot made every re-read look like a new message, which
is what produced duplicate user rows, duplicate assistant rows, and tool or
harness metadata rendered as prose.

This module owns the truth instead:

* ``/api/v1/captain/prompt`` creates exactly one turn and one canonical user
  message, keyed by the frontend's ``message_id``. Replaying the same
  ``message_id`` reuses both.
* Terminal output is an *adapter*: :func:`ingest_terminal_rows` maps snapshot
  rows onto the turn they belong to and **upserts** that turn's primary
  assistant reply and tool events. Evolving output revises a row; it never
  appends a second one.
* Only ``type='conversation'`` messages are visible chat. ``tool`` events are
  delivered as bounded labels for the explicit "show tool calls" option;
  ``internal`` and ``status`` events are never delivered to a chat client.

Rows that belong to no known turn are not recorded at all: an unattributed
terminal row has no audience and must fail closed rather than become chat.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app import db
from app.contracts import (
    MagiAssistantBlockRemoveEvent,
    MagiAssistantBlockUpsertEvent,
    MagiAssistantCancelledEvent,
    MagiAssistantCompletedEvent,
    MagiAssistantFailedEvent,
    MagiAssistantStartedEvent,
    MagiResponseV1,
    magi_response_plain_text,
)
from app.herdr_client import classify_history_rows, is_pi_status_footer, tool_call_preview

CONVERSATION_SCHEMA = 'conversation.v1'

MESSAGE_TYPES = ('conversation', 'tool', 'internal', 'status')
TURN_STATUSES = ('awaiting_reply', 'streaming', 'answered', 'cancelled', 'failed')

# Slots per turn, which also fixes render order: prompt, tool events, reply.
_PROMPT_SLOT = 'prompt'
_PRIMARY_SLOT = 'primary'
_SLOTS_PER_TURN = 1000
_PROMPT_OFFSET = 0
_EVENT_OFFSET = 1
_PRIMARY_OFFSET = _SLOTS_PER_TURN - 1

# Bounds. A conversation record must not grow without limit just because a
# terminal keeps producing rows.
MAX_TOOL_EVENTS_PER_TURN = 12
MAX_INTERNAL_EVENTS_PER_TURN = 8
# Primary prose is deliberately not tail-truncated. A local terminal buffer is
# already bounded; truncating the canonical row again would silently discard a
# prefix that can no longer be recovered after the viewport slides.
MAX_MESSAGE_WINDOW = 200
TURN_MATCH_WINDOW = 40
# A prompt can scroll off Claude's alternate-screen viewport while its reply is
# still growing. Continue only from substantial assistant prose already bound
# to exactly one turn; shorter/common fragments are not an audience signal.
REPLY_CONTINUITY_MIN_CHARS = 40
MAX_ATTACHMENTS_PER_MESSAGE = 10
_SAFE_UPLOAD_ID = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
# SQLite is single-writer; the poll, the socket loop, and a prompt can all
# arrive together, so wait for the lock instead of failing the request.
_BUSY_TIMEOUT_SECONDS = 5.0
_TOKEN = re.compile(r'\S+')


class MagiEventConflict(ValueError):
    """A valid event conflicts with durable turn identity or ordering."""


def _now() -> int:
    """Unix epoch milliseconds, the canonical timestamp precision."""
    return int(time.time() * 1000)


def prompt_match_key(text: str) -> str:
    """The comparison form of a prompt.

    A terminal snapshot hard-wraps a prompt at the pane width and the parser
    rejoins it, so whitespace is not preserved end to end. Collapsing runs of
    whitespace is the only normalization applied - the words themselves must
    match exactly, because that equality is what attributes a reply to a turn.
    """
    return ' '.join((text or '').split())


@contextmanager
def _session():
    """One short transaction that always commits-or-rolls-back and closes."""
    conn = sqlite3.connect(db.DB_PATH, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _attachment_records(attachments: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Bounded public references to bytes owned by the authenticated upload store."""
    if attachments is None:
        return None
    if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError('A message may include at most 10 attachments.')
    records: List[Dict[str, Any]] = []
    for attachment in attachments:
        upload_id = str(attachment.get('upload_id') or '')
        name = str(attachment.get('filename') or attachment.get('name') or '')
        media_type = str(attachment.get('media_type') or '')
        size = attachment.get('size')
        if (not _SAFE_UPLOAD_ID.fullmatch(upload_id) or not name or len(name) > 160
                or not media_type or len(media_type) > 128 or not isinstance(size, int)
                or isinstance(size, bool) or size < 0 or size > 25 * 1024 * 1024):
            raise ValueError('Invalid canonical attachment metadata.')
        records.append({
            'id': upload_id,
            'upload_id': upload_id,
            'name': name,
            'media_type': media_type,
            'size': size,
            'url': f'/api/v1/uploads/{upload_id}',
        })
    return records


def _decode_attachments(value: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(value or '[]')
        if not isinstance(parsed, list):
            return []
        return _attachment_records(parsed) or []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _public_message(row: sqlite3.Row, client_message_id: Optional[str]) -> Dict[str, Any]:
    message = {
        'id': row['id'],
        'turn_id': row['turn_id'],
        'client_message_id': client_message_id,
        'role': row['role'],
        'type': row['type'],
        'text': row['text'],
        'visible_in_chat': bool(row['visible_in_chat']),
        'sequence_index': row['sequence_index'],
        'revision': row['revision'],
        'source': row['source'],
        'attachments': _decode_attachments(row['attachments_json']),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }
    if row['role'] == 'assistant' and row['type'] == 'conversation':
        structured = None
        if row['content_source'] == 'structured' and row['structured_content_json']:
            try:
                structured = MagiResponseV1.model_validate_json(row['structured_content_json'])
            except (TypeError, ValueError):
                # Database/cache corruption cannot turn unknown JSON into a
                # render instruction. The already-bounded plain text remains a
                # safe legacy fallback.
                structured = None
        if structured is None:
            message['content_source'] = 'terminal-fallback'
        else:
            message['content_source'] = 'structured'
            message['structured_content'] = structured.model_dump(mode='json')
            message['structured_revision'] = row['structured_revision']
    return message


def _new_message_id(conn: sqlite3.Connection) -> str:
    while True:
        message_id = 'cm_' + secrets.token_hex(10)
        used = conn.execute(
            '''SELECT 1 FROM conversation_messages WHERE id = ?
               UNION ALL
               SELECT 1 FROM conversation_turns WHERE assistant_message_id = ?
               LIMIT 1''',
            (message_id, message_id),
        ).fetchone()
        if used is None:
            return message_id


def _reserve_assistant_message_id(conn: sqlite3.Connection, turn_id: str) -> str:
    turn = conn.execute(
        'SELECT assistant_message_id FROM conversation_turns WHERE id = ?', (turn_id,),
    ).fetchone()
    if turn is None:
        raise LookupError('Conversation turn not found.')
    if turn['assistant_message_id']:
        return turn['assistant_message_id']
    primary = conn.execute(
        'SELECT id FROM conversation_messages WHERE turn_id = ? AND slot = ?',
        (turn_id, _PRIMARY_SLOT),
    ).fetchone()
    message_id = primary['id'] if primary else _new_message_id(conn)
    conn.execute(
        'UPDATE conversation_turns SET assistant_message_id = ?, updated_at = ? WHERE id = ?',
        (message_id, _now(), turn_id),
    )
    return message_id


def ensure_conversation(user_id: str, target: str) -> str:
    """The conversation id for this user/target, creating it on first use."""
    with _session() as conn:
        return _ensure_conversation(conn, user_id, target)


def _ensure_conversation(conn: sqlite3.Connection, user_id: str, target: str) -> str:
    row = conn.execute(
        'SELECT id FROM conversations WHERE user_id = ? AND target = ?', (user_id, target)
    ).fetchone()
    if row:
        return row['id']
    now = _now()
    conversation_id = 'cv_' + secrets.token_hex(8)
    conn.execute(
        'INSERT INTO conversations (id, user_id, target, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        (conversation_id, user_id, target, now, now),
    )
    return conversation_id


def _touch_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (_now(), conversation_id))


def _next_turn_index(conn: sqlite3.Connection, conversation_id: str) -> int:
    row = conn.execute(
        'SELECT MAX(sequence_index) AS top FROM conversation_turns WHERE conversation_id = ?',
        (conversation_id,),
    ).fetchone()
    return (row['top'] + 1) if row and row['top'] is not None else 0


def _sequence_for(turn_index: int, offset: int) -> int:
    return turn_index * _SLOTS_PER_TURN + offset


def _upsert_message(
    conn: sqlite3.Connection, *, conversation_id: str, turn_id: str, turn_index: int,
    slot: str, offset: int, role: str, message_type: str, text: str, visible: bool,
    source: str, force: bool = False,
    attachments: Optional[List[Dict[str, Any]]] = None,
    structurally_bounded: bool = False,
    observation_complete: bool = False,
) -> Optional[Dict[str, Any]]:
    """Create or revise the one message holding ``slot`` in this turn.

    Returns the public record when it changed, else None so callers can deliver
    only real changes. A revision that would *lose* content is refused: the
    retained terminal buffer scrolls, so a later read of the same reply can be a
    strict subset of what was already recorded. ``force`` skips that guard for
    text the client itself submitted, where a shorter edit is a real correction
    rather than a partial re-read.

    Terminal-derived primary replies are admitted only after
    :func:`_terminal_reply_respects_known_user_boundary` has paired the
    snapshot's structural prompt boundaries. This is the second, independent
    defense against cross-role duplication: a classifier failure cannot extend
    an earlier canonical reply merely because its text overlaps a later prompt.
    """
    if not text:
        return None
    existing = conn.execute(
        'SELECT * FROM conversation_messages WHERE turn_id = ? AND slot = ?', (turn_id, slot)
    ).fetchone()
    now = _now()
    attachment_records = _attachment_records(attachments)
    attachments_json = json.dumps(attachment_records, separators=(',', ':'), sort_keys=True) if attachment_records is not None else None
    if existing is None:
        message_id = (
            _reserve_assistant_message_id(conn, turn_id)
            if slot == _PRIMARY_SLOT else _new_message_id(conn)
        )
        conn.execute(
            '''INSERT INTO conversation_messages
               (id, turn_id, conversation_id, role, type, slot, text, visible_in_chat,
                sequence_index, revision, source, attachments_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)''',
            (message_id, turn_id, conversation_id, role, message_type, slot, text,
             1 if visible else 0, _sequence_for(turn_index, offset), source,
             attachments_json or '[]', now, now),
        )
        return _public_message(
            conn.execute('SELECT * FROM conversation_messages WHERE id = ?', (message_id,)).fetchone(),
            None,
        )
    # Semantic content is authoritative once accepted. Terminal snapshots and
    # late synchronous text may still arrive, but they can no longer revise the
    # primary row's document or its plain-text projection.
    if slot == _PRIMARY_SLOT and existing['content_source'] == 'structured':
        return None
    attachments_changed = attachments_json is not None and existing['attachments_json'] != attachments_json
    if not force and message_type == 'conversation':
        if structurally_bounded:
            # A later prompt boundary makes this segment ordered, but does not
            # prove that a partial observation contains the whole earlier reply.
            # Preserve the stored prose prefix unless the harness also gives us
            # an explicit completion observation. This is intentionally a
            # structural decision; never subtract prompt text or use textual
            # similarity to decide what to remove.
            if observation_complete:
                text = text.strip()
            else:
                merged = merge_captured_text(existing['text'], text)
                if merged is None or len(merged) < len(existing['text']):
                    return None
                text = merged
        else:
            merged = merge_captured_text(existing['text'], text)
            if merged is None:
                # Fail closed. A new disjoint window cannot prove whether it is
                # a continuation, a different audience, or a provider correction;
                # preserving the captured prefix is safer than replacing it.
                return None
            text = merged
    elif not force and existing['text'] == text:
        return None
    if existing['text'] == text and not attachments_changed:
        return None
    if attachments_json is None:
        conn.execute(
            'UPDATE conversation_messages SET text = ?, revision = revision + 1, updated_at = ? WHERE id = ?',
            (text, now, existing['id']),
        )
    else:
        conn.execute(
            '''UPDATE conversation_messages
               SET text = ?, attachments_json = ?, revision = revision + 1, updated_at = ?
               WHERE id = ?''',
            (text, attachments_json, now, existing['id']),
        )
    return _public_message(
        conn.execute('SELECT * FROM conversation_messages WHERE id = ?', (existing['id'],)).fetchone(),
        None,
    )


def _suffix_prefix_length(left, right) -> int:
    """Length of the longest suffix of ``left`` equal to a prefix of ``right``.

    This is a KMP fold rather than a quadratic suffix scan: a retained terminal
    window can be large, and every socket/poll read exercises this path.
    """
    if not left or not right:
        return 0
    prefix = [0] * len(right)
    matched = 0
    for index in range(1, len(right)):
        while matched and right[index] != right[matched]:
            matched = prefix[matched - 1]
        if right[index] == right[matched]:
            matched += 1
        prefix[index] = matched
    matched = 0
    for value in left:
        while matched and (matched == len(right) or value != right[matched]):
            matched = prefix[matched - 1]
        if matched < len(right) and value == right[matched]:
            matched += 1
    return matched


def _token_spans(text: str) -> List[Tuple[str, int, int]]:
    return [(match.group(0), match.start(), match.end()) for match in _TOKEN.finditer(text)]


def _subsequence_index(haystack: List[str], needle: List[str]) -> int:
    """Find a contiguous token sequence without quadratic window slicing."""
    if not needle or len(needle) > len(haystack):
        return -1
    prefix = [0] * len(needle)
    matched = 0
    for index in range(1, len(needle)):
        while matched and needle[index] != needle[matched]:
            matched = prefix[matched - 1]
        if needle[index] == needle[matched]:
            matched += 1
        prefix[index] = matched
    for index, value in enumerate(haystack):
        while matched and value != needle[matched]:
            matched = prefix[matched - 1]
        if value == needle[matched]:
            matched += 1
            if matched == len(needle):
                return index - len(needle) + 1
    return -1


def _substantial_token_overlap(tokens: List[Tuple[str, int, int]], count: int) -> bool:
    if count >= 2:
        return True
    return count == 1 and len(tokens[0][0]) >= REPLY_CONTINUITY_MIN_CHARS


def merge_captured_text(stored: str, incoming: str) -> Optional[str]:
    """Return the lossless union of two views of one logical reply.

    The terminal can first expose ``A B C`` and later only ``B C D``. Exact
    containment handles ordinary streaming and delayed duplicate reads; token
    overlap handles hard-wrap/reflow changes while preserving the original
    formatting; suffix/prefix overlap joins sliding windows in either arrival
    order. No overlap returns ``None`` instead of guessing or concatenating two
    possibly duplicated renderings.
    """
    stored, incoming = (stored or '').strip(), (incoming or '').strip()
    if not stored:
        return incoming or None
    if not incoming or stored == incoming or incoming in stored:
        return stored
    if stored in incoming:
        return incoming

    stored_flat, incoming_flat = ' '.join(stored.split()), ' '.join(incoming.split())
    if stored_flat == incoming_flat or incoming_flat in stored_flat:
        return stored
    if stored_flat in incoming_flat:
        return incoming

    old_spans, new_spans = _token_spans(stored), _token_spans(incoming)
    old_tokens = [item[0] for item in old_spans]
    new_tokens = [item[0] for item in new_spans]
    if old_tokens and new_tokens:
        if _subsequence_index(old_tokens, new_tokens) >= 0:
            return stored
        if _subsequence_index(new_tokens, old_tokens) >= 0:
            return incoming

        append_overlap = _suffix_prefix_length(old_tokens, new_tokens)
        prepend_overlap = _suffix_prefix_length(new_tokens, old_tokens)
        append_safe = _substantial_token_overlap(new_spans, append_overlap)
        prepend_safe = _substantial_token_overlap(old_spans, prepend_overlap)
        if append_safe and (not prepend_safe or append_overlap >= prepend_overlap):
            # Start immediately after the overlapping token; this retains the
            # incoming whitespace/paragraph separator before its first new word.
            tail = incoming[new_spans[append_overlap - 1][2]:]
            return stored.rstrip() + tail
        if prepend_safe:
            overlap_start = len(new_spans) - prepend_overlap
            head = incoming[:new_spans[overlap_start][1]]
            return head + stored.lstrip()

    # A provider can stream through the middle of one long token. Keep this
    # character fallback conservative; normal prose and the A/B/C adversary use
    # the safer token path above.
    append_chars = _suffix_prefix_length(stored, incoming)
    prepend_chars = _suffix_prefix_length(incoming, stored)
    if append_chars >= 8 and len(stored[-append_chars:].strip()) >= 4 and append_chars >= prepend_chars:
        return stored + incoming[append_chars:]
    if prepend_chars >= 8 and len(incoming[-prepend_chars:].strip()) >= 4:
        return incoming[:-prepend_chars] + stored
    return None


def record_prompt(
    user_id: str, target: str, client_message_id: str, text: str, *, source: str = 'text',
    submitted_text: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Record one canonical user turn, idempotent on ``client_message_id``.

    The frontend's submission id is the turn's identity, so a retry, a double
    tap, or a replayed request reuses the same turn and the same user message.

    ``text`` is what the captain wrote and is what chat renders.
    ``submitted_text`` is what the provider actually received (it can carry an
    attachment manifest or routing prefix), and is stored separately as the key
    the terminal adapter matches its snapshot rows against.
    """
    if not client_message_id:
        raise ValueError('A client message id is required to record a conversation turn.')
    with _session() as conn:
        conversation_id = _ensure_conversation(conn, user_id, target)
        turn = conn.execute(
            'SELECT * FROM conversation_turns WHERE conversation_id = ? AND client_message_id = ?',
            (conversation_id, client_message_id),
        ).fetchone()
        created = turn is None
        if turn is None:
            now = _now()
            turn_id = 'ct_' + secrets.token_hex(8)
            turn_index = _next_turn_index(conn, conversation_id)
            assistant_message_id = _new_message_id(conn)
            conn.execute(
                '''INSERT INTO conversation_turns
                   (id, conversation_id, client_message_id, prompt_key, assistant_message_id,
                    status, sequence_index, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'awaiting_reply', ?, ?, ?)''',
                (turn_id, conversation_id, client_message_id,
                 prompt_match_key(submitted_text if submitted_text is not None else text),
                 assistant_message_id, turn_index, now, now),
            )
        else:
            turn_id, turn_index = turn['id'], turn['sequence_index']
            assistant_message_id = _reserve_assistant_message_id(conn, turn_id)
            # An edited resubmission keeps its turn but replaces both the text
            # chat shows and the key the terminal adapter matches against.
            conn.execute(
                'UPDATE conversation_turns SET prompt_key = ?, updated_at = ? WHERE id = ?',
                (prompt_match_key(submitted_text if submitted_text is not None else text), _now(), turn_id),
            )
        # A turn always carries a user message: the client's transcript row is
        # keyed to it, so a prompt with no typed text falls back to what the
        # provider actually received rather than leaving the turn headless.
        _upsert_message(
            conn, conversation_id=conversation_id, turn_id=turn_id, turn_index=turn_index,
            slot=_PROMPT_SLOT, offset=_PROMPT_OFFSET, role='user', message_type='conversation',
            text=text or (submitted_text or ''), visible=True, source=source, force=True,
            attachments=attachments,
        )
        _touch_conversation(conn, conversation_id)
        return {
            'conversation_id': conversation_id, 'turn_id': turn_id,
            'assistant_message_id': assistant_message_id, 'created': created,
            'messages': _turn_messages(conn, turn_id, client_message_id),
        }


def record_primary_reply(
    user_id: str, target: str, turn_id: str, text: str, *, source: str = 'text',
) -> List[Dict[str, Any]]:
    """Record a reply the provider returned synchronously for a known turn."""
    text = (text or '').strip()
    if not text or is_pi_status_footer(text):
        return []
    with _session() as conn:
        turn = conn.execute('SELECT * FROM conversation_turns WHERE id = ?', (turn_id,)).fetchone()
        # Stop/failure freezes the turn. The prompt request can finish after a
        # concurrent cancel (aborting the client's HTTP request does not abort
        # provider work), and that late synchronous result must not resurrect
        # the turn or appear beside the stopped partial response.
        if turn is None or turn['status'] in ('cancelled', 'failed'):
            return []
        primary = conn.execute(
            'SELECT content_source FROM conversation_messages WHERE turn_id = ? AND slot = ?',
            (turn_id, _PRIMARY_SLOT),
        ).fetchone()
        if primary is not None and primary['content_source'] == 'structured':
            return []
        changed = _upsert_message(
            conn, conversation_id=turn['conversation_id'], turn_id=turn_id,
            turn_index=turn['sequence_index'], slot=_PRIMARY_SLOT, offset=_PRIMARY_OFFSET,
            role='assistant', message_type='conversation', text=text,
            visible=True, source=source,
        )
        # A synchronous provider return is an explicit completion observation,
        # even when its text is byte-for-byte identical to a streaming row.
        _set_turn_status(conn, turn_id, 'answered')
        if changed or turn['status'] != 'answered':
            _touch_conversation(conn, turn['conversation_id'])
        return [changed] if changed else []


def _canonical_event_hash(event: Any) -> str:
    encoded = json.dumps(
        event.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _stored_structured_response(row: Optional[sqlite3.Row]) -> Optional[MagiResponseV1]:
    if row is None or row['content_source'] != 'structured' or not row['structured_content_json']:
        return None
    try:
        return MagiResponseV1.model_validate_json(row['structured_content_json'])
    except (TypeError, ValueError) as exc:
        raise MagiEventConflict('The stored structured response is invalid and cannot be revised.') from exc


def _upsert_structured_primary(
    conn: sqlite3.Connection, *, turn: sqlite3.Row, message_id: str,
    response: MagiResponseV1, event_revision: int,
) -> Dict[str, Any]:
    """Make a validated semantic document the authoritative primary reply."""
    existing = conn.execute(
        'SELECT * FROM conversation_messages WHERE turn_id = ? AND slot = ?',
        (turn['id'], _PRIMARY_SLOT),
    ).fetchone()
    text = magi_response_plain_text(response)
    document = json.dumps(
        response.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    )
    now = _now()
    if existing is None:
        conn.execute(
            '''INSERT INTO conversation_messages
               (id, turn_id, conversation_id, role, type, slot, text, visible_in_chat,
                sequence_index, revision, source, attachments_json, content_source,
                structured_content_json, structured_revision, created_at, updated_at)
               VALUES (?, ?, ?, 'assistant', 'conversation', ?, ?, 1, ?, 1,
                       'magi-event', '[]', 'structured', ?, ?, ?, ?)''',
            (message_id, turn['id'], turn['conversation_id'], _PRIMARY_SLOT, text,
             _sequence_for(turn['sequence_index'], _PRIMARY_OFFSET), document,
             event_revision, now, now),
        )
    else:
        if existing['id'] != message_id:
            raise MagiEventConflict('The event message id does not match this turn.')
        conn.execute(
            '''UPDATE conversation_messages
               SET role = 'assistant', type = 'conversation', text = ?, visible_in_chat = 1,
                   source = 'magi-event', content_source = 'structured',
                   structured_content_json = ?, structured_revision = ?,
                   revision = revision + 1, updated_at = ?
               WHERE id = ?''',
            (text, document, event_revision, now, message_id),
        )
    return _public_message(
        conn.execute('SELECT * FROM conversation_messages WHERE id = ?', (message_id,)).fetchone(),
        None,
    )


def _magi_event_result(
    conn: sqlite3.Connection, event: Any, status: str, changed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    turn = conn.execute(
        'SELECT status FROM conversation_turns WHERE id = ?', (event.turn_id,),
    ).fetchone()
    if changed is None:
        primary = conn.execute(
            'SELECT * FROM conversation_messages WHERE turn_id = ? AND slot = ?',
            (event.turn_id, _PRIMARY_SLOT),
        ).fetchone()
        changed = _public_message(primary, None) if primary is not None else None
    return {
        'status': status,
        'event_id': event.event_id,
        'event_type': event.event_type,
        'turn_id': event.turn_id,
        'message_id': event.message_id,
        'revision': event.revision,
        'turn_status': turn['status'] if turn else None,
        'message': changed,
    }


def apply_magi_event(user_id: str, target: str, event: Any) -> Dict[str, Any]:
    """Apply one validated ``magi.event.v1`` event transactionally.

    Event ids and per-turn revisions are durable. The first event must be
    ``assistant.started`` revision 1; every later event is exactly the next
    revision. New block ids append, while an existing id updates only its
    current index. Deletion happens solely through ``assistant.block.remove``.
    A full completion document is authoritative and freezes the semantic stream.
    """
    payload_hash = _canonical_event_hash(event)
    with _session() as conn:
        # Serialize revision checks with the write they authorize. Polling and a
        # producer retry can otherwise both observe the same latest revision.
        conn.execute('BEGIN IMMEDIATE')
        turn = conn.execute(
            '''SELECT t.*, c.user_id AS owner_user_id, c.target AS target
               FROM conversation_turns t
               JOIN conversations c ON c.id = t.conversation_id
               WHERE t.id = ? AND c.user_id = ? AND c.target = ?''',
            (event.turn_id, user_id, target),
        ).fetchone()
        if turn is None:
            raise LookupError('Conversation turn not found.')
        reserved_message_id = _reserve_assistant_message_id(conn, event.turn_id)
        if event.message_id != reserved_message_id:
            raise MagiEventConflict('The event message id does not match this turn.')

        duplicate = conn.execute(
            'SELECT * FROM magi_response_events WHERE event_id = ?', (event.event_id,),
        ).fetchone()
        if duplicate is not None:
            if (
                duplicate['turn_id'] != event.turn_id
                or duplicate['message_id'] != event.message_id
                or duplicate['payload_sha256'] != payload_hash
            ):
                raise MagiEventConflict('That event id was already used for different content.')
            return _magi_event_result(conn, event, 'duplicate')

        latest = conn.execute(
            '''SELECT * FROM magi_response_events
               WHERE turn_id = ? ORDER BY revision DESC LIMIT 1''',
            (event.turn_id,),
        ).fetchone()
        expected_revision = 1 if latest is None else latest['revision'] + 1
        if event.revision != expected_revision:
            if event.revision < expected_revision:
                raise MagiEventConflict('That event revision was already accepted for this turn.')
            raise MagiEventConflict(f'Expected event revision {expected_revision}.')
        if latest is None:
            if not isinstance(event, MagiAssistantStartedEvent):
                raise MagiEventConflict('The first event must be assistant.started revision 1.')
        else:
            if isinstance(event, MagiAssistantStartedEvent):
                raise MagiEventConflict('assistant.started may appear only once.')
            if latest['event_type'] in {
                'assistant.completed', 'assistant.failed', 'assistant.cancelled',
            }:
                raise MagiEventConflict('The structured response stream is already terminal.')
        if turn['status'] in ('cancelled', 'failed'):
            raise MagiEventConflict('The conversation turn is already terminal.')

        primary = conn.execute(
            'SELECT * FROM conversation_messages WHERE turn_id = ? AND slot = ?',
            (event.turn_id, _PRIMARY_SLOT),
        ).fetchone()
        current_response = _stored_structured_response(primary)
        changed = None
        next_status = 'streaming'

        if isinstance(event, MagiAssistantBlockUpsertEvent):
            blocks = list(current_response.blocks) if current_response else []
            existing_index = next(
                (index for index, block in enumerate(blocks) if block.block_id == event.block.block_id),
                None,
            )
            if existing_index is None:
                if event.block_index != len(blocks):
                    raise MagiEventConflict('A new block must append at the next explicit index.')
                blocks.append(event.block)
            else:
                if event.block_index != existing_index:
                    raise MagiEventConflict('An existing block may update only at its stable index.')
                blocks[existing_index] = event.block
            response = MagiResponseV1(
                schema_version='magi.response.v1', blocks=blocks,
                actions=list(current_response.actions) if current_response else [],
            )
            changed = _upsert_structured_primary(
                conn, turn=turn, message_id=event.message_id,
                response=response, event_revision=event.revision,
            )
        elif isinstance(event, MagiAssistantBlockRemoveEvent):
            if current_response is None:
                raise MagiEventConflict('There is no structured block to remove.')
            blocks = [block for block in current_response.blocks if block.block_id != event.block_id]
            if len(blocks) == len(current_response.blocks):
                raise MagiEventConflict('The requested structured block does not exist.')
            if not blocks:
                raise MagiEventConflict('A correction cannot remove the final response block.')
            response = MagiResponseV1(
                schema_version='magi.response.v1', blocks=blocks,
                actions=list(current_response.actions),
            )
            changed = _upsert_structured_primary(
                conn, turn=turn, message_id=event.message_id,
                response=response, event_revision=event.revision,
            )
        elif isinstance(event, MagiAssistantCompletedEvent):
            changed = _upsert_structured_primary(
                conn, turn=turn, message_id=event.message_id,
                response=event.response, event_revision=event.revision,
            )
            next_status = 'answered'
        elif isinstance(event, MagiAssistantFailedEvent):
            next_status = 'failed'
        elif isinstance(event, MagiAssistantCancelledEvent):
            next_status = 'cancelled'

        _set_turn_status(conn, event.turn_id, next_status)
        conn.execute(
            '''INSERT INTO magi_response_events
               (event_id, turn_id, message_id, revision, event_type, payload_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (event.event_id, event.turn_id, event.message_id, event.revision,
             event.event_type, payload_hash, _now()),
        )
        _touch_conversation(conn, turn['conversation_id'])
        return _magi_event_result(conn, event, 'applied', changed)


def _set_turn_status(conn: sqlite3.Connection, turn_id: str, status: str) -> None:
    if status not in TURN_STATUSES:
        raise ValueError(f'Unknown turn status: {status}')
    current = conn.execute(
        'SELECT status FROM conversation_turns WHERE id = ?', (turn_id,)
    ).fetchone()
    # Cancellation/failure are terminal outcomes. In particular, a provider
    # failure can race an explicit stop and must not rewrite "cancelled" after
    # the captain has already frozen the turn.
    if current is None or (current['status'] in ('cancelled', 'failed') and current['status'] != status):
        return
    conn.execute(
        'UPDATE conversation_turns SET status = ?, updated_at = ? WHERE id = ? AND status != ?',
        (status, _now(), turn_id, status),
    )


def set_turn_status(user_id: str, target: str, client_message_id: str, status: str) -> None:
    """Mark a turn cancelled or failed from an explicit client action."""
    with _session() as conn:
        conversation_id = _ensure_conversation(conn, user_id, target)
        turn = conn.execute(
            'SELECT id FROM conversation_turns WHERE conversation_id = ? AND client_message_id = ?',
            (conversation_id, client_message_id),
        ).fetchone()
        if turn:
            _set_turn_status(conn, turn['id'], status)


def turn_messages(turn_id: str) -> List[Dict[str, Any]]:
    """The deliverable messages of one turn, after any recording completed."""
    with _session() as conn:
        turn = conn.execute(
            'SELECT client_message_id FROM conversation_turns WHERE id = ?', (turn_id,)
        ).fetchone()
        if turn is None:
            return []
        return _turn_messages(conn, turn_id, turn['client_message_id'])


def _turn_messages(conn: sqlite3.Connection, turn_id: str, client_message_id: Optional[str]) -> List[Dict[str, Any]]:
    rows = conn.execute(
        '''SELECT m.*, t.status AS turn_status FROM conversation_messages m
           JOIN conversation_turns t ON t.id = m.turn_id
           WHERE m.turn_id = ? AND m.type IN ('conversation', 'tool')
           ORDER BY m.sequence_index''',
        (turn_id,),
    ).fetchall()
    return [
        {
            **_public_message(row, client_message_id if row['role'] == 'user' else None),
            'turn_status': row['turn_status'],
        }
        for row in rows
    ]


class _Segment:
    """One prompt row, or a continuity-anchored viewport head, and its activity."""

    __slots__ = ('prompt', 'prose', 'tools', 'internal', 'closed')

    def __init__(self, prompt: Optional[str]) -> None:
        self.prompt = prompt
        self.prose: List[str] = []
        self.tools: List[str] = []
        self.internal: List[str] = []
        # A later user/control boundary proves this response is no longer the
        # live tail even if the harness status observation is unavailable.
        self.closed = False

    @property
    def has_activity(self) -> bool:
        return bool(self.prose or self.tools or self.internal)


def build_segments(rows: Iterable[Dict[str, str]]) -> List[_Segment]:
    """Group classified snapshot rows into prompt-delimited segments.

    A user-role 'control' row is an internally addressed turn (a Firstmate
    instruction, harness chrome caught in a user-styled box), so it closes the
    segment the captain's prompt opened. Agent-side control rows are only noise
    interleaved with a reply and close nothing.
    """
    segments: List[_Segment] = []
    current: Optional[_Segment] = None
    saw_user_boundary = False
    for row in rows:
        role, kind = row.get('role'), row.get('kind')
        text = (row.get('text') or '').strip()
        if not text:
            continue
        if kind == 'control':
            if role == 'user':
                if current is not None:
                    current.closed = True
                current = None
                saw_user_boundary = True
            elif current is not None:
                current.internal.append(text)
            continue
        if role == 'user':
            if current is not None:
                current.closed = True
            saw_user_boundary = True
            current = _Segment(text)
            segments.append(current)
            continue
        if current is None:
            # Claude's alternate screen eventually pushes the prompt above the
            # visible viewport while leaving the growing assistant blocks at
            # its head. Preserve only that leading activity; assistant output
            # after an observed user/control boundary remains unattributed.
            if saw_user_boundary:
                continue
            current = _Segment(None)
            segments.append(current)
        if kind == 'tool':
            current.tools.append(text)
        else:
            current.prose.append(text)
    return segments


def join_primary_text(blocks: List[str]) -> str:
    """Build one ordered primary reply from prose separated by tool activity.

    Overlapping/repeated blocks are folded exactly once. Truly disjoint blocks
    are known, from their position inside one prompt-delimited snapshot, to be
    consecutive prose and are joined with a paragraph boundary. No prefix is
    dropped to satisfy a second arbitrary size cap.
    """
    joined = ''
    for raw in blocks:
        block = (raw or '').strip()
        if not block:
            continue
        if not joined:
            joined = block
            continue
        merged = merge_captured_text(joined, block)
        joined = merged if merged is not None else f'{joined}\n\n{block}'
    return joined


def _has_reply_continuity(stored: str, incoming: str) -> bool:
    """Whether assistant prose strongly anchors a promptless sliding window."""
    if not stored or not incoming:
        return False
    old_flat, new_flat = ' '.join(stored.split()), ' '.join(incoming.split())
    if min(len(old_flat), len(new_flat)) >= REPLY_CONTINUITY_MIN_CHARS and (
        old_flat in new_flat or new_flat in old_flat
    ):
        return True
    if max(
        _suffix_prefix_length(old_flat, new_flat),
        _suffix_prefix_length(new_flat, old_flat),
    ) >= REPLY_CONTINUITY_MIN_CHARS:
        return True
    # A newly recognised tool line can disappear from corrected prose. An
    # unchanged substantial block on either side remains a valid anchor.
    for old_block in stored.split('\n\n'):
        for new_block in incoming.split('\n\n'):
            old_normalized, new_normalized = ' '.join(old_block.split()), ' '.join(new_block.split())
            shared = 0
            for old_char, new_char in zip(old_normalized, new_normalized):
                if old_char != new_char:
                    break
                shared += 1
            if shared >= REPLY_CONTINUITY_MIN_CHARS:
                return True
    return False


def _terminal_reply_respects_known_user_boundary(
    turn: sqlite3.Row, turns: List[sqlite3.Row], observed_prompt_turn_ids: set[str],
) -> bool:
    """Named invariant: **reply-must-not-cross-known-user-boundary**.

    A later canonical user turn is an ordering fact, not text to search for.
    An earlier terminal reply may therefore be revised only when the same
    snapshot also exposes a structural prompt boundary for every later turn
    that could separate it. Text overlap is allowed by ``merge_captured_text``
    only after this gate; it can never open the gate or extend a reply across a
    known user turn by itself.
    """
    later = [candidate for candidate in turns if candidate['sequence_index'] > turn['sequence_index']]
    return not later or all(candidate['id'] in observed_prompt_turn_ids for candidate in later)


def _match_segments_to_turns(
    turns: List[sqlite3.Row], segments: List[_Segment],
) -> List[Tuple[sqlite3.Row, _Segment, bool]]:
    """Pair snapshot segments with turns using structural prompt boundaries.

    Prompt-bearing segments match newest-first. A single leading segment whose
    prompt has scrolled off may continue a turn only when its assistant prose
    overlaps the primary reply already attributed to exactly one recent turn.
    That continuity fallback is permitted only when every later canonical turn
    is also structurally observed in this snapshot. Thus neither a bad role
    classification nor text overlap can make turn N's reply consume turn N+1.
    """
    matched: List[Tuple[sqlite3.Row, _Segment, bool]] = []
    used: set[str] = set()
    observed_prompt_turn_ids: set[str] = set()
    keys = {
        turn['id']: prompt_match_key(turn['prompt_key'] or turn['prompt_text'] or '')
        for turn in turns
    }
    # First map every prompt-bearing segment, including a prompt with no reply
    # yet. Its existence is still the structural boundary needed to close an
    # earlier turn.
    for segment in reversed(segments):
        if segment.prompt is None:
            continue
        for turn in reversed(turns):
            if turn['id'] in used:
                continue
            if keys[turn['id']] and keys[turn['id']] == prompt_match_key(segment.prompt):
                used.add(turn['id'])
                observed_prompt_turn_ids.add(turn['id'])
                if segment.has_activity:
                    matched.append((
                        turn, segment,
                        bool(
                            _terminal_reply_respects_known_user_boundary(
                                turn, turns, observed_prompt_turn_ids
                            ) and any(
                                candidate['sequence_index'] > turn['sequence_index']
                                for candidate in turns
                            )
                        ),
                    ))
                break

    for segment in segments:
        if segment.prompt is not None or not segment.has_activity:
            continue
        incoming = join_primary_text(segment.prose)
        # Determine ambiguity before applying the ordering gate. Filtering
        # first would turn an ambiguous overlap into "the newest eligible
        # turn", which is exactly the open-turn guess this adapter must never
        # make.
        candidates = [
            turn for turn in turns
            if turn['id'] not in used
            and _has_reply_continuity(turn['reply_text'] or '', incoming)
        ]
        if len(candidates) == 1 and _terminal_reply_respects_known_user_boundary(
            candidates[0], turns, observed_prompt_turn_ids
        ):
            turn = candidates[0]
            used.add(turn['id'])
            matched.append((
                turn, segment,
                bool(any(candidate['sequence_index'] > turn['sequence_index'] for candidate in turns)),
            ))

    # This is the independent store-side gate. A parser can accidentally fold
    # a later user row into an older assistant segment; if its later canonical
    # prompt was not separately observed in this snapshot, discard the whole
    # older terminal revision rather than trying to subtract matching words.
    matched = [
        pair for pair in matched
        if _terminal_reply_respects_known_user_boundary(pair[0], turns, observed_prompt_turn_ids)
    ]
    matched.sort(key=lambda pair: pair[0]['sequence_index'])
    return matched


def _remove_structural_pi_footer_poison(
    conn: sqlite3.Connection, conversation_id: str,
) -> int:
    """Delete only terminal replies proven to be complete Pi telemetry rows.

    This repairs canonical rows written before the no-CH footer grammar existed.
    It deliberately does not search for similar text, trim mixed prose, or
    rewrite non-terminal sources. A retained corrected snapshot may then
    reconstruct a real reply through the normal segment-matching path.
    """
    candidates = conn.execute(
        '''SELECT id, text FROM conversation_messages
           WHERE conversation_id = ? AND role = 'assistant'
             AND type = 'conversation' AND slot = ? AND source = 'terminal' ''',
        (conversation_id, _PRIMARY_SLOT),
    ).fetchall()
    poisoned = [row['id'] for row in candidates if is_pi_status_footer(row['text'])]
    if not poisoned:
        return 0
    conn.executemany('DELETE FROM conversation_messages WHERE id = ?', [(item,) for item in poisoned])
    _touch_conversation(conn, conversation_id)
    return len(poisoned)


def _recent_turns(conn: sqlite3.Connection, conversation_id: str) -> List[sqlite3.Row]:
    """Turns the adapter may still write to.

    A cancelled turn is excluded: the captain stopped that response, so output
    the harness produces afterwards must not appear as its reply. A failed turn
    never reached the provider at all.
    """
    rows = conn.execute(
        '''SELECT t.id AS id, t.sequence_index AS sequence_index, t.status AS status,
                  t.client_message_id AS client_message_id, t.prompt_key AS prompt_key,
                  prompt.text AS prompt_text, reply.text AS reply_text
           FROM conversation_turns t
           LEFT JOIN conversation_messages prompt ON prompt.turn_id = t.id AND prompt.slot = ?
           LEFT JOIN conversation_messages reply ON reply.turn_id = t.id AND reply.slot = ?
           WHERE t.conversation_id = ? AND t.status NOT IN ('cancelled', 'failed')
           ORDER BY t.sequence_index DESC LIMIT ?''',
        (_PROMPT_SLOT, _PRIMARY_SLOT, conversation_id, TURN_MATCH_WINDOW),
    ).fetchall()
    return list(reversed(rows))


def ingest_terminal_rows(
    user_id: str, target: str, rows: Iterable[Dict[str, str]], *,
    response_complete: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Fold a classified terminal snapshot into the canonical record.

    This is the whole of the temporary terminal adapter. It only ever revises
    turns the gateway already knows about, so harness output that belongs to no
    submitted prompt cannot become visible chat.

    Rows are re-classified here even when the caller already typed them: the
    store is the source of truth, so the metadata firewall has to hold at the
    boundary that writes rather than at whichever reader happened to call it.
    Classification is idempotent.
    """
    rows = classify_history_rows(list(rows))
    changed: List[Dict[str, Any]] = []
    with _session() as conn:
        conversation_id = _ensure_conversation(conn, user_id, target)
        _remove_structural_pi_footer_poison(conn, conversation_id)
        turns = _recent_turns(conn, conversation_id)
        if not turns:
            return []
        matches = _match_segments_to_turns(turns, build_segments(rows))
        newest_turn_id = matches[-1][0]['id'] if matches else None
        for turn, segment, structurally_bounded in matches:
            complete = segment.closed or (
                response_complete is True and turn['id'] == newest_turn_id
            )
            changed.extend(_apply_segment(
                conn, conversation_id, turn, segment, complete=complete,
                structurally_bounded=structurally_bounded,
                # A later prompt closes the old segment for ordering/status,
                # but is not proof that the old reply was fully observed. Only
                # an explicit completion observation may replace stored prose.
                observation_complete=(response_complete is True),
            ))
        if changed:
            _touch_conversation(conn, conversation_id)
    return changed


def _apply_segment(
    conn: sqlite3.Connection, conversation_id: str, turn: sqlite3.Row, segment: _Segment,
    *, complete: bool, structurally_bounded: bool = False,
    observation_complete: bool = False,
) -> List[Dict[str, Any]]:
    # An accepted semantic lifecycle owns this turn from assistant.started
    # onward. Snapshot rows remain available in Herdr, but are fallback only and
    # must not race the structured stream's content or terminal outcome.
    if conn.execute(
        'SELECT 1 FROM magi_response_events WHERE turn_id = ? LIMIT 1', (turn['id'],),
    ).fetchone():
        return []
    changed: List[Dict[str, Any]] = []
    turn_index = turn['sequence_index']
    for index, raw in enumerate(segment.tools[:MAX_TOOL_EVENTS_PER_TURN]):
        # Only the bounded label is recorded. A tool row's raw text is a shell
        # command or file excerpt and can carry tokens or paths; it stays in the
        # terminal rather than entering the conversation record.
        event = _upsert_message(
            conn, conversation_id=conversation_id, turn_id=turn['id'], turn_index=turn_index,
            slot=f'tool:{index}', offset=_EVENT_OFFSET + index, role='assistant',
            message_type='tool', text=tool_call_preview(raw), visible=False, source='terminal',
        )
        if event:
            changed.append(event)
    for index, raw in enumerate(segment.internal[:MAX_INTERNAL_EVENTS_PER_TURN]):
        _upsert_message(
            conn, conversation_id=conversation_id, turn_id=turn['id'], turn_index=turn_index,
            slot=f'internal:{index}', offset=_EVENT_OFFSET + MAX_TOOL_EVENTS_PER_TURN + index,
            role='assistant', message_type='internal', text=raw[:2000], visible=False,
            source='terminal',
        )
    primary = join_primary_text(segment.prose)
    if primary:
        reply = _upsert_message(
            conn, conversation_id=conversation_id, turn_id=turn['id'], turn_index=turn_index,
            slot=_PRIMARY_SLOT, offset=_PRIMARY_OFFSET, role='assistant',
            message_type='conversation', text=primary, visible=True, source='terminal',
            structurally_bounded=structurally_bounded, observation_complete=observation_complete,
        )
        if reply:
            changed.append(reply)
    if segment.has_activity:
        # Prose/tool activity means the response started, not that it finished.
        # Only a later user boundary or an observed idle/done harness completes
        # the live tail. Never downgrade a completed/terminal historical turn.
        if complete and turn['status'] in ('awaiting_reply', 'streaming'):
            _set_turn_status(conn, turn['id'], 'answered')
        elif not complete and turn['status'] == 'awaiting_reply':
            _set_turn_status(conn, turn['id'], 'streaming')
    return changed


def list_messages(
    user_id: str, target: str, *, limit: int = MAX_MESSAGE_WINDOW, include_internal: bool = False,
) -> Dict[str, Any]:
    """The canonical transcript: conversation messages plus bounded tool events.

    'internal' and 'status' events are excluded from every chat payload; the
    flag exists for diagnostics and tests, never for a rendering client.
    """
    types = MESSAGE_TYPES if include_internal else ('conversation', 'tool')
    placeholders = ', '.join('?' for _ in types)
    with _session() as conn:
        conversation_id = _ensure_conversation(conn, user_id, target)
        _remove_structural_pi_footer_poison(conn, conversation_id)
        rows = conn.execute(
            f'''SELECT m.*, t.client_message_id AS client_message_id, t.status AS turn_status
                FROM conversation_messages m
                JOIN conversation_turns t ON t.id = m.turn_id
                WHERE m.conversation_id = ? AND m.type IN ({placeholders})
                ORDER BY m.sequence_index DESC LIMIT ?''',
            (conversation_id, *types, max(1, min(limit, MAX_MESSAGE_WINDOW))),
        ).fetchall()
    messages = [
        {
            **_public_message(row, row['client_message_id'] if row['role'] == 'user' else None),
            'turn_status': row['turn_status'],
        }
        for row in reversed(rows)
    ]
    return {
        'schema_version': CONVERSATION_SCHEMA,
        'target': target,
        'conversation_id': conversation_id,
        'messages': messages,
        'last_sequence_index': messages[-1]['sequence_index'] if messages else None,
    }


def reset_conversation(user_id: str, target: str) -> Dict[str, Any]:
    """Delete this conversation's canonical record.

    Poisoned local state is invalidated client-side by the storage version (see
    ConversationSession.ts); this is the server-side equivalent for an operator
    or a test that needs a clean thread.
    """
    with _session() as conn:
        conversation_id = _ensure_conversation(conn, user_id, target)
        conn.execute(
            '''DELETE FROM magi_response_events
               WHERE turn_id IN (SELECT id FROM conversation_turns WHERE conversation_id = ?)''',
            (conversation_id,),
        )
        conn.execute('DELETE FROM conversation_messages WHERE conversation_id = ?', (conversation_id,))
        conn.execute('DELETE FROM conversation_turns WHERE conversation_id = ?', (conversation_id,))
        _touch_conversation(conn, conversation_id)
    return {'status': 'reset', 'target': target, 'conversation_id': conversation_id}
