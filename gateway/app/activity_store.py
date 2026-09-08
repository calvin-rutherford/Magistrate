"""Tenant-owned canonical activity and source-event persistence.

Structured source snapshots reconcile current facts; immutable source journals
supply history.  Neither path accepts terminal output.  Every read and write is
qualified by the authenticated principal supplied by the route layer.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import unquote

from app import db

ACTIVITY_SCHEMA = 'activity.v1'
SOURCE_EVENT_SCHEMA = 'magistrate.source-event.v1'
ACTIVITY_STATES = frozenset({'active', 'awaiting-user', 'completed', 'failed', 'cancelled', 'resolved'})
SOURCE_EVENT_KINDS = frozenset({
    'supervision.outcome',
    'primary.message', 'primary.final', 'worker.message', 'worker.final',
})
ACTIVITY_KINDS = frozenset({
    'objective.started',
    'objective.progress',
    'decision.requested',
    'decision.resolved',
    'objective.completed',
    'objective.failed',
    'objective.cancelled',
    'supervision.outcome',
    'primary.message', 'primary.final', 'worker.message', 'worker.final',
})
ACTIVITY_IMPORTANCE = frozenset({'routine', 'attention'})
_ACTIVITY_KIND_STATES = {
    'objective.started': frozenset({'active'}),
    'objective.progress': frozenset({'active', 'awaiting-user'}),
    'decision.requested': frozenset({'awaiting-user'}),
    'decision.resolved': frozenset({'resolved'}),
    'objective.completed': frozenset({'completed'}),
    'objective.failed': frozenset({'failed'}),
    'objective.cancelled': frozenset({'cancelled'}),
    'supervision.outcome': frozenset({'completed'}),
    'primary.message': frozenset({'completed'}),
    'primary.final': frozenset({'completed'}),
    'worker.message': frozenset({'completed'}),
    'worker.final': frozenset({'completed'}),
}
MAX_ACTIVITY_PAGE = 200
MAX_ACTIVITY_FOCUS_RECORDS = 5_000
MAX_ACTIVITY_SUMMARY_CHARS = 600
MAX_ACTIVITY_TITLE_CHARS = 240
MAX_SOURCE_PAYLOAD_BYTES = 8 * 1024
MAX_SOURCE_ERROR_CHARS = 240
MAX_SNAPSHOT_ACTIVITY_RECORDS = 5_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_BUSY_TIMEOUT_SECONDS = 5.0
_SAFE_HASH = re.compile(r'^[0-9a-f]{64}$')
_GITHUB_PR_URL = re.compile(
    r'^https://github\.com/([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9-]{0,37}[A-Za-z0-9])'
    r'/([A-Za-z0-9._-]{1,100})/pull/([1-9][0-9]*)$'
)
_GITLAB_MR_URL = re.compile(
    r'^https://([a-z0-9.-]{1,253})/([A-Za-z0-9._/-]+)/-/merge_requests/([1-9][0-9]*)$'
)
_ENV_ASSIGNMENT = re.compile(
    r'(?i)(?<![A-Za-z0-9_])(?:export\s+)?[A-Za-z_][A-Za-z0-9_]{0,127}\s*(?:\+\s*)?='
)
_SENSITIVE_TEXT = re.compile(
    r'''(?ix)(?:
      (?:proxy[-_ ]?)?authorization\s*["']?\s*[:=]\s*[^\s,;}]+(?:\s+[^\s,;}]+)?
      |["']?[A-Za-z0-9_. -]{0,96}(?:secret|pass(?:word|wd)?|pwd|token|auth|key|credential)
       [A-Za-z0-9_. -]{0,96}["']?\s*[:=]\s*["']?[^\s,;}"']+
      |\b[A-Z][A-Z0-9_]{1,63}\s*=\s*[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@
      |\b[a-z][a-z0-9+.-]{0,31}://[^\s/@]+@[^\s,;]+
      |-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{8,}|\bsk-[A-Za-z0-9]{8,}
      |\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b
    )'''
)


def _has_controls(value: str) -> bool:
    return any(
        unicodedata.category(character).startswith('C')
        or unicodedata.category(character) in {'Zl', 'Zp'}
        for character in value
    )


def _contains_sensitive_text(value: str) -> bool:
    decoded = value
    for _ in range(3):
        if _ENV_ASSIGNMENT.search(decoded) or _SENSITIVE_TEXT.search(decoded):
            return True
        expanded = unquote(decoded)
        if expanded == decoded:
            return False
        decoded = expanded
    return bool(_ENV_ASSIGNMENT.search(decoded) or _SENSITIVE_TEXT.search(decoded))


class SourceEventConflict(ValueError):
    """An immutable source identity changed, reordered, or became invalid."""


class SourceUnavailable(RuntimeError):
    """A structured source could not be observed safely."""


def _now() -> int:
    return int(time.time() * 1000)


@contextmanager
def _session(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db.DB_PATH, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            if immediate:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
    finally:
        conn.close()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
        allow_nan=False,
    )


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _bounded_text(value: Any, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise ValueError('A bounded text value is required.')
        return ''
    # Source summaries are inert text. Controls and overlong values fail closed
    # rather than being normalized into a different semantic record.
    if _has_controls(value) or len(value) > maximum:
        raise ValueError('A bounded text value is required.')
    text = value.strip()
    if len(text) > maximum:
        raise ValueError('A bounded text value is required.')
    if required and not text:
        raise ValueError('A bounded text value is required.')
    return text


def _validate_hash(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_HASH.fullmatch(value):
        raise ValueError('A canonical SHA-256 payload hash is required.')
    return value


def safe_pull_request_url(value: Any) -> Optional[str]:
    """Return one existing canonical Firstmate forge PR/MR URL or ``None``."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    github = _GITHUB_PR_URL.fullmatch(value)
    if github:
        owner, repository, _ = github.groups()
        if '--' not in owner and repository not in {'.', '..'}:
            return value
        return None
    gitlab = _GITLAB_MR_URL.fullmatch(value)
    if not gitlab:
        return None
    host, path, _ = gitlab.groups()
    if host == 'github.com' or host.startswith('.') or host.endswith('.') or '..' in host:
        return None
    labels = host.split('.')
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in labels):
        return None
    if len(path) < 3 or len(path) > 1024:
        return None
    segments = path.split('/')
    if len(segments) < 2 or len(segments) > 20:
        return None
    if any(
        not segment or len(segment) > 255 or segment in {'.', '..'}
        or segment.startswith('-') or segment.endswith('.git') or segment.endswith('.atom')
        for segment in segments
    ):
        return None
    return value


def _record_id(user_id: str, source_instance_id: str, record_key: str) -> str:
    digest = hashlib.sha256(f'{user_id}\0{source_instance_id}\0{record_key}'.encode('utf-8')).hexdigest()
    return 'ca_' + digest[:24]


def _source_row_id(user_id: str, source_instance_id: str, stream_name: str, source_event_id: str) -> str:
    digest = hashlib.sha256(
        f'{user_id}\0{source_instance_id}\0{stream_name}\0{source_event_id}'.encode('utf-8')
    ).hexdigest()
    return 'se_' + digest[:24]


def _decode_refs(value: str) -> List[Dict[str, str]]:
    try:
        refs = json.loads(value or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(refs, list):
        return []
    public: List[Dict[str, str]] = []
    for raw in refs[:8]:
        if not isinstance(raw, dict) or raw.get('kind') not in {'pull-request', 'report'}:
            continue
        kind = raw['kind']
        if kind == 'pull-request':
            url = safe_pull_request_url(raw.get('url'))
            if url:
                public.append({'kind': kind, 'url': url})
        else:
            reference = raw.get('id')
            if isinstance(reference, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', reference):
                public.append({'kind': kind, 'id': reference})
    return public


def _public_activity(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        'id': row['id'],
        'sequence': row['sequence_index'],
        'revision': row['revision'],
        'kind': row['kind'],
        'state': row['state'],
        'importance': row['importance'],
        'title': row['title'],
        'summary': row['summary'],
        'summary_truncated': bool(row['summary_truncated']),
        'task_id': row['task_id'],
        'decision_key': row['decision_key'],
        'objective_id': row['objective_id'],
        'run_id': row['run_id'],
        'project': row['project'],
        'occurred_at': row['occurred_at'],
        'observed_at': row['observed_at'],
        'refs': _decode_refs(row['refs_json']),
        'source': {
            'instance_id': row['source_instance_id'],
            'event_id': row['source_event_id'],
        },
    }


def _next_sequence(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        'SELECT MAX(sequence_index) AS top FROM activity_records WHERE user_id = ?', (user_id,),
    ).fetchone()
    return int(row['top']) + 1 if row and row['top'] is not None else 1


def _record_activity_change(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    latest = conn.execute(
        'SELECT MAX(change_sequence) AS top FROM activity_changes WHERE user_id = ?',
        (row['user_id'],),
    ).fetchone()['top']
    sequence = int(latest or 0) + 1
    conn.execute(
        '''INSERT INTO activity_changes
           (user_id, change_sequence, record_id, record_revision, changed_at)
           VALUES (?, ?, ?, ?, ?)''',
        (row['user_id'], sequence, row['id'], row['revision'], _now()),
    )
    return sequence


def _validated_refs(refs: Optional[List[Dict[str, str]]]) -> str:
    if refs is None:
        candidate: List[Dict[str, str]] = []
    elif isinstance(refs, list) and len(refs) <= 8:
        candidate = refs
    else:
        raise ValueError('Activity references are not in the closed allowlist.')
    encoded = canonical_json(candidate)
    # Decode through the public allowlist and require exact semantic equality.
    normalized = _decode_refs(encoded)
    if normalized != candidate:
        raise ValueError('Activity references are not in the closed allowlist.')
    return canonical_json(normalized)


def _upsert_activity(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    source_instance_id: str,
    record_key: str,
    kind: str,
    state: str,
    importance: str,
    title: str,
    summary: str,
    source_payload_sha256: str,
    summary_truncated: bool = False,
    source_event_id: Optional[str] = None,
    task_id: Optional[str] = None,
    decision_key: Optional[str] = None,
    objective_id: Optional[str] = None,
    run_id: Optional[str] = None,
    project: Optional[str] = None,
    occurred_at: Optional[int] = None,
    observed_at: Optional[int] = None,
    refs: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    if (
        not isinstance(user_id, str) or not user_id
        or len(user_id) > 128 or _has_controls(user_id)
    ):
        raise ValueError('A bounded tenant identity is required.')
    if (
        not isinstance(source_instance_id, str) or not source_instance_id
        or len(source_instance_id) > 128 or _has_controls(source_instance_id)
    ):
        raise ValueError('A bounded source instance is required.')
    if (
        not isinstance(record_key, str) or not record_key
        or len(record_key) > 300 or _has_controls(record_key)
    ):
        raise ValueError('A bounded activity identity is required.')
    if (
        kind not in ACTIVITY_KINDS or state not in ACTIVITY_STATES
        or importance not in ACTIVITY_IMPORTANCE
        or state not in _ACTIVITY_KIND_STATES.get(kind, frozenset())
    ):
        raise ValueError('Unknown activity kind, state, or importance.')
    title = _bounded_text(title, MAX_ACTIVITY_TITLE_CHARS, required=True)
    summary = _bounded_text(summary, MAX_ACTIVITY_SUMMARY_CHARS, required=True)
    if type(summary_truncated) is not bool:
        raise ValueError('Activity summary_truncated must be boolean.')
    task_id = _bounded_text(task_id, 200) or None
    decision_key = _bounded_text(decision_key, 128) or None
    objective_id = _bounded_text(objective_id, 128) or None
    run_id = _bounded_text(run_id, 128) or None
    project = _bounded_text(project, 160) or None
    decision_kind = kind in {'decision.requested', 'decision.resolved'}
    if (
        not objective_id
        or (decision_kind and (not task_id or not decision_key))
        or (not decision_kind and decision_key is not None)
        or (kind.startswith('primary.') and task_id is not None)
        or (not kind.startswith('primary.') and task_id is None)
    ):
        raise ValueError('Activity causal identity does not match its semantic kind.')
    if source_event_id is not None:
        source_event_id = _bounded_text(source_event_id, 200, required=True)
    if any(_contains_sensitive_text(value) for value in (
        title, summary, task_id or '', decision_key or '', objective_id or '', run_id or '',
        project or '', source_instance_id, source_event_id or '',
    )):
        raise ValueError('Activity public fields resemble credential material.')
    if occurred_at is not None and (
        not isinstance(occurred_at, int) or isinstance(occurred_at, bool)
        or occurred_at < 0 or occurred_at > MAX_SAFE_INTEGER
    ):
        raise ValueError('Activity occurred_at must be epoch milliseconds or null.')
    observed_at = observed_at if observed_at is not None else _now()
    if (
        not isinstance(observed_at, int) or isinstance(observed_at, bool)
        or observed_at < 0 or observed_at > MAX_SAFE_INTEGER
    ):
        raise ValueError('Activity observed_at must be epoch milliseconds.')
    source_payload_sha256 = _validate_hash(source_payload_sha256)
    refs_json = _validated_refs(refs)
    existing = conn.execute(
        '''SELECT * FROM activity_records
           WHERE user_id = ? AND source_instance_id = ? AND record_key = ?''',
        (user_id, source_instance_id, record_key),
    ).fetchone()
    comparable = (
        kind, state, importance, title, summary, summary_truncated, task_id, decision_key,
        objective_id, run_id, project, occurred_at, refs_json,
        source_payload_sha256, source_event_id,
    )
    if existing is not None:
        prior = (
            existing['kind'], existing['state'], existing['importance'], existing['title'],
            existing['summary'], bool(existing['summary_truncated']), existing['task_id'], existing['decision_key'],
            existing['objective_id'], existing['run_id'], existing['project'],
            existing['occurred_at'], existing['refs_json'], existing['source_payload_sha256'],
            existing['source_event_id'],
        )
        if prior == comparable:
            return _public_activity(existing)
        now = _now()
        conn.execute(
            '''UPDATE activity_records
               SET source_event_id = ?, revision = revision + 1, kind = ?, state = ?,
                   importance = ?, title = ?, summary = ?, summary_truncated = ?, task_id = ?, decision_key = ?,
                   objective_id = ?, run_id = ?, project = ?, occurred_at = ?,
                   observed_at = ?, refs_json = ?,
                   source_payload_sha256 = ?, updated_at = ?
               WHERE id = ?''',
            (source_event_id, kind, state, importance, title, summary, 1 if summary_truncated else 0, task_id,
             decision_key, objective_id, run_id, project, occurred_at, observed_at, refs_json,
             source_payload_sha256, now, existing['id']),
        )
        updated = conn.execute(
            'SELECT * FROM activity_records WHERE id = ?', (existing['id'],),
        ).fetchone()
        _record_activity_change(conn, updated)
        return _public_activity(updated)

    now = _now()
    record_id = _record_id(user_id, source_instance_id, record_key)
    conn.execute(
        '''INSERT INTO activity_records
           (id, user_id, source_instance_id, source_event_id, record_key,
            sequence_index, revision, kind, state, importance, title, summary,
            summary_truncated, task_id, decision_key, objective_id, run_id, project, occurred_at,
            observed_at, refs_json, source_payload_sha256, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (record_id, user_id, source_instance_id, source_event_id, record_key,
         _next_sequence(conn, user_id), kind, state, importance, title, summary,
         1 if summary_truncated else 0, task_id, decision_key, objective_id, run_id, project, occurred_at,
         observed_at, refs_json, source_payload_sha256, now, now),
    )
    inserted = conn.execute(
        'SELECT * FROM activity_records WHERE id = ?', (record_id,),
    ).fetchone()
    _record_activity_change(conn, inserted)
    return _public_activity(inserted)


def get_source_state(user_id: str, source_instance_id: str, stream_name: str) -> Optional[Dict[str, Any]]:
    with _session() as conn:
        row = conn.execute(
            '''SELECT * FROM activity_sources
               WHERE user_id = ? AND source_instance_id = ? AND stream_name = ?''',
            (user_id, source_instance_id, stream_name),
        ).fetchone()
    return dict(row) if row else None


def _validate_source_identity(user_id: object, source_instance_id: object, stream_name: object) -> None:
    if (
        not isinstance(user_id, str) or not user_id or len(user_id) > 128
        or _has_controls(user_id)
        or not isinstance(source_instance_id, str) or not source_instance_id
        or len(source_instance_id) > 128 or _has_controls(source_instance_id)
        or _contains_sensitive_text(source_instance_id)
        or not isinstance(stream_name, str) or not stream_name
        or len(stream_name) > 128 or _has_controls(stream_name)
        or _contains_sensitive_text(stream_name)
    ):
        raise ValueError('Activity source identity is invalid.')


def initialize_source(
    user_id: str,
    source_instance_id: str,
    stream_name: str,
    *,
    bootstrap_policy: str,
    cursor: int,
    prefix_sha256: str,
    source_tail: int,
) -> Dict[str, Any]:
    _validate_source_identity(user_id, source_instance_id, stream_name)
    if (
        bootstrap_policy not in {'tail', 'from-start'}
        and not re.fullmatch(r'after:(?:0|[1-9][0-9]{0,15})', bootstrap_policy)
    ):
        raise ValueError('Unknown activity bootstrap policy.')
    if (
        type(cursor) is not int or type(source_tail) is not int
        or min(cursor, source_tail) < 0 or cursor > source_tail
        or source_tail > MAX_SAFE_INTEGER
        or (bootstrap_policy == 'from-start' and cursor != 0)
        or (bootstrap_policy == 'tail' and cursor != source_tail)
        or (bootstrap_policy.startswith('after:') and int(bootstrap_policy[6:]) != cursor)
    ):
        raise ValueError('Invalid activity source cursor.')
    if (cursor and not _SAFE_HASH.fullmatch(prefix_sha256)) or (not cursor and prefix_sha256 != ''):
        raise ValueError('A bootstrapped cursor requires its validated prefix hash.')
    with _session(immediate=True) as conn:
        conn.execute(
            '''INSERT INTO activity_sources
               (user_id, source_instance_id, stream_name, bootstrap_policy, cursor,
                prefix_sha256, source_tail, state, last_reconciled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'available', ?)
               ON CONFLICT(user_id, source_instance_id, stream_name) DO UPDATE SET
                 bootstrap_policy = excluded.bootstrap_policy,
                 cursor = excluded.cursor,
                 prefix_sha256 = excluded.prefix_sha256,
                 source_tail = excluded.source_tail,
                 state = 'available',
                 last_error_code = NULL,
                 last_error_detail = NULL,
                 last_reconciled_at = excluded.last_reconciled_at
               WHERE activity_sources.state = 'fault'
                 AND activity_sources.cursor = 0
                 AND activity_sources.prefix_sha256 = ''
                 AND activity_sources.source_tail = 0
                 AND activity_sources.accepted_count = 0''',
            (user_id, source_instance_id, stream_name, bootstrap_policy, cursor,
             prefix_sha256, source_tail, _now()),
        )
    state = get_source_state(user_id, source_instance_id, stream_name)
    if state is None:
        raise RuntimeError('Activity source initialization failed.')
    return state


def apply_source_event_batch(
    user_id: str,
    source_instance_id: str,
    stream_name: str,
    *,
    expected_cursor: int,
    events: Iterable[Dict[str, Any]],
    new_cursor: int,
    new_prefix_sha256: str,
    source_tail: int,
) -> List[Dict[str, Any]]:
    """Persist journal events, projections, and cursor in one transaction."""
    _validate_source_identity(user_id, source_instance_id, stream_name)
    changed: List[Dict[str, Any]] = []
    event_list = list(events)
    if (
        type(expected_cursor) is not int or type(new_cursor) is not int
        or type(source_tail) is not int or min(expected_cursor, new_cursor, source_tail) < 0
        or new_cursor < expected_cursor or new_cursor > source_tail
        or source_tail > MAX_SAFE_INTEGER
        or len(event_list) != new_cursor - expected_cursor
    ):
        raise SourceEventConflict('The source cursor cannot move outside the validated journal.')
    if (
        (new_cursor and not _SAFE_HASH.fullmatch(new_prefix_sha256))
        or (not new_cursor and new_prefix_sha256 != '')
    ):
        raise SourceEventConflict('The source prefix hash is invalid.')
    with _session(immediate=True) as conn:
        source = conn.execute(
            '''SELECT * FROM activity_sources
               WHERE user_id = ? AND source_instance_id = ? AND stream_name = ?''',
            (user_id, source_instance_id, stream_name),
        ).fetchone()
        if source is None:
            raise SourceEventConflict('The activity source was not initialized.')
        if source['cursor'] != expected_cursor:
            raise SourceEventConflict('The activity source cursor changed concurrently.')
        accepted = 0
        duplicates = 0
        for event_offset, item in enumerate(event_list, start=1):
            if not isinstance(item, dict):
                raise SourceEventConflict('A source event must be a validated object.')
            source_event_id = _bounded_text(item.get('source_event_id'), 200, required=True)
            source_cursor = item.get('source_cursor')
            source_hash = _validate_hash(item.get('payload_sha256'))
            if (
                type(source_cursor) is not int
                or source_cursor != expected_cursor + event_offset
                or source_cursor > source_tail
            ):
                raise SourceEventConflict('A source event cursor must be contiguous and positive.')
            audience = item.get('audience')
            event_kind = item.get('event_kind')
            if audience != 'captain' or event_kind not in SOURCE_EVENT_KINDS:
                raise SourceEventConflict('Unknown source audience or event kind.')
            occurred_at = item.get('occurred_at')
            if occurred_at is not None and (
                type(occurred_at) is not int or occurred_at < 0 or occurred_at > MAX_SAFE_INTEGER
            ):
                raise SourceEventConflict('A source event timestamp is invalid.')
            public_payload = item.get('payload')
            if not isinstance(public_payload, dict):
                raise SourceEventConflict('A source event payload must be a validated object.')
            encoded_payload = canonical_json(public_payload)
            if len(encoded_payload.encode('utf-8')) > MAX_SOURCE_PAYLOAD_BYTES:
                raise SourceEventConflict('The normalized source event is too large.')
            duplicate = conn.execute(
                '''SELECT * FROM canonical_source_events
                   WHERE user_id = ? AND source_instance_id = ? AND stream_name = ?
                     AND source_event_id = ?''',
                (user_id, source_instance_id, stream_name, source_event_id),
            ).fetchone()
            if duplicate is not None:
                if duplicate['source_cursor'] != source_cursor or duplicate['payload_sha256'] != source_hash:
                    raise SourceEventConflict('A source event identity was reused with different content.')
                duplicates += 1
                continue
            cursor_duplicate = conn.execute(
                '''SELECT source_event_id, payload_sha256 FROM canonical_source_events
                   WHERE user_id = ? AND source_instance_id = ? AND stream_name = ?
                     AND source_cursor = ?''',
                (user_id, source_instance_id, stream_name, source_cursor),
            ).fetchone()
            if cursor_duplicate is not None:
                raise SourceEventConflict('A source cursor was reused by another event.')
            conn.execute(
                '''INSERT INTO canonical_source_events
                   (id, user_id, source_instance_id, stream_name, source_event_id,
                    source_cursor, payload_sha256, event_kind, audience, occurred_at,
                    payload_json, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (_source_row_id(user_id, source_instance_id, stream_name, source_event_id),
                 user_id, source_instance_id, stream_name, source_event_id, source_cursor,
                 source_hash, event_kind, audience, occurred_at,
                 encoded_payload, _now()),
            )
            accepted += 1
            activity = item.get('activity')
            if activity is not None:
                projected = _upsert_activity(
                    conn,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    source_event_id=source_event_id,
                    source_payload_sha256=source_hash,
                    **activity,
                )
                changed.append(projected)
        conn.execute(
            '''UPDATE activity_sources
               SET cursor = ?, prefix_sha256 = ?, source_tail = ?, state = 'available',
                   last_error_code = NULL, last_error_detail = NULL,
                   last_reconciled_at = ?, accepted_count = accepted_count + ?,
                   duplicate_count = duplicate_count + ?
               WHERE user_id = ? AND source_instance_id = ? AND stream_name = ?''',
            (new_cursor, new_prefix_sha256, source_tail, _now(), accepted, duplicates,
             user_id, source_instance_id, stream_name),
        )
    return changed


def reconcile_snapshot(
    user_id: str,
    source_instance_id: str,
    *,
    observed_at: int,
    snapshot_sha256: str,
    records: Iterable[Dict[str, Any]],
    open_decision_keys: Iterable[str],
) -> List[Dict[str, Any]]:
    """Upsert allowlisted current facts and close only previously open decisions."""
    if not isinstance(user_id, str) or not user_id or len(user_id) > 128 or _has_controls(user_id):
        raise ValueError('A bounded tenant identity is required.')
    if (
        not isinstance(source_instance_id, str) or not source_instance_id
        or len(source_instance_id) > 128 or _has_controls(source_instance_id)
    ):
        raise ValueError('A bounded source instance is required.')
    if (
        type(observed_at) is not int or observed_at < 0
        or observed_at > MAX_SAFE_INTEGER
    ):
        raise ValueError('Activity observed_at must be epoch milliseconds.')
    _validate_hash(snapshot_sha256)
    candidates = list(records)
    raw_open_keys = list(open_decision_keys)
    if (
        len(candidates) > MAX_SNAPSHOT_ACTIVITY_RECORDS
        or len(raw_open_keys) > MAX_SNAPSHOT_ACTIVITY_RECORDS
        or any(not isinstance(candidate, dict) for candidate in candidates)
    ):
        raise ValueError('The activity snapshot exceeds its bounded projection.')
    candidate_keys = [candidate.get('record_key') for candidate in candidates]
    if (
        any(not isinstance(key, str) or not key for key in candidate_keys)
        or len(set(candidate_keys)) != len(candidate_keys)
    ):
        raise ValueError('The activity snapshot repeats a record identity.')
    def valid_decision_identity(identity: object) -> bool:
        if not isinstance(identity, str) or identity.count('\0') != 1:
            return False
        task_id, decision_key = identity.split('\0', 1)
        return (
            0 < len(task_id) <= 200 and 0 < len(decision_key) <= 128
            and not _has_controls(task_id) and not _has_controls(decision_key)
        )

    if (
        any(not valid_decision_identity(key) for key in raw_open_keys)
        or len(set(raw_open_keys)) != len(raw_open_keys)
    ):
        raise ValueError('Snapshot decision identities are invalid.')
    open_keys = set(raw_open_keys)
    changed: List[Dict[str, Any]] = []
    with _session(immediate=True) as conn:
        source = conn.execute(
            '''SELECT * FROM activity_sources
               WHERE user_id = ? AND source_instance_id = ? AND stream_name = 'fleet-snapshot' ''',
            (user_id, source_instance_id),
        ).fetchone()
        if source is None:
            conn.execute(
                '''INSERT INTO activity_sources
                   (user_id, source_instance_id, stream_name, bootstrap_policy, state,
                    last_reconciled_at, last_snapshot_at, snapshot_sha256)
                   VALUES (?, ?, 'fleet-snapshot', 'snapshot-current', 'available', ?, ?, ?)''',
                (user_id, source_instance_id, _now(), observed_at, snapshot_sha256),
            )
        else:
            previous_observation = source['last_snapshot_at']
            if previous_observation is not None and observed_at < previous_observation:
                # A delayed subprocess result cannot regress a newer durable
                # projection or resolve a decision that was still open then.
                return []
            if (
                previous_observation is not None and observed_at == previous_observation
                and source['snapshot_sha256'] and source['snapshot_sha256'] != snapshot_sha256
            ):
                raise SourceEventConflict('A Firstmate snapshot timestamp was reused with different content.')
        for candidate in candidates:
            before = conn.execute(
                '''SELECT revision, source_payload_sha256 FROM activity_records
                   WHERE user_id = ? AND source_instance_id = ? AND record_key = ?''',
                (user_id, source_instance_id, candidate['record_key']),
            ).fetchone()
            projected = _upsert_activity(
                conn,
                user_id=user_id,
                source_instance_id=source_instance_id,
                observed_at=observed_at,
                **candidate,
            )
            if before is None or projected['revision'] != before['revision']:
                changed.append(projected)

        # A disappearance is meaningful only for an exact keyed decision in a
        # successfully validated complete local snapshot. It says "closed",
        # not approved/rejected; action authority remains in attention_actions.
        decision_rows = conn.execute(
            '''SELECT * FROM activity_records
               WHERE user_id = ? AND source_instance_id = ?
                 AND kind = 'decision.requested' AND state = 'awaiting-user' ''',
            (user_id, source_instance_id),
        ).fetchall()
        for row in decision_rows:
            identity = f"{row['task_id']}\0{row['decision_key']}"
            if identity in open_keys:
                continue
            resolution_hash = payload_sha256({
                'kind': 'decision.resolved', 'task_id': row['task_id'],
                'decision_key': row['decision_key'], 'snapshot': snapshot_sha256,
            })
            resolved = _upsert_activity(
                conn,
                user_id=user_id,
                source_instance_id=source_instance_id,
                record_key=row['record_key'],
                kind='decision.resolved',
                state='resolved',
                importance='routine',
                title=row['title'],
                summary='Decision is no longer open in the authoritative Firstmate snapshot.',
                source_payload_sha256=resolution_hash,
                task_id=row['task_id'],
                decision_key=row['decision_key'],
                objective_id=row['objective_id'],
                run_id=row['run_id'],
                project=row['project'],
                occurred_at=None,
                observed_at=observed_at,
                refs=_decode_refs(row['refs_json']),
            )
            changed.append(resolved)
        conn.execute(
            '''UPDATE activity_sources
               SET state = 'available', last_error_code = NULL, last_error_detail = NULL,
                   last_reconciled_at = ?, last_snapshot_at = ?, snapshot_sha256 = ?
               WHERE user_id = ? AND source_instance_id = ? AND stream_name = 'fleet-snapshot' ''',
            (_now(), observed_at, snapshot_sha256, user_id, source_instance_id),
        )
    return changed


def mark_source_fault(
    user_id: str,
    source_instance_id: str,
    stream_name: str,
    code: str,
    detail: str,
    *,
    bootstrap_policy: str = 'tail',
    conflict: bool = False,
) -> None:
    _validate_source_identity(user_id, source_instance_id, stream_name)
    if (
        bootstrap_policy not in {'tail', 'from-start', 'snapshot-current'}
        and not re.fullmatch(r'after:(?:0|[1-9][0-9]{0,15})', bootstrap_policy)
    ):
        raise ValueError('Unknown activity bootstrap policy.')
    code = _bounded_text(code, 64, required=True)
    detail = _bounded_text(detail, MAX_SOURCE_ERROR_CHARS, required=True)
    now = _now()
    with _session(immediate=True) as conn:
        conn.execute(
            '''INSERT INTO activity_sources
               (user_id, source_instance_id, stream_name, bootstrap_policy, state,
                last_error_code, last_error_detail, last_reconciled_at, conflict_count)
               VALUES (?, ?, ?, ?, 'fault', ?, ?, ?, ?)
               ON CONFLICT(user_id, source_instance_id, stream_name) DO UPDATE SET
                 state = 'fault', last_error_code = excluded.last_error_code,
                 last_error_detail = excluded.last_error_detail,
                 last_reconciled_at = excluded.last_reconciled_at,
                 conflict_count = activity_sources.conflict_count + excluded.conflict_count''',
            (user_id, source_instance_id, stream_name, bootstrap_policy, code, detail,
             now, 1 if conflict else 0),
        )


def _activity_summary(conn: sqlite3.Connection, user_id: str) -> Dict[str, int]:
    active_objective_count = int(conn.execute(
        '''SELECT COUNT(DISTINCT objective_id) FROM activity_records
           WHERE user_id = ? AND kind IN ('objective.started', 'objective.progress')
             AND state IN ('active', 'awaiting-user') AND objective_id IS NOT NULL''',
        (user_id,),
    ).fetchone()[0])
    operation_count = int(conn.execute(
        '''SELECT COUNT(*) FROM activity_records AS operation
           WHERE operation.user_id = ?
             AND operation.kind NOT LIKE 'objective.%'
             AND operation.kind NOT LIKE 'decision.%'
             AND EXISTS (
               SELECT 1 FROM activity_records AS objective
               WHERE objective.user_id = operation.user_id
                 AND objective.objective_id = operation.objective_id
                 AND objective.kind IN ('objective.started', 'objective.progress')
                 AND objective.state IN ('active', 'awaiting-user')
             )''',
        (user_id,),
    ).fetchone()[0])
    pending_decisions = int(conn.execute(
        '''SELECT COUNT(*) FROM activity_records
           WHERE user_id = ? AND kind = 'decision.requested' AND state = 'awaiting-user' ''',
        (user_id,),
    ).fetchone()[0])
    return {
        'active_objectives': active_objective_count,
        'operation_count': operation_count,
        'pending_decisions': pending_decisions,
    }


def snapshot_activity(
    user_id: str, *, before: Optional[int] = None, limit: int = 100,
) -> Dict[str, Any]:
    """Return a bounded current projection plus its durable replay cursor.

    Snapshot pagination uses the stable insertion sequence, while
    ``snapshot_cursor`` identifies the last activity change included by this
    SQLite read transaction.  Callers merge the projection monotonically and
    then replay after that cursor, so a response delayed behind realtime cannot
    roll a newer revision backwards.
    """
    if (
        not isinstance(user_id, str) or not user_id or len(user_id) > 128
        or _has_controls(user_id)
    ):
        raise ValueError('A bounded tenant identity is required.')
    if before is not None and (
        type(before) is not int or before < 1 or before > MAX_SAFE_INTEGER
    ):
        raise ValueError('Activity snapshot cursor is outside the supported range.')
    limit = max(1, min(limit, MAX_ACTIVITY_PAGE))
    with _session() as conn:
        # Python's sqlite wrapper does not start a transaction for SELECTs.
        # Pin every page/focus/summary/cursor read to one WAL snapshot.
        conn.execute('BEGIN')
        latest_sequence = int(conn.execute(
            'SELECT COALESCE(MAX(sequence_index), 0) FROM activity_records WHERE user_id = ?',
            (user_id,),
        ).fetchone()[0])
        snapshot_cursor = int(conn.execute(
            'SELECT COALESCE(MAX(change_sequence), 0) FROM activity_changes WHERE user_id = ?',
            (user_id,),
        ).fetchone()[0])
        page_before = before if before is not None else latest_sequence + 1
        rows = conn.execute(
            '''SELECT r.*,
                      (SELECT MAX(c.change_sequence) FROM activity_changes c
                       WHERE c.user_id = r.user_id AND c.record_id = r.id) AS delivery_sequence
               FROM activity_records r
               WHERE r.user_id = ? AND r.sequence_index < ?
               ORDER BY r.sequence_index DESC LIMIT ?''',
            (user_id, page_before, limit + 1),
        ).fetchall()
        page = rows[:limit]
        # Current non-terminal objectives and decisions must be recoverable even
        # when a long completed history pushes them outside the newest page.
        focus_rows = conn.execute(
            '''SELECT r.*,
                      (SELECT MAX(c.change_sequence) FROM activity_changes c
                       WHERE c.user_id = r.user_id AND c.record_id = r.id) AS delivery_sequence
               FROM activity_records r
               WHERE r.user_id = ? AND (
                    (r.kind IN ('objective.started', 'objective.progress')
                     AND r.state IN ('active', 'awaiting-user'))
                    OR (r.kind = 'decision.requested' AND r.state = 'awaiting-user')
               )
               ORDER BY r.sequence_index DESC LIMIT ?''',
            (user_id, MAX_ACTIVITY_FOCUS_RECORDS + 1),
        ).fetchall()
        summary = _activity_summary(conn, user_id)

    def deliver(row: sqlite3.Row) -> Dict[str, Any]:
        delivery_sequence = row['delivery_sequence']
        if delivery_sequence is None:
            raise RuntimeError('A canonical activity row has no durable change identity.')
        return {**_public_activity(row), 'delivery_sequence': int(delivery_sequence)}

    return {
        'schema_version': ACTIVITY_SCHEMA,
        'records': [deliver(row) for row in page],
        'focus_records': [deliver(row) for row in focus_rows[:MAX_ACTIVITY_FOCUS_RECORDS]],
        'focus_truncated': len(focus_rows) > MAX_ACTIVITY_FOCUS_RECORDS,
        'snapshot_cursor': snapshot_cursor,
        'latest_sequence': latest_sequence,
        'next_before': page[-1]['sequence_index'] if len(rows) > limit and page else None,
        'has_more': len(rows) > limit,
        'summary': summary,
    }


def list_activity(user_id: str, *, after: int = 0, limit: int = 100) -> Dict[str, Any]:
    """Replay insertions and in-place revisions after a durable change cursor."""
    if (
        not isinstance(user_id, str) or not user_id or len(user_id) > 128
        or _has_controls(user_id)
    ):
        raise ValueError('A bounded tenant identity is required.')
    if type(after) is not int or after < 0 or after > MAX_SAFE_INTEGER:
        raise ValueError('Activity cursor is outside the supported range.')
    limit = max(1, min(limit, MAX_ACTIVITY_PAGE))
    with _session() as conn:
        conn.execute('BEGIN')
        changes = conn.execute(
            '''SELECT change_sequence, record_id, record_revision FROM activity_changes
               WHERE user_id = ? AND change_sequence > ?
               ORDER BY change_sequence LIMIT ?''',
            (user_id, after, limit + 1),
        ).fetchall()
        latest = conn.execute(
            'SELECT MAX(change_sequence) AS top FROM activity_changes WHERE user_id = ?',
            (user_id,),
        ).fetchone()['top']
        latest_cursor = int(latest or 0)
        if after > latest_cursor:
            raise ValueError('Activity cursor is ahead of the durable change ledger.')
        page = changes[:limit]
        if any(
            change['change_sequence'] != after + offset
            for offset, change in enumerate(page, start=1)
        ):
            raise RuntimeError('The durable activity change ledger is not contiguous.')
        records: List[Dict[str, Any]] = []
        for change in page:
            row = conn.execute(
                'SELECT * FROM activity_records WHERE user_id = ? AND id = ?',
                (user_id, change['record_id']),
            ).fetchone()
            if row is None or row['revision'] < change['record_revision']:
                raise RuntimeError('The durable activity change ledger is inconsistent.')
            records.append({
                **_public_activity(row),
                'delivery_sequence': change['change_sequence'],
            })
        summary = _activity_summary(conn, user_id)
    has_more = len(changes) > limit
    next_cursor = page[-1]['change_sequence'] if page else after
    return {
        'schema_version': ACTIVITY_SCHEMA,
        'records': records,
        'next_cursor': next_cursor,
        'latest_cursor': latest_cursor,
        'has_more': has_more,
        'summary': summary,
    }


def recent_activity(user_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 50))
    with _session() as conn:
        rows = conn.execute(
            '''SELECT * FROM activity_records WHERE user_id = ?
               ORDER BY sequence_index DESC LIMIT ?''',
            (user_id, limit),
        ).fetchall()
    return [_public_activity(row) for row in rows]


def source_diagnostics(user_id: str) -> List[Dict[str, Any]]:
    with _session() as conn:
        rows = conn.execute(
            '''SELECT * FROM activity_sources WHERE user_id = ?
               ORDER BY source_instance_id, stream_name''',
            (user_id,),
        ).fetchall()
    return [{
        'source_instance_id': row['source_instance_id'],
        'stream': row['stream_name'],
        'bootstrap_policy': row['bootstrap_policy'],
        'state': row['state'],
        'cursor': row['cursor'],
        'source_tail': row['source_tail'],
        'lag': max(0, row['source_tail'] - row['cursor']),
        'last_reconciled_at': row['last_reconciled_at'],
        'last_snapshot_at': row['last_snapshot_at'],
        'last_error': ({'code': row['last_error_code'], 'detail': row['last_error_detail']}
                       if row['last_error_code'] else None),
        'accepted_count': row['accepted_count'],
        'duplicate_count': row['duplicate_count'],
        'conflict_count': row['conflict_count'],
    } for row in rows]


def known_activity_users() -> List[str]:
    """Principals with durable app state; used only by restart reconciliation."""
    now = int(time.time())
    with _session() as conn:
        rows = conn.execute(
            '''SELECT user_id FROM conversations
               UNION SELECT user_id FROM activity_sources
               UNION SELECT user_id FROM gateway_sessions
                     WHERE revoked_at IS NULL AND expires_at > ?''',
            (now,),
        ).fetchall()
    return [
        row['user_id'] for row in rows
        if isinstance(row['user_id'], str) and 0 < len(row['user_id']) <= 128
        and not _has_controls(row['user_id'])
    ]
