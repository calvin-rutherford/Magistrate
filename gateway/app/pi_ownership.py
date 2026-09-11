"""Durable ownership of one canonical captain turn by exact Pi entries.

This module is deliberately independent of display/worker transport history. A
single-use opaque capability joins a Gateway-prepared canonical reservation to
an authenticated local Pi adapter.  Only an exact, gap-free, finalized source
envelope may update the already-reserved primary assistant message.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import db

PI_OWNERSHIP_SCHEMA = 'magistrate.pi.ownership.v1'
PI_DISPATCH_SCHEMA = 'magistrate.pi.dispatch.v1'
PI_CONTENT_SOURCE = 'pi-semantic'
PI_SOURCE_NAME = 'pi-semantic'
PI_PREPARE_ENTRY_TYPE = 'magistrate.pi.dispatch.prepare.v1'
PI_BIND_ENTRY_TYPE = 'magistrate.pi.dispatch.bind.v1'
PI_FINALIZE_ENTRY_TYPE = 'magistrate.pi.dispatch.finalize.v1'
PI_DEFAULT_CAPABILITY_TTL_MS = 120_000
PI_MIN_CAPABILITY_TTL_MS = 5_000
PI_MAX_CAPABILITY_TTL_MS = 600_000
PI_MAX_SOURCE_ENTRIES = 4096
PI_MAX_VISIBLE_CHARS = 200_000
PI_MAX_VISIBLE_BYTES = 1_000_000
PI_MAX_RECOVERY_BATCH = 100
PI_MAX_CLOCK_SKEW_MS = 30_000
_BUSY_TIMEOUT_SECONDS = 5.0
_PI_ENTRY_ID_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'


class PiOwnershipError(ValueError):
    """A Pi ownership envelope conflicts with durable identity or ordering."""


def _now() -> int:
    return int(time.time() * 1000)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode('utf-8'))


def _valid_unicode(value: str) -> bool:
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _valid_identity_text(value: str) -> bool:
    return _valid_unicode(value) and not any(
        ord(character) < 32
        or 127 <= ord(character) <= 159
        or ord(character) in {0x2028, 0x2029}
        for character in value
    )


def _valid_visible_text(value: str) -> bool:
    return _valid_unicode(value) and not any(
        (ord(character) < 32 and ord(character) not in {9, 10, 13})
        or 127 <= ord(character) <= 159
        for character in value
    )


@contextmanager
def _session():
    conn = sqlite3.connect(db.DB_PATH, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _capability_hash(capability: str) -> str:
    if not isinstance(capability, str) or not re.fullmatch(r'pic_[A-Za-z0-9_-]{40,96}', capability):
        raise PiOwnershipError('The Pi dispatch capability is malformed.')
    return _sha256_text(capability)


def _row_dispatch(row: sqlite3.Row, *, include_secret: bool = False) -> dict[str, Any]:
    result = {
        'schema_version': PI_DISPATCH_SCHEMA,
        'dispatch_incarnation': row['dispatch_incarnation'],
        'capability_sha256': row['capability_sha256'],
        'tenant_id': row['tenant_id'],
        'principal_id': row['principal_id'],
        'conversation_id': row['conversation_id'],
        'turn_id': row['turn_id'],
        'assistant_message_id': row['assistant_message_id'],
        'objective_id': row['objective_id'],
        'run_id': row['run_id'],
        'prompt_sha256': row['prompt_sha256'],
        'state': row['state'],
        'expires_at': row['expires_at'],
        'accepted_envelope_sha256': (
            row['final_envelope_sha256'] if row['state'] == 'finalized'
            else row['failure_envelope_sha256'] if row['state'] == 'failed'
            else None
        ),
    }
    if include_secret:
        try:
            result['capability'] = db.decrypt_token(row['capability_enc'])
            if row['state'] == 'prepared':
                result['prompt'] = db.decrypt_token(row['prompt_enc'])
        except db.SecretDecryptionError as exc:
            raise PiOwnershipError('Stored Pi dispatch material cannot be authenticated.') from exc
    return result


def prepare_pi_dispatch(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    assistant_message_id: str,
    objective_id: str,
    run_id: str,
    prompt: str,
    visible_prompt: str,
    ttl_ms: int = PI_DEFAULT_CAPABILITY_TTL_MS,
) -> dict[str, Any]:
    """Prepare or replay one dispatch inside the caller's prompt transaction.

    The current authenticated principal is also the current tenancy boundary,
    but the protocol keeps the two fields distinct.  Existing ownership makes
    an edited replay a conflict rather than silently changing the model input.
    """
    if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not (
        PI_MIN_CAPABILITY_TTL_MS <= ttl_ms <= PI_MAX_CAPABILITY_TTL_MS
    ):
        raise PiOwnershipError('The Pi dispatch capability TTL is outside the supported range.')
    if (
        not isinstance(user_id, str) or not user_id or len(user_id) > 256
        or not _valid_identity_text(user_id)
    ):
        raise PiOwnershipError('The Pi dispatch owner identity is invalid.')
    if not isinstance(prompt, str) or not prompt or not isinstance(visible_prompt, str):
        raise PiOwnershipError('A Pi dispatch requires non-empty submitted text.')
    if not _valid_unicode(prompt) or not _valid_unicode(visible_prompt):
        raise PiOwnershipError('Pi dispatch text must contain valid Unicode scalar values.')
    prompt_bytes = prompt.encode('utf-8')
    visible_prompt_bytes = visible_prompt.encode('utf-8')
    encoded_prompt_literal = json.dumps(
        prompt, ensure_ascii=False, separators=(',', ':'),
    ).encode('utf-8')
    if (
        len(prompt) > 100_000 or len(prompt_bytes) > PI_MAX_VISIBLE_BYTES
        or len(visible_prompt) > 100_000
        or len(visible_prompt_bytes) > PI_MAX_VISIBLE_BYTES
        or len(encoded_prompt_literal) > PI_MAX_VISIBLE_BYTES
    ):
        raise PiOwnershipError('The Pi dispatch prompt is too large.')
    prompt_hash = _sha256_bytes(prompt_bytes)
    visible_prompt_hash = _sha256_text(visible_prompt)
    existing = conn.execute(
        'SELECT * FROM pi_semantic_dispatches WHERE turn_id = ?', (turn_id,),
    ).fetchone()
    if existing is not None:
        exact_identity = (
            existing['user_id'] == user_id
            and existing['tenant_id'] == user_id
            and existing['principal_id'] == user_id
            and existing['conversation_id'] == conversation_id
            and existing['assistant_message_id'] == assistant_message_id
            and existing['objective_id'] == objective_id
            and existing['run_id'] == run_id
        )
        if not exact_identity:
            raise PiOwnershipError('The canonical turn already has different Pi ownership.')
        if (
            existing['prompt_sha256'] != prompt_hash
            or existing['visible_prompt_sha256'] != visible_prompt_hash
        ):
            raise PiOwnershipError('An owned Pi turn cannot be replayed with different text.')
        return _row_dispatch(existing, include_secret=True)

    # A previously accepted assistant or structured producer is already
    # authoritative. Do not run the model twice or retroactively seize a turn
    # merely because the feature flag changed before an HTTP retry.
    primary = conn.execute(
        "SELECT 1 FROM conversation_messages WHERE turn_id = ? AND slot = 'primary' LIMIT 1",
        (turn_id,),
    ).fetchone()
    if primary is not None:
        raise PiOwnershipError('The turn already has an accepted primary assistant response.')
    structured = conn.execute(
        '''SELECT 1 FROM magi_response_events WHERE turn_id = ?
           UNION ALL
           SELECT 1 FROM magi_additional_response_events WHERE turn_id = ?
           LIMIT 1''',
        (turn_id, turn_id),
    ).fetchone()
    if structured is not None:
        raise PiOwnershipError('The turn already belongs to a structured response producer.')

    now = _now()
    for _ in range(100):
        incarnation = 'pdi_' + secrets.token_urlsafe(24)
        capability = 'pic_' + secrets.token_urlsafe(32)
        capability_sha256 = _sha256_text(capability)
        try:
            conn.execute(
                '''INSERT INTO pi_semantic_dispatches
                   (dispatch_incarnation, capability_sha256, capability_enc,
                    user_id, tenant_id, principal_id, conversation_id, turn_id,
                    assistant_message_id, objective_id, run_id, prompt_sha256,
                    prompt_enc, visible_prompt_sha256, state, expires_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'prepared', ?, ?, ?)''',
                (
                    incarnation, capability_sha256, db.encrypt_token(capability),
                    user_id, user_id, user_id, conversation_id, turn_id,
                    assistant_message_id, objective_id, run_id, prompt_hash,
                    db.encrypt_token(prompt), visible_prompt_hash, now + ttl_ms,
                    now, now,
                ),
            )
            row = conn.execute(
                'SELECT * FROM pi_semantic_dispatches WHERE dispatch_incarnation = ?',
                (incarnation,),
            ).fetchone()
            return _row_dispatch(row, include_secret=True)
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError('Could not allocate a unique Pi dispatch capability.')


def turn_has_pi_ownership(conn: sqlite3.Connection, turn_id: str) -> bool:
    """True from atomic prepare onward, including failure and final replay."""
    return conn.execute(
        'SELECT 1 FROM pi_semantic_dispatches WHERE turn_id = ?', (turn_id,),
    ).fetchone() is not None


def has_pi_ownership(user_id: str, target: str, turn_id: str) -> bool:
    with _session() as conn:
        return conn.execute(
            '''SELECT 1 FROM pi_semantic_dispatches d
               JOIN conversations c ON c.id = d.conversation_id
               WHERE d.turn_id = ? AND d.user_id = ? AND c.user_id = ? AND c.target = ?''',
            (turn_id, user_id, user_id, target),
        ).fetchone() is not None


def has_terminal_fallback_candidates(user_id: str, target: str) -> bool:
    """Whether a terminal read could still lawfully change any recent turn."""
    with _session() as conn:
        conversation = conn.execute(
            'SELECT id FROM conversations WHERE user_id = ? AND target = ?',
            (user_id, target),
        ).fetchone()
        if conversation is None:
            return False
        return conn.execute(
            '''SELECT 1 FROM conversation_turns t
               WHERE t.conversation_id = ? AND t.status NOT IN ('cancelled', 'failed')
                 AND NOT EXISTS (
                     SELECT 1 FROM pi_semantic_dispatches d WHERE d.turn_id = t.id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM magi_response_events e WHERE e.turn_id = t.id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM magi_additional_response_events e WHERE e.turn_id = t.id
                 )
               ORDER BY t.sequence_index DESC LIMIT 1''',
            (conversation['id'],),
        ).fetchone() is not None


def get_recoverable_dispatches(limit: int = PI_MAX_RECOVERY_BATCH) -> list[dict[str, Any]]:
    """Return open work plus completed evidence awaiting an adapter receipt."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= PI_MAX_RECOVERY_BATCH:
        raise ValueError('Pi recovery limit is outside the supported range.')
    with _session() as conn:
        rows = conn.execute(
            '''SELECT * FROM pi_semantic_dispatches
               WHERE state IN ('prepared', 'bound')
                  OR (state IN ('finalized', 'failed') AND adapter_acknowledged_at IS NULL)
               ORDER BY updated_at, dispatch_incarnation LIMIT ?''',
            (limit,),
        ).fetchall()
        recoverable = []
        for row in rows:
            try:
                recoverable.append(_row_dispatch(row, include_secret=True))
            except PiOwnershipError:
                # Fail closed, but rotate an undecryptable row behind other
                # owners so a bounded batch cannot be permanently starved.
                now = _now()
                conn.execute(
                    '''UPDATE pi_semantic_dispatches
                       SET last_attempt_at = ?, updated_at = ?
                       WHERE dispatch_incarnation = ?''',
                    (now, now, row['dispatch_incarnation']),
                )
                continue
        return recoverable


def get_pi_dispatch(
    user_id: str, target: str, dispatch_incarnation: str, *, include_secret: bool = False,
) -> dict[str, Any]:
    with _session() as conn:
        row = conn.execute(
            '''SELECT d.* FROM pi_semantic_dispatches d
               JOIN conversations c ON c.id = d.conversation_id
               WHERE d.dispatch_incarnation = ? AND d.user_id = ?
                 AND c.user_id = ? AND c.target = ?''',
            (dispatch_incarnation, user_id, user_id, target),
        ).fetchone()
        if row is None:
            raise LookupError('Pi dispatch not found.')
        result = _row_dispatch(row, include_secret=include_secret)
        result.update({
            'pi_session_id': row['pi_session_id'],
            'pi_user_entry_id': row['pi_user_entry_id'],
            'pi_assistant_entry_id': row['pi_assistant_entry_id'],
            'visible_content_sha256': row['visible_content_sha256'],
            'failure_code': row['failure_code'],
            'adapter_acknowledged_at': row['adapter_acknowledged_at'],
        })
        return result


def mark_pi_adapter_acknowledged(
    user_id: str,
    target: str,
    dispatch_incarnation: str,
    accepted_envelope_sha256: str,
) -> None:
    if not isinstance(accepted_envelope_sha256, str) or not re.fullmatch(
        r'[a-f0-9]{64}', accepted_envelope_sha256,
    ):
        raise PiOwnershipError('The Pi adapter acknowledgement hash is malformed.')
    with _session() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _owned_row(conn, user_id, target, dispatch_incarnation)
        expected = (
            row['final_envelope_sha256'] if row['state'] == 'finalized'
            else row['failure_envelope_sha256'] if row['state'] == 'failed'
            else None
        )
        if expected is None or not hmac.compare_digest(expected, accepted_envelope_sha256):
            raise PiOwnershipError('The Pi adapter acknowledged different canonical evidence.')
        if row['adapter_acknowledged_at'] is None:
            now = _now()
            conn.execute(
                '''UPDATE pi_semantic_dispatches
                   SET adapter_acknowledged_at = ?, capability_enc = '', prompt_enc = '',
                       updated_at = ? WHERE dispatch_incarnation = ?''',
                (now, now, dispatch_incarnation),
            )


def mark_pi_dispatch_attempt(dispatch_incarnation: str) -> None:
    now = _now()
    with _session() as conn:
        conn.execute(
            'UPDATE pi_semantic_dispatches SET last_attempt_at = ?, updated_at = ? WHERE dispatch_incarnation = ?',
            (now, now, dispatch_incarnation),
        )


class _StrictPiModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*', mode='after')
    @classmethod
    def validate_unicode(cls, value: Any) -> Any:
        if isinstance(value, str) and not _valid_unicode(value):
            raise ValueError('Pi ownership strings must contain valid Unicode scalar values.')
        return value


class PiSourceEntry(_StrictPiModel):
    order: int = Field(ge=0, le=9_007_199_254_740_991)
    entry_id: str = Field(min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN)
    parent_id: Optional[str] = Field(default=None, max_length=128, pattern=_PI_ENTRY_ID_PATTERN)
    entry_type: Literal['message', 'custom', 'redacted']
    role: Optional[Literal['user', 'assistant']] = None
    custom_type: Optional[str] = Field(default=None, min_length=1, max_length=128)
    stop_reason: Optional[Literal['stop', 'length', 'toolUse', 'error', 'aborted']] = None

    @model_validator(mode='after')
    def validate_shape(self) -> 'PiSourceEntry':
        if self.entry_type == 'message':
            if self.role is None or self.custom_type is not None:
                raise ValueError('Pi message source entries require only a structural role.')
            if self.role == 'assistant' and self.stop_reason is None:
                raise ValueError('Pi assistant source entries require finality metadata.')
            if self.role != 'assistant' and self.stop_reason is not None:
                raise ValueError('Only Pi assistant entries have a stop reason.')
        elif self.entry_type == 'custom':
            if self.custom_type is None or self.role is not None or self.stop_reason is not None:
                raise ValueError('Pi custom source entries require only a custom type.')
        elif self.role is not None or self.custom_type is not None or self.stop_reason is not None:
            raise ValueError('Non-message Pi source entries cannot carry message metadata.')
        return self


class PiAssistantContentBlock(_StrictPiModel):
    # This is an ordinal among visible text blocks, not the Pi content-array
    # index. Consequently omitted thinking/tool blocks disclose neither data nor
    # position through this channel.
    index: int = Field(ge=0, le=1_000_000)
    type: Literal['text']
    text: str


class PiOwnershipEnvelope(_StrictPiModel):
    schema_version: Literal['magistrate.pi.ownership.v1']
    event_type: Literal['dispatch.prepared', 'dispatch.bound', 'dispatch.finalized', 'dispatch.failed']
    request_nonce: str = Field(min_length=16, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    dispatch_incarnation: str = Field(min_length=20, max_length=100, pattern=r'^pdi_[A-Za-z0-9_-]+$')
    capability_sha256: str = Field(min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    tenant_id: str = Field(min_length=1, max_length=256)
    principal_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=4, max_length=128, pattern=r'^cv_[A-Za-z0-9_-]+$')
    turn_id: str = Field(min_length=4, max_length=128, pattern=r'^ct_[A-Za-z0-9_-]+$')
    assistant_message_id: str = Field(min_length=4, max_length=128, pattern=r'^cm_[A-Za-z0-9_-]+$')
    objective_id: str = Field(min_length=5, max_length=128, pattern=r'^obj_[A-Za-z0-9_-]+$')
    run_id: str = Field(min_length=5, max_length=128, pattern=r'^run_[A-Za-z0-9_-]+$')
    source_revision: int = Field(ge=0, le=2)
    pi_session_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    pi_prepare_entry_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    pi_user_entry_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    pi_user_entry_order: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    pi_user_content_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    pi_bind_entry_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    pi_assistant_entry_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    pi_assistant_entry_order: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    pi_finalize_entry_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    finality: Literal['pending', 'final', 'failed']
    stop_reason: Optional[Literal['stop', 'length', 'toolUse', 'error', 'aborted']] = None
    bound_at: Optional[int] = Field(default=None, ge=1_000_000_000_000)
    finalized_at: Optional[int] = Field(default=None, ge=1_000_000_000_000)
    source_start_order: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    source_end_order: Optional[int] = Field(default=None, ge=0, le=9_007_199_254_740_991)
    source_cursor: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=_PI_ENTRY_ID_PATTERN,
    )
    source_sequence: list[PiSourceEntry] = Field(default_factory=list, max_length=PI_MAX_SOURCE_ENTRIES)
    source_sequence_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    visible_content_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    assistant_content: list[PiAssistantContentBlock] = Field(default_factory=list, max_length=4096)
    error_code: Optional[str] = Field(default=None, min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9._-]{0,63}$')

    @field_validator('tenant_id', 'principal_id', mode='after')
    @classmethod
    def validate_owner_identity(cls, value: str) -> str:
        if not _valid_identity_text(value):
            raise ValueError('Pi owner identity contains unsafe control characters.')
        return value

    @model_validator(mode='after')
    def validate_stage(self) -> 'PiOwnershipEnvelope':
        binding_identity = (
            self.pi_session_id, self.pi_prepare_entry_id, self.pi_user_entry_id,
            self.pi_user_entry_order, self.pi_user_content_sha256,
            self.pi_bind_entry_id, self.bound_at,
        )
        final_identity = (
            self.pi_assistant_entry_id, self.pi_assistant_entry_order,
            self.pi_finalize_entry_id, self.finalized_at,
        )
        source_identity = (
            self.source_start_order, self.source_end_order, self.source_cursor,
            self.source_sequence_sha256,
        )
        if self.event_type == 'dispatch.prepared':
            if (
                self.source_revision != 0 or self.finality != 'pending'
                or any(value is not None for value in binding_identity + final_identity + source_identity)
                or self.stop_reason is not None or self.error_code is not None
                or self.source_sequence or self.assistant_content
                or self.visible_content_sha256 is not None
            ):
                raise ValueError('A prepared dispatch must carry no claimed Pi source state.')
        elif self.event_type == 'dispatch.bound':
            if (
                self.source_revision != 1 or self.finality != 'pending'
                or any(value is None for value in binding_identity + source_identity)
                or any(value is not None for value in final_identity)
                or self.stop_reason is not None or self.error_code is not None
                or not self.source_sequence or self.assistant_content
                or self.visible_content_sha256 is not None
            ):
                raise ValueError('A bound dispatch must carry only complete binding evidence.')
        elif self.event_type == 'dispatch.finalized':
            if (
                self.source_revision != 2 or self.finality != 'final'
                or self.stop_reason != 'stop'
                or any(value is None for value in binding_identity + final_identity + source_identity)
                or self.error_code is not None or not self.source_sequence
                or not self.assistant_content or self.visible_content_sha256 is None
            ):
                raise ValueError('Only a normally stopped exact Pi entry is a successful final response.')
        elif self.event_type == 'dispatch.failed':
            if (
                self.source_revision != 0 or self.finality != 'failed'
                or self.error_code is None
                or any(value is not None for value in binding_identity + final_identity + source_identity)
                or self.source_sequence or self.assistant_content
                or self.visible_content_sha256 is not None or self.stop_reason is not None
            ):
                raise ValueError('A failed dispatch cannot carry assistant content.')
        return self


def _semantic_envelope_hash(envelope: PiOwnershipEnvelope) -> str:
    body = envelope.model_dump(mode='json')
    # A transport retry has a new nonce but must represent identical semantic
    # evidence.  The nonce is authenticated by IPC, not part of replay identity.
    body.pop('request_nonce', None)
    return _sha256_bytes(_json_bytes(body))


def _sequence_dicts(envelope: PiOwnershipEnvelope) -> list[dict[str, Any]]:
    return [entry.model_dump(mode='json') for entry in envelope.source_sequence]


def _validate_sequence(envelope: PiOwnershipEnvelope, *, final: bool) -> tuple[list[dict[str, Any]], str]:
    sequence = _sequence_dicts(envelope)
    if not sequence or len(sequence) > PI_MAX_SOURCE_ENTRIES:
        raise PiOwnershipError('Pi source evidence is empty or oversized.')
    if envelope.source_start_order is None or envelope.source_end_order is None:
        raise PiOwnershipError('Pi source ordering is incomplete.')
    if (
        envelope.source_end_order < envelope.source_start_order
        or envelope.source_end_order - envelope.source_start_order + 1 != len(sequence)
        or any(
            item['order'] != envelope.source_start_order + index
            for index, item in enumerate(sequence)
        )
    ):
        raise PiOwnershipError('Pi source ordering is gapped or ambiguous.')
    ids = [item['entry_id'] for item in sequence]
    if len(ids) != len(set(ids)):
        raise PiOwnershipError('Pi source entry identity is ambiguous.')
    for previous, current in zip(sequence, sequence[1:]):
        if current['parent_id'] != previous['entry_id']:
            raise PiOwnershipError('Pi source entries do not form one exact parent chain.')
    computed_hash = _sha256_bytes(_json_bytes(sequence))
    if not envelope.source_sequence_sha256 or not hmac.compare_digest(
        computed_hash, envelope.source_sequence_sha256,
    ):
        raise PiOwnershipError('Pi source sequence hash does not match its entries.')
    if envelope.source_cursor != sequence[-1]['entry_id']:
        raise PiOwnershipError('Pi source cursor does not identify the accepted tail.')

    by_id = {item['entry_id']: item for item in sequence}
    prepare = by_id.get(envelope.pi_prepare_entry_id or '')
    user = by_id.get(envelope.pi_user_entry_id or '')
    bind = by_id.get(envelope.pi_bind_entry_id or '')
    if (
        prepare is None or sequence[0] is not prepare
        or prepare['entry_type'] != 'custom'
        or prepare['custom_type'] != PI_PREPARE_ENTRY_TYPE
    ):
        raise PiOwnershipError('Pi prepare marker does not anchor the source sequence.')
    if user is None or user['entry_type'] != 'message' or user['role'] != 'user':
        raise PiOwnershipError('The initiating Pi user entry is not exact.')
    if envelope.pi_user_entry_order != user['order']:
        raise PiOwnershipError('The initiating Pi user order does not match its entry.')
    if bind is None or bind['entry_type'] != 'custom' or bind['custom_type'] != PI_BIND_ENTRY_TYPE:
        raise PiOwnershipError('Pi bind marker is missing from the source sequence.')
    positions = {item['entry_id']: index for index, item in enumerate(sequence)}
    if not positions[prepare['entry_id']] < positions[user['entry_id']] < positions[bind['entry_id']]:
        raise PiOwnershipError('Pi prepare, user, and bind ordering is invalid.')
    user_entries = [item for item in sequence if item['entry_type'] == 'message' and item['role'] == 'user']
    if len(user_entries) != 1 or user_entries[0]['entry_id'] != envelope.pi_user_entry_id:
        raise PiOwnershipError('Another Pi user boundary makes source ownership ambiguous.')

    if not final:
        if sequence[-1]['entry_id'] != envelope.pi_bind_entry_id:
            raise PiOwnershipError('A binding sequence must end at its durable bind marker.')
        return sequence, computed_hash

    assistant = by_id.get(envelope.pi_assistant_entry_id or '')
    finalize = by_id.get(envelope.pi_finalize_entry_id or '')
    if (
        assistant is None or assistant['entry_type'] != 'message'
        or assistant['role'] != 'assistant' or assistant['stop_reason'] != 'stop'
        or envelope.pi_assistant_entry_order != assistant['order']
    ):
        raise PiOwnershipError('The finalized Pi assistant entry is not exact.')
    if (
        finalize is None or sequence[-1] is not finalize
        or finalize['entry_type'] != 'custom'
        or finalize['custom_type'] != PI_FINALIZE_ENTRY_TYPE
    ):
        raise PiOwnershipError('Pi finalize marker does not close the source sequence.')
    if not positions[bind['entry_id']] < positions[assistant['entry_id']] < positions[finalize['entry_id']]:
        raise PiOwnershipError('Pi bind, assistant, and finalize ordering is invalid.')
    later_assistants = [
        item for item in sequence[positions[assistant['entry_id']] + 1:]
        if item['entry_type'] == 'message' and item['role'] == 'assistant'
    ]
    if later_assistants:
        raise PiOwnershipError('The bound Pi assistant is not the final assistant entry.')
    return sequence, computed_hash


def _visible_text(envelope: PiOwnershipEnvelope) -> tuple[str, int]:
    blocks = [block.model_dump(mode='json', exclude_none=True) for block in envelope.assistant_content]
    if not blocks or [block['index'] for block in blocks] != list(range(len(blocks))):
        raise PiOwnershipError('Pi assistant content blocks are gapped or incomplete.')
    visible = [
        {'index': block['index'], 'text': block['text']}
        for block in blocks
    ]
    computed_hash = _sha256_bytes(_json_bytes(visible))
    if not envelope.visible_content_sha256 or not hmac.compare_digest(
        computed_hash, envelope.visible_content_sha256,
    ):
        raise PiOwnershipError('Pi visible-content hash does not match its text blocks.')
    text = ''.join(item['text'] for item in visible)
    encoded = text.encode('utf-8')
    if not text.strip():
        raise PiOwnershipError('A finalized Pi assistant entry has no visible text.')
    if not _valid_visible_text(text):
        raise PiOwnershipError('Pi finalized visible content contains unsafe control characters.')
    if len(text) > PI_MAX_VISIBLE_CHARS or len(encoded) > PI_MAX_VISIBLE_BYTES:
        raise PiOwnershipError('The finalized Pi assistant content is too large.')
    return text, len(encoded)


def _owned_row(
    conn: sqlite3.Connection, user_id: str, target: str, dispatch_incarnation: str,
) -> sqlite3.Row:
    row = conn.execute(
        '''SELECT d.*, c.user_id AS owner_user_id, c.target AS conversation_target,
                  t.status AS turn_status, t.lifecycle_state AS lifecycle_state,
                  t.lifecycle_revision AS lifecycle_revision,
                  t.lifecycle_decision_key AS lifecycle_decision_key,
                  t.sequence_index AS turn_sequence
           FROM pi_semantic_dispatches d
           JOIN conversations c ON c.id = d.conversation_id
           JOIN conversation_turns t ON t.id = d.turn_id
           WHERE d.dispatch_incarnation = ? AND d.user_id = ?
             AND c.user_id = ? AND c.target = ?''',
        (dispatch_incarnation, user_id, user_id, target),
    ).fetchone()
    if row is None:
        raise LookupError('Pi dispatch not found.')
    return row


def _verify_identity(row: sqlite3.Row, envelope: PiOwnershipEnvelope, capability: str) -> None:
    capability_sha256 = _capability_hash(capability)
    if not hmac.compare_digest(capability_sha256, row['capability_sha256']):
        raise PiOwnershipError('The Pi dispatch capability does not own this incarnation.')
    if not hmac.compare_digest(envelope.capability_sha256, row['capability_sha256']):
        raise PiOwnershipError('The Pi adapter returned a different capability identity.')
    expected = {
        'tenant_id': row['tenant_id'],
        'principal_id': row['principal_id'],
        'conversation_id': row['conversation_id'],
        'turn_id': row['turn_id'],
        'assistant_message_id': row['assistant_message_id'],
        'objective_id': row['objective_id'],
        'run_id': row['run_id'],
    }
    if any(getattr(envelope, key) != value for key, value in expected.items()):
        raise PiOwnershipError('The Pi adapter envelope crosses canonical ownership identity.')


def _record_message_change(conn: sqlite3.Connection, message_id: str) -> None:
    row = conn.execute(
        'SELECT conversation_id, revision, updated_at FROM conversation_messages WHERE id = ?',
        (message_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError('A Pi canonical change references a missing message.')
    top = conn.execute(
        'SELECT MAX(change_sequence) AS top FROM conversation_changes WHERE conversation_id = ?',
        (row['conversation_id'],),
    ).fetchone()['top']
    sequence = int(top) + 1 if top is not None else 0
    conn.execute(
        '''INSERT INTO conversation_changes
           (conversation_id, change_sequence, message_id, message_revision, changed_at)
           VALUES (?, ?, ?, ?, ?)''',
        (row['conversation_id'], sequence, message_id, row['revision'], row['updated_at']),
    )


def _apply_binding(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    envelope: PiOwnershipEnvelope,
    sequence: list[dict[str, Any]],
    sequence_hash: str,
    envelope_hash: str,
) -> str:
    if envelope.bound_at is None or envelope.bound_at > row['expires_at']:
        raise PiOwnershipError('The Pi dispatch capability expired before source binding.')
    if not envelope.pi_user_content_sha256 or not hmac.compare_digest(
        envelope.pi_user_content_sha256, row['prompt_sha256'],
    ):
        raise PiOwnershipError('The exact Pi user entry differs from the dispatched prompt.')
    if envelope.bound_at < row['created_at'] - 5_000:
        raise PiOwnershipError('The Pi source binding predates its dispatch capability.')
    if envelope.bound_at > _now() + PI_MAX_CLOCK_SKEW_MS:
        raise PiOwnershipError('The Pi source binding time is in the future.')
    if row['state'] == 'finalized':
        raise PiOwnershipError('A finalized Pi dispatch cannot be rebound.')
    if row['state'] == 'failed':
        raise PiOwnershipError('A failed Pi dispatch cannot be rebound.')
    if row['state'] == 'bound':
        if (
            row['binding_envelope_sha256'] == envelope_hash
            and row['pi_session_id'] == envelope.pi_session_id
            and row['pi_user_entry_id'] == envelope.pi_user_entry_id
            and row['pi_user_entry_sha256'] == envelope.pi_user_content_sha256
        ):
            return 'duplicate'
        raise PiOwnershipError('The Pi dispatch capability was already consumed by another binding.')
    if not envelope.pi_session_id:
        raise PiOwnershipError('A Pi binding requires an exact session identity.')
    now = _now()
    try:
        conn.execute(
            '''UPDATE pi_semantic_dispatches
               SET state = 'bound', capability_used_at = ?, pi_session_id = ?,
                   pi_prepare_entry_id = ?, pi_user_entry_id = ?, pi_user_entry_order = ?,
                   pi_user_entry_sha256 = ?, pi_bind_entry_id = ?, binding_sequence_json = ?,
                   binding_sequence_sha256 = ?, binding_envelope_sha256 = ?,
                   source_start_order = ?, source_end_order = ?, source_cursor = ?,
                   source_sequence_sha256 = ?, bound_at = ?, updated_at = ?
               WHERE dispatch_incarnation = ? AND state = 'prepared' ''',
            (
                now, envelope.pi_session_id, envelope.pi_prepare_entry_id,
                envelope.pi_user_entry_id, envelope.pi_user_entry_order,
                envelope.pi_user_content_sha256, envelope.pi_bind_entry_id,
                json.dumps(sequence, ensure_ascii=False, separators=(',', ':'), sort_keys=True),
                sequence_hash, envelope_hash, envelope.source_start_order,
                envelope.source_end_order, envelope.source_cursor, sequence_hash,
                envelope.bound_at, now, row['dispatch_incarnation'],
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise PiOwnershipError('A Pi source entry is already owned by another canonical turn.') from exc
    if conn.execute('SELECT changes()').fetchone()[0] != 1:
        raise PiOwnershipError('The Pi dispatch binding changed concurrently.')
    return 'applied'


def _upsert_final_message(
    conn: sqlite3.Connection, row: sqlite3.Row, text: str,
) -> tuple[int, bool]:
    existing = conn.execute(
        'SELECT * FROM conversation_messages WHERE id = ?', (row['assistant_message_id'],),
    ).fetchone()
    now = _now()
    sequence_index = int(row['turn_sequence']) * 1000 + 999
    if existing is None:
        slot_collision = conn.execute(
            "SELECT id FROM conversation_messages WHERE turn_id = ? AND slot = 'primary'",
            (row['turn_id'],),
        ).fetchone()
        if slot_collision is not None:
            raise PiOwnershipError('The reserved Pi assistant slot has a different message identity.')
        conn.execute(
            '''INSERT INTO conversation_messages
               (id, turn_id, conversation_id, role, type, slot, text,
                visible_in_chat, sequence_index, revision, source,
                attachments_json, content_source, structured_content_json,
                structured_revision, assistant_kind, created_at, updated_at)
               VALUES (?, ?, ?, 'assistant', 'conversation', 'primary', ?, 1,
                       ?, 1, ?, '[]', ?, NULL, NULL, 'response', ?, ?)''',
            (
                row['assistant_message_id'], row['turn_id'], row['conversation_id'],
                text, sequence_index, PI_SOURCE_NAME, PI_CONTENT_SOURCE, now, now,
            ),
        )
        _record_message_change(conn, row['assistant_message_id'])
        return 1, True
    if existing['turn_id'] != row['turn_id'] or existing['slot'] != 'primary':
        raise PiOwnershipError('The reserved Pi assistant identity is already used elsewhere.')
    if existing['content_source'] == 'structured':
        raise PiOwnershipError('Accepted structured canonical content cannot be overwritten.')
    changed = (
        existing['text'] != text
        or existing['role'] != 'assistant'
        or existing['type'] != 'conversation'
        or not bool(existing['visible_in_chat'])
        or existing['sequence_index'] != sequence_index
        or existing['source'] != PI_SOURCE_NAME
        or existing['content_source'] != PI_CONTENT_SOURCE
        or existing['structured_content_json'] is not None
        or existing['structured_revision'] is not None
    )
    if not changed:
        return int(existing['revision']), False
    conn.execute(
        '''UPDATE conversation_messages
           SET role = 'assistant', type = 'conversation', text = ?,
               visible_in_chat = 1, sequence_index = ?, source = ?, content_source = ?,
               structured_content_json = NULL, structured_revision = NULL,
               assistant_kind = 'response', revision = revision + 1,
               updated_at = ? WHERE id = ?''',
        (
            text, sequence_index, PI_SOURCE_NAME, PI_CONTENT_SOURCE,
            now, row['assistant_message_id'],
        ),
    )
    _record_message_change(conn, row['assistant_message_id'])
    revision = conn.execute(
        'SELECT revision FROM conversation_messages WHERE id = ?',
        (row['assistant_message_id'],),
    ).fetchone()['revision']
    return int(revision), True


def apply_pi_ownership_envelope(
    user_id: str,
    target: str,
    capability: str,
    payload: Any,
) -> dict[str, Any]:
    """Verify and consume one authenticated adapter state transition.

    Validation occurs before mutation.  Binding/finalization then share one
    SQLite transaction with the canonical update, so malformed, ambiguous,
    cross-tenant, gapped, or hash-mismatched input leaves accepted data intact.
    """
    envelope = PiOwnershipEnvelope.model_validate(payload)
    envelope_hash = _semantic_envelope_hash(envelope)

    sequence: list[dict[str, Any]] = []
    sequence_hash = ''
    text: Optional[str] = None
    visible_bytes: Optional[int] = None
    if envelope.event_type == 'dispatch.bound':
        sequence, sequence_hash = _validate_sequence(envelope, final=False)
        if envelope.assistant_content or envelope.visible_content_sha256 is not None:
            raise PiOwnershipError('A pending Pi binding cannot carry assistant content.')
    elif envelope.event_type == 'dispatch.finalized':
        sequence, sequence_hash = _validate_sequence(envelope, final=True)
        text, visible_bytes = _visible_text(envelope)
    elif envelope.event_type == 'dispatch.prepared':
        if envelope.source_sequence or envelope.assistant_content:
            raise PiOwnershipError('A prepared adapter response cannot claim Pi entries.')
    elif envelope.event_type == 'dispatch.failed':
        if envelope.assistant_content:
            raise PiOwnershipError('A failed adapter response cannot carry visible content.')

    with _session() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = _owned_row(conn, user_id, target, envelope.dispatch_incarnation)
        _verify_identity(row, envelope, capability)

        if envelope.event_type == 'dispatch.prepared':
            return {
                'status': 'pending', 'state': row['state'],
                'dispatch_incarnation': row['dispatch_incarnation'],
                'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
            }

        if envelope.event_type == 'dispatch.failed':
            if row['state'] == 'finalized':
                raise PiOwnershipError('A finalized Pi dispatch cannot fail afterward.')
            if row['state'] == 'failed':
                if (
                    row['failure_code'] == envelope.error_code
                    and row['failure_envelope_sha256'] == envelope_hash
                ):
                    return {
                        'status': 'duplicate', 'state': 'failed',
                        'dispatch_incarnation': row['dispatch_incarnation'],
                        'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
                        'accepted_envelope_sha256': envelope_hash,
                    }
                raise PiOwnershipError('The Pi dispatch already has a different failure.')
            now = _now()
            conn.execute(
                '''UPDATE pi_semantic_dispatches
                   SET state = 'failed', failure_code = ?,
                       failure_envelope_sha256 = ?, updated_at = ?
                   WHERE dispatch_incarnation = ?''',
                (envelope.error_code, envelope_hash, now, row['dispatch_incarnation']),
            )
            if row['lifecycle_state'] not in {'completed', 'cancelled', 'failed'}:
                conn.execute(
                    '''UPDATE conversation_turns
                       SET status = 'failed', lifecycle_state = 'failed',
                           lifecycle_decision_key = NULL,
                           lifecycle_revision = lifecycle_revision + 1, updated_at = ?
                       WHERE id = ?''',
                    (now, row['turn_id']),
                )
            return {
                'status': 'applied', 'state': 'failed',
                'dispatch_incarnation': row['dispatch_incarnation'],
                'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
                'accepted_envelope_sha256': envelope_hash,
            }

        if envelope.event_type == 'dispatch.bound':
            status = _apply_binding(
                conn, row, envelope, sequence, sequence_hash, envelope_hash,
            )
            return {
                'status': status, 'state': 'bound',
                'dispatch_incarnation': row['dispatch_incarnation'],
                'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
            }

        # A final recovery envelope contains the complete binding prefix.  This
        # lets a Gateway that crashed before consuming revision one recover
        # directly from durable adapter state without resubmitting the prompt.
        assert text is not None and visible_bytes is not None
        if envelope.bound_at is None or envelope.finalized_at is None:
            raise PiOwnershipError('A finalized Pi envelope lacks durable transition times.')
        if envelope.finalized_at < envelope.bound_at:
            raise PiOwnershipError('Pi finalization predates source binding.')
        if envelope.finalized_at > _now() + PI_MAX_CLOCK_SKEW_MS:
            raise PiOwnershipError('Pi finalization time is in the future.')
        if row['state'] == 'finalized':
            if (
                row['final_envelope_sha256'] == envelope_hash
                and row['visible_content_sha256'] == envelope.visible_content_sha256
                and row['pi_assistant_entry_id'] == envelope.pi_assistant_entry_id
            ):
                message = conn.execute(
                    'SELECT revision FROM conversation_messages WHERE id = ?',
                    (row['assistant_message_id'],),
                ).fetchone()
                return {
                    'status': 'duplicate', 'state': 'finalized',
                    'dispatch_incarnation': row['dispatch_incarnation'],
                    'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
                    'message_revision': message['revision'] if message else None,
                    'accepted_envelope_sha256': envelope_hash,
                }
            raise PiOwnershipError('Final Pi source replay conflicts with accepted content.')
        if row['state'] == 'failed':
            if (
                row['failure_code'] == 'canonical-turn-frozen'
                and row['failure_envelope_sha256'] == envelope_hash
            ):
                return {
                    'status': 'duplicate', 'state': 'failed',
                    'dispatch_incarnation': row['dispatch_incarnation'],
                    'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
                    'accepted_envelope_sha256': envelope_hash,
                }
            raise PiOwnershipError('A failed Pi dispatch cannot finalize afterward.')

        bind_position = next(
            (index for index, item in enumerate(sequence)
             if item['entry_id'] == envelope.pi_bind_entry_id),
            None,
        )
        if bind_position is None:
            raise PiOwnershipError('The final Pi sequence has no binding prefix.')
        binding_sequence = sequence[:bind_position + 1]
        binding_hash = _sha256_bytes(_json_bytes(binding_sequence))
        binding_payload = envelope.model_copy(update={
            'event_type': 'dispatch.bound',
            'source_revision': 1,
            'pi_assistant_entry_id': None,
            'pi_assistant_entry_order': None,
            'pi_finalize_entry_id': None,
            'finality': 'pending',
            'stop_reason': None,
            'finalized_at': None,
            'source_end_order': binding_sequence[-1]['order'],
            'source_cursor': binding_sequence[-1]['entry_id'],
            'source_sequence': [PiSourceEntry.model_validate(item) for item in binding_sequence],
            'source_sequence_sha256': binding_hash,
            'visible_content_sha256': None,
            'assistant_content': [],
            'error_code': None,
        })
        binding_envelope_hash = _semantic_envelope_hash(binding_payload)
        if row['state'] == 'prepared':
            _apply_binding(
                conn, row, binding_payload, binding_sequence, binding_hash,
                binding_envelope_hash,
            )
            row = _owned_row(conn, user_id, target, envelope.dispatch_incarnation)
        else:
            try:
                stored_binding = json.loads(row['binding_sequence_json'] or 'null')
            except json.JSONDecodeError as exc:
                raise PiOwnershipError('Stored Pi binding evidence is invalid.') from exc
            if (
                stored_binding != binding_sequence
                or row['binding_sequence_sha256'] != binding_hash
                or row['binding_envelope_sha256'] != binding_envelope_hash
            ):
                raise PiOwnershipError('Final Pi source evidence changed its accepted binding prefix.')
            if (
                row['pi_session_id'] != envelope.pi_session_id
                or row['pi_user_entry_id'] != envelope.pi_user_entry_id
                or row['pi_user_entry_sha256'] != envelope.pi_user_content_sha256
                or row['pi_bind_entry_id'] != envelope.pi_bind_entry_id
            ):
                raise PiOwnershipError('Final Pi source identity differs from its binding.')

        if row['turn_status'] in {'cancelled', 'failed'} or row['lifecycle_state'] in {'cancelled', 'failed'}:
            # The final source proof is accepted only as a discard receipt. It
            # can now be acknowledged/pruned without ever writing assistant text.
            now = _now()
            conn.execute(
                '''UPDATE pi_semantic_dispatches
                   SET state = 'failed', failure_code = 'canonical-turn-frozen',
                       failure_envelope_sha256 = ?, updated_at = ?
                   WHERE dispatch_incarnation = ? AND state = 'bound' ''',
                (envelope_hash, now, row['dispatch_incarnation']),
            )
            if conn.execute('SELECT changes()').fetchone()[0] != 1:
                raise PiOwnershipError('The frozen Pi disposition changed concurrently.')
            return {
                'status': 'discarded', 'state': 'failed',
                'dispatch_incarnation': row['dispatch_incarnation'],
                'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
                'accepted_envelope_sha256': envelope_hash,
            }
        revision, changed = _upsert_final_message(conn, row, text)
        now = _now()
        lifecycle_changed = row['lifecycle_state'] != 'completed'
        conn.execute(
            '''UPDATE conversation_turns
               SET status = 'answered', lifecycle_state = 'completed',
                   lifecycle_decision_key = NULL,
                   lifecycle_revision = lifecycle_revision + ?, updated_at = ?
               WHERE id = ?''',
            (1 if lifecycle_changed else 0, now, row['turn_id']),
        )
        try:
            conn.execute(
                '''UPDATE pi_semantic_dispatches
                   SET state = 'finalized', pi_assistant_entry_id = ?,
                       pi_assistant_entry_order = ?, pi_finalize_entry_id = ?,
                       source_start_order = ?, source_end_order = ?, source_cursor = ?,
                       source_sequence_sha256 = ?, visible_content_sha256 = ?,
                       visible_content_bytes = ?, final_envelope_sha256 = ?,
                       finalized_at = ?, updated_at = ?
                   WHERE dispatch_incarnation = ? AND state = 'bound' ''',
                (
                    envelope.pi_assistant_entry_id, envelope.pi_assistant_entry_order,
                    envelope.pi_finalize_entry_id, envelope.source_start_order,
                    envelope.source_end_order, envelope.source_cursor, sequence_hash,
                    envelope.visible_content_sha256, visible_bytes, envelope_hash,
                    envelope.finalized_at, now, row['dispatch_incarnation'],
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PiOwnershipError('A Pi assistant entry is already owned by another canonical turn.') from exc
        if conn.execute('SELECT changes()').fetchone()[0] != 1:
            raise PiOwnershipError('The Pi dispatch finalization changed concurrently.')
        conn.execute(
            'UPDATE conversations SET updated_at = ? WHERE id = ?',
            (now, row['conversation_id']),
        )
        return {
            'status': 'applied', 'state': 'finalized',
            'dispatch_incarnation': row['dispatch_incarnation'],
            'turn_id': row['turn_id'], 'message_id': row['assistant_message_id'],
            'message_revision': revision, 'message_changed': changed,
            'visible_content_sha256': envelope.visible_content_sha256,
            'accepted_envelope_sha256': envelope_hash,
        }
