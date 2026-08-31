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

import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app import db
from app.herdr_client import classify_history_rows, tool_call_preview

CONVERSATION_SCHEMA = 'conversation.v1'

MESSAGE_TYPES = ('conversation', 'tool', 'internal', 'status')
TURN_STATUSES = ('awaiting_reply', 'answered', 'cancelled', 'failed')

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
MAX_PRIMARY_TEXT = 20_000
MAX_MESSAGE_WINDOW = 200
TURN_MATCH_WINDOW = 40
# SQLite is single-writer; the poll, the socket loop, and a prompt can all
# arrive together, so wait for the lock instead of failing the request.
_BUSY_TIMEOUT_SECONDS = 5.0


def _now() -> int:
    return int(time.time())


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


def _public_message(row: sqlite3.Row, client_message_id: Optional[str]) -> Dict[str, Any]:
    return {
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
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


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
) -> Optional[Dict[str, Any]]:
    """Create or revise the one message holding ``slot`` in this turn.

    Returns the public record when it changed, else None so callers can deliver
    only real changes. A revision that would *lose* content is refused: the
    retained terminal buffer scrolls, so a later read of the same reply can be a
    strict subset of what was already recorded. ``force`` skips that guard for
    text the client itself submitted, where a shorter edit is a real correction
    rather than a partial re-read.
    """
    if not text:
        return None
    existing = conn.execute(
        'SELECT * FROM conversation_messages WHERE turn_id = ? AND slot = ?', (turn_id, slot)
    ).fetchone()
    now = _now()
    if existing is None:
        message_id = 'cm_' + secrets.token_hex(10)
        conn.execute(
            '''INSERT INTO conversation_messages
               (id, turn_id, conversation_id, role, type, slot, text, visible_in_chat,
                sequence_index, revision, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)''',
            (message_id, turn_id, conversation_id, role, message_type, slot, text,
             1 if visible else 0, _sequence_for(turn_index, offset), source, now, now),
        )
        return _public_message(
            conn.execute('SELECT * FROM conversation_messages WHERE id = ?', (message_id,)).fetchone(),
            None,
        )
    if not force and not _should_replace(existing['text'], text):
        return None
    if force and existing['text'] == text:
        return None
    conn.execute(
        'UPDATE conversation_messages SET text = ?, revision = revision + 1, updated_at = ? WHERE id = ?',
        (text, now, existing['id']),
    )
    return _public_message(
        conn.execute('SELECT * FROM conversation_messages WHERE id = ?', (existing['id'],)).fetchone(),
        None,
    )


def _should_replace(stored: str, incoming: str) -> bool:
    """Whether a re-read of the same turn is genuinely newer content.

    Terminal scrollback drops the head of a long reply, so an incoming read that
    is already contained in the stored text is an older, shorter view of the same
    message rather than a correction.
    """
    if stored == incoming:
        return False
    return incoming not in stored


def record_prompt(
    user_id: str, target: str, client_message_id: str, text: str, *, source: str = 'text',
    submitted_text: Optional[str] = None,
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
            conn.execute(
                '''INSERT INTO conversation_turns
                   (id, conversation_id, client_message_id, prompt_key, status, sequence_index, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'awaiting_reply', ?, ?, ?)''',
                (turn_id, conversation_id, client_message_id,
                 prompt_match_key(submitted_text if submitted_text is not None else text),
                 turn_index, now, now),
            )
        else:
            turn_id, turn_index = turn['id'], turn['sequence_index']
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
        )
        _touch_conversation(conn, conversation_id)
        return {
            'conversation_id': conversation_id, 'turn_id': turn_id, 'created': created,
            'messages': _turn_messages(conn, turn_id, client_message_id),
        }


def record_primary_reply(
    user_id: str, target: str, turn_id: str, text: str, *, source: str = 'text',
) -> List[Dict[str, Any]]:
    """Record a reply the provider returned synchronously for a known turn."""
    text = (text or '').strip()
    if not text:
        return []
    with _session() as conn:
        turn = conn.execute('SELECT * FROM conversation_turns WHERE id = ?', (turn_id,)).fetchone()
        # Stop/failure freezes the turn. The prompt request can finish after a
        # concurrent cancel (aborting the client's HTTP request does not abort
        # provider work), and that late synchronous result must not resurrect
        # the turn or appear beside the stopped partial response.
        if turn is None or turn['status'] in ('cancelled', 'failed'):
            return []
        changed = _upsert_message(
            conn, conversation_id=turn['conversation_id'], turn_id=turn_id,
            turn_index=turn['sequence_index'], slot=_PRIMARY_SLOT, offset=_PRIMARY_OFFSET,
            role='assistant', message_type='conversation', text=text[:MAX_PRIMARY_TEXT],
            visible=True, source=source, force=True,
        )
        if changed:
            _set_turn_status(conn, turn_id, 'answered')
            _touch_conversation(conn, turn['conversation_id'])
        return [changed] if changed else []


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
    """One prompt row from the snapshot with the agent activity that follows it."""

    __slots__ = ('prompt', 'prose', 'tools', 'internal')

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.prose: List[str] = []
        self.tools: List[str] = []
        self.internal: List[str] = []

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
    for row in rows:
        role, kind = row.get('role'), row.get('kind')
        text = (row.get('text') or '').strip()
        if not text:
            continue
        if kind == 'control':
            if role == 'user':
                current = None
            elif current is not None:
                current.internal.append(text)
            continue
        if role == 'user':
            current = _Segment(text)
            segments.append(current)
            continue
        if current is None:
            continue
        if kind == 'tool':
            current.tools.append(text)
        else:
            current.prose.append(text)
    return segments


def join_primary_text(blocks: List[str]) -> str:
    """One primary reply per turn, built from the turn's prose blocks in order.

    A harness interleaves prose with tool activity, so a turn legitimately has
    several prose blocks. They are one reply, not several messages: joining them
    keeps every word the agent said while still rendering exactly one assistant
    bubble. The result is bounded from the end, because the newest prose is the
    part a reader needs.
    """
    unique: List[str] = []
    for block in blocks:
        if not unique or unique[-1] != block:
            unique.append(block)
    kept: List[str] = []
    total = 0
    for block in reversed(unique):
        cost = len(block) + (2 if kept else 0)
        if kept and total + cost > MAX_PRIMARY_TEXT:
            break
        kept.append(block)
        total += cost
    kept.reverse()
    return '\n\n'.join(kept)[:MAX_PRIMARY_TEXT]


def _match_segments_to_turns(
    turns: List[sqlite3.Row], segments: List[_Segment],
) -> List[Tuple[sqlite3.Row, _Segment]]:
    """Pair each snapshot segment with the turn that produced it.

    Both lists are in conversation order, so matching newest-first is what keeps
    an older repeated phrase from stealing the newest turn's reply and still
    works when the retained snapshot has scrolled past the older turns.
    """
    matched: List[Tuple[sqlite3.Row, _Segment]] = []
    used: set[str] = set()
    keys = {
        turn['id']: prompt_match_key(turn['prompt_key'] or turn['prompt_text'] or '')
        for turn in turns
    }
    for segment in reversed(segments):
        if not segment.has_activity:
            continue
        for turn in reversed(turns):
            if turn['id'] in used:
                continue
            if keys[turn['id']] and keys[turn['id']] == prompt_match_key(segment.prompt):
                used.add(turn['id'])
                matched.append((turn, segment))
                break
    matched.reverse()
    return matched


def _recent_turns(conn: sqlite3.Connection, conversation_id: str) -> List[sqlite3.Row]:
    """Turns the adapter may still write to.

    A cancelled turn is excluded: the captain stopped that response, so output
    the harness produces afterwards must not appear as its reply. A failed turn
    never reached the provider at all.
    """
    rows = conn.execute(
        '''SELECT t.id AS id, t.sequence_index AS sequence_index, t.status AS status,
                  t.client_message_id AS client_message_id, t.prompt_key AS prompt_key,
                  m.text AS prompt_text
           FROM conversation_turns t
           LEFT JOIN conversation_messages m ON m.turn_id = t.id AND m.slot = ?
           WHERE t.conversation_id = ? AND t.status NOT IN ('cancelled', 'failed')
           ORDER BY t.sequence_index DESC LIMIT ?''',
        (_PROMPT_SLOT, conversation_id, TURN_MATCH_WINDOW),
    ).fetchall()
    return list(reversed(rows))


def ingest_terminal_rows(
    user_id: str, target: str, rows: Iterable[Dict[str, str]],
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
        turns = _recent_turns(conn, conversation_id)
        if not turns:
            return []
        for turn, segment in _match_segments_to_turns(turns, build_segments(rows)):
            changed.extend(_apply_segment(conn, conversation_id, turn, segment))
        if changed:
            _touch_conversation(conn, conversation_id)
    return changed


def _apply_segment(
    conn: sqlite3.Connection, conversation_id: str, turn: sqlite3.Row, segment: _Segment,
) -> List[Dict[str, Any]]:
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
        )
        if reply:
            changed.append(reply)
        if turn['status'] == 'awaiting_reply':
            _set_turn_status(conn, turn['id'], 'answered')
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
        conn.execute('DELETE FROM conversation_messages WHERE conversation_id = ?', (conversation_id,))
        conn.execute('DELETE FROM conversation_turns WHERE conversation_id = ?', (conversation_id,))
        _touch_conversation(conn, conversation_id)
    return {'status': 'reset', 'target': target, 'conversation_id': conversation_id}
