import ast
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app import db
from app.contracts import MagiEventContract
from app.conversation_store import (
    MagiEventConflict,
    apply_magi_event,
    list_messages,
    ingest_terminal_rows,
    record_prompt,
    reserve_assistant_message,
    reset_conversation,
    set_turn_status,
)
from app.pi_ownership import (
    PI_BIND_ENTRY_TYPE,
    PI_FINALIZE_ENTRY_TYPE,
    PI_PREPARE_ENTRY_TYPE,
    PiOwnershipEnvelope,
    PiOwnershipError,
    apply_pi_ownership_envelope,
    get_pi_dispatch,
    get_recoverable_dispatches,
    has_pi_ownership,
    has_terminal_fallback_candidates,
    mark_pi_adapter_acknowledged,
)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')).hexdigest()


def source_entry(order, entry_id, parent_id, entry_type, *, role=None, custom_type=None, stop_reason=None):
    return {
        'order': order,
        'entry_id': entry_id,
        'parent_id': parent_id,
        'entry_type': entry_type,
        'role': role,
        'custom_type': custom_type,
        'stop_reason': stop_reason,
    }


def create_owned_turn(prompt='exact prompt', *, ttl_ms=120_000, user_id=None):
    suffix = uuid.uuid4().hex
    user_id = user_id or f'pi-user-{suffix}'
    target = 'captain'
    turn = record_prompt(
        user_id, target, f'client-{suffix}', prompt,
        submitted_text=prompt, pi_semantic=True, pi_capability_ttl_ms=ttl_ms,
    )
    return user_id, target, turn


def source_for(turn, *, include_redacted=False):
    tag = turn['turn_id'].removeprefix('ct_')
    entries = [
        source_entry(40, f'prep_{tag}', f'prior_{tag}', 'custom', custom_type=PI_PREPARE_ENTRY_TYPE),
        source_entry(41, f'user_{tag}', f'prep_{tag}', 'message', role='user'),
        source_entry(42, f'bind_{tag}', f'user_{tag}', 'custom', custom_type=PI_BIND_ENTRY_TYPE),
    ]
    if include_redacted:
        entries.append(source_entry(43, f'opaque_{tag}', f'bind_{tag}', 'redacted'))
        assistant_order = 44
    else:
        assistant_order = 43
    entries.extend([
        source_entry(
            assistant_order, f'assistant_{tag}', entries[-1]['entry_id'],
            'message', role='assistant', stop_reason='stop',
        ),
        source_entry(
            assistant_order + 1, f'final_{tag}', f'assistant_{tag}',
            'custom', custom_type=PI_FINALIZE_ENTRY_TYPE,
        ),
    ])
    return entries


def envelope(turn, *, event_type='dispatch.finalized', text_blocks=None, entries=None, nonce=None):
    dispatch = turn['pi_dispatch']
    entries = entries if entries is not None else source_for(turn)
    nonce = nonce or f'nonce_{uuid.uuid4().hex}'
    bound = int(time.time() * 1000)
    common = {
        'schema_version': 'magistrate.pi.ownership.v1',
        'event_type': event_type,
        'request_nonce': nonce,
        'dispatch_incarnation': dispatch['dispatch_incarnation'],
        'capability_sha256': dispatch['capability_sha256'],
        'tenant_id': dispatch['tenant_id'],
        'principal_id': dispatch['principal_id'],
        'conversation_id': dispatch['conversation_id'],
        'turn_id': dispatch['turn_id'],
        'assistant_message_id': dispatch['assistant_message_id'],
        'objective_id': dispatch['objective_id'],
        'run_id': dispatch['run_id'],
        'pi_session_id': f'session_{turn["turn_id"].removeprefix("ct_")}',
        'pi_prepare_entry_id': entries[0]['entry_id'] if entries else None,
        'pi_user_entry_id': entries[1]['entry_id'] if entries else None,
        'pi_user_entry_order': entries[1]['order'] if entries else None,
        'pi_user_content_sha256': dispatch['prompt_sha256'],
        'pi_bind_entry_id': entries[2]['entry_id'] if entries else None,
        'bound_at': bound,
        'error_code': None,
    }
    if event_type == 'dispatch.prepared':
        common.update({
            'source_revision': 0, 'finality': 'pending',
            'pi_session_id': None, 'pi_prepare_entry_id': None,
            'pi_user_entry_id': None, 'pi_user_entry_order': None,
            'pi_user_content_sha256': None, 'pi_bind_entry_id': None,
            'bound_at': None, 'source_sequence': [], 'assistant_content': [],
        })
        return common
    if event_type == 'dispatch.failed':
        common.update({
            'source_revision': 0, 'finality': 'failed', 'error_code': 'adapter-failed',
            'pi_session_id': None, 'pi_prepare_entry_id': None,
            'pi_user_entry_id': None, 'pi_user_entry_order': None,
            'pi_user_content_sha256': None, 'pi_bind_entry_id': None,
            'bound_at': None, 'source_sequence': [], 'assistant_content': [],
        })
        return common
    if event_type == 'dispatch.bound':
        entries = entries[:3]
        common.update({
            'source_revision': 1, 'finality': 'pending',
            'source_start_order': entries[0]['order'],
            'source_end_order': entries[-1]['order'],
            'source_cursor': entries[-1]['entry_id'],
            'source_sequence': entries,
            'source_sequence_sha256': canonical_hash(entries),
            'assistant_content': [],
        })
        return common
    blocks = text_blocks if text_blocks is not None else [
        {'index': 0, 'type': 'text', 'text': 'final answer'},
    ]
    visible = [{'index': item['index'], 'text': item['text']} for item in blocks]
    common.update({
        'source_revision': 2,
        'pi_assistant_entry_id': entries[-2]['entry_id'],
        'pi_assistant_entry_order': entries[-2]['order'],
        'pi_finalize_entry_id': entries[-1]['entry_id'],
        'finality': 'final', 'stop_reason': 'stop',
        'finalized_at': bound + 1,
        'source_start_order': entries[0]['order'],
        'source_end_order': entries[-1]['order'],
        'source_cursor': entries[-1]['entry_id'],
        'source_sequence': entries,
        'source_sequence_sha256': canonical_hash(entries),
        'visible_content_sha256': canonical_hash(visible),
        'assistant_content': blocks,
    })
    return common


def apply(user_id, target, turn, payload):
    return apply_pi_ownership_envelope(
        user_id, target, turn['pi_dispatch']['capability'], payload,
    )


def test_prepare_is_atomic_encrypted_and_exactly_replayable():
    user_id, target, turn = create_owned_turn('secrêt prompt\nline two')
    dispatch = turn['pi_dispatch']
    assert dispatch['assistant_message_id'] == turn['assistant_message_id']
    assert dispatch['turn_id'] == turn['turn_id']
    assert dispatch['objective_id'] == turn['objective_id']
    assert dispatch['run_id'] == turn['run_id']
    assert dispatch['state'] == 'prepared'
    assert has_pi_ownership(user_id, target, turn['turn_id'])

    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT * FROM pi_semantic_dispatches WHERE turn_id = ?', (turn['turn_id'],),
        ).fetchone()
        assert 'secrêt prompt' not in row['prompt_enc']
        assert dispatch['capability'] not in row['capability_enc']
        assert row['capability_sha256'] == hashlib.sha256(dispatch['capability'].encode()).hexdigest()
        reservation = conn.execute(
            "SELECT message_id FROM conversation_assistant_reservations WHERE turn_id = ? AND slot = 'primary'",
            (turn['turn_id'],),
        ).fetchone()
        assert reservation['message_id'] == turn['assistant_message_id']
    assert dispatch['capability'].encode() not in Path(db.DB_PATH).read_bytes()
    wal = Path(str(db.DB_PATH) + '-wal')
    if wal.exists():
        assert dispatch['capability'].encode() not in wal.read_bytes()

    replay = record_prompt(
        user_id, target, turn['messages'][0]['client_message_id'], 'secrêt prompt\nline two',
        submitted_text='secrêt prompt\nline two', pi_semantic=True,
    )
    assert replay['pi_dispatch']['dispatch_incarnation'] == dispatch['dispatch_incarnation']
    assert replay['pi_dispatch']['capability'] == dispatch['capability']

    with pytest.raises(PiOwnershipError, match='different text'):
        record_prompt(
            user_id, target, turn['messages'][0]['client_message_id'], 'edited',
            submitted_text='edited', pi_semantic=True,
        )
    assert list_messages(user_id, target)['messages'][0]['text'] == 'secrêt prompt\nline two'


def test_final_recovery_from_prepared_is_atomic_and_preserves_long_exact_unicode():
    user_id, target, turn = create_owned_turn()
    exact = ('  α🙂\n' + ('0123456789' * 15_000) + '\ntrailing spaces   ')
    blocks = [
        {'index': 0, 'type': 'text', 'text': exact[:70_000]},
        {'index': 1, 'type': 'text', 'text': exact[70_000:]},
    ]
    result = apply(user_id, target, turn, envelope(
        turn, text_blocks=blocks, entries=source_for(turn, include_redacted=True),
    ))
    assert result['state'] == 'finalized'
    conversation = list_messages(user_id, target)
    assistants = [message for message in conversation['messages'] if message['role'] == 'assistant']
    assert len(assistants) == 1
    assert assistants[0]['id'] == turn['assistant_message_id']
    assert assistants[0]['text'] == exact
    assert assistants[0]['source'] == 'pi-semantic'
    assert assistants[0]['content_source'] == 'pi-semantic'
    state = get_pi_dispatch(user_id, target, turn['pi_dispatch']['dispatch_incarnation'])
    assert state['pi_assistant_entry_id'].startswith('assistant_')
    assert state['visible_content_sha256'] == result['visible_content_sha256']


def test_gateway_reinitialization_and_source_replay_preserve_identity():
    user_id, target, turn = create_owned_turn('restart-safe prompt')
    incarnation = turn['pi_dispatch']['dispatch_incarnation']
    db.init_db()
    recovered = get_pi_dispatch(user_id, target, incarnation, include_secret=True)
    assert recovered['dispatch_incarnation'] == incarnation
    assert recovered['capability'] == turn['pi_dispatch']['capability']
    applied = apply(user_id, target, turn, envelope(turn, text_blocks=[
        {'index': 0, 'type': 'text', 'text': 'restart-safe exact response'},
    ]))
    assert applied['message_id'] == turn['assistant_message_id']
    [assistant] = [
        message for message in list_messages(user_id, target)['messages']
        if message['role'] == 'assistant'
    ]
    assert assistant['id'] == turn['assistant_message_id']
    assert assistant['text'] == 'restart-safe exact response'


def test_visible_payload_schema_excludes_thinking_tools_and_unknown_fields():
    user_id, target, turn = create_owned_turn()
    payload = envelope(turn)
    payload['assistant_content'].append({
        'index': 1, 'type': 'thinking', 'text': 'private chain of thought',
    })
    with pytest.raises(ValidationError):
        apply(user_id, target, turn, payload)
    payload = envelope(turn)
    payload['assistant_content'][0]['toolCall'] = {'name': 'read', 'args': {'secret': 'x'}}
    with pytest.raises(ValidationError):
        apply(user_id, target, turn, payload)
    payload = envelope(turn, text_blocks=[
        {'index': 0, 'type': 'text', 'text': 'unsafe\x1b[31mcontrol'},
    ])
    with pytest.raises(PiOwnershipError, match='unsafe control'):
        apply(user_id, target, turn, payload)
    assert not any(message['role'] == 'assistant' for message in list_messages(user_id, target)['messages'])


def test_bound_consumes_capability_once_and_final_replay_is_idempotent():
    user_id, target, turn = create_owned_turn()
    bound = envelope(turn, event_type='dispatch.bound')
    first = apply(user_id, target, turn, bound)
    assert first == {
        'status': 'applied', 'state': 'bound',
        'dispatch_incarnation': turn['pi_dispatch']['dispatch_incarnation'],
        'turn_id': turn['turn_id'], 'message_id': turn['assistant_message_id'],
    }
    retried = dict(bound, request_nonce=f'retry_{uuid.uuid4().hex}')
    assert apply(user_id, target, turn, retried)['status'] == 'duplicate'

    changed = json.loads(json.dumps(bound))
    changed['request_nonce'] = f'changed_{uuid.uuid4().hex}'
    changed['pi_session_id'] = 'session_conflict'
    with pytest.raises(PiOwnershipError, match='already consumed'):
        apply(user_id, target, turn, changed)

    final = envelope(turn)
    final['bound_at'] = bound['bound_at']
    accepted = apply(user_id, target, turn, final)
    replay = dict(final, request_nonce=f'final_retry_{uuid.uuid4().hex}')
    duplicate = apply(user_id, target, turn, replay)
    assert accepted['message_revision'] == duplicate['message_revision'] == 1
    assert duplicate['status'] == 'duplicate'

    altered = json.loads(json.dumps(final))
    altered['request_nonce'] = f'altered_{uuid.uuid4().hex}'
    altered['assistant_content'][0]['text'] = 'different'
    altered['visible_content_sha256'] = canonical_hash([{'index': 0, 'text': 'different'}])
    with pytest.raises(PiOwnershipError, match='conflicts'):
        apply(user_id, target, turn, altered)
    assert list_messages(user_id, target)['messages'][-1]['text'] == 'final answer'


def test_pi_source_entries_cannot_be_owned_by_two_canonical_turns():
    user_id, target, first_turn = create_owned_turn()
    apply(user_id, target, first_turn, envelope(first_turn))
    _, _, second_turn = create_owned_turn(user_id=user_id)
    reused = envelope(second_turn, entries=source_for(first_turn))
    reused['pi_session_id'] = f'session_{first_turn["turn_id"].removeprefix("ct_")}'
    with pytest.raises(PiOwnershipError, match='already owned'):
        apply(user_id, target, second_turn, reused)
    assert get_pi_dispatch(
        user_id, target, second_turn['pi_dispatch']['dispatch_incarnation'],
    )['state'] == 'prepared'


def test_cross_principal_identity_and_capability_substitution_are_rejected():
    user_id, target, turn = create_owned_turn()
    payload = envelope(turn, event_type='dispatch.bound')
    with pytest.raises(LookupError):
        apply('another-principal', target, turn, payload)
    with pytest.raises(PiOwnershipError, match='capability'):
        apply_pi_ownership_envelope(user_id, target, 'pic_' + 'A' * 43, payload)
    payload['tenant_id'] = 'another-tenant'
    with pytest.raises(PiOwnershipError, match='identity'):
        apply(user_id, target, turn, payload)
    assert get_pi_dispatch(user_id, target, turn['pi_dispatch']['dispatch_incarnation'])['state'] == 'prepared'


@pytest.mark.parametrize('mutation,match', [
    ('gap', 'gapped'),
    ('parent', 'parent chain'),
    ('sequence_hash', 'sequence hash'),
    ('content_hash', 'visible-content hash'),
    ('cursor', 'cursor'),
    ('other_user', 'user boundary'),
    ('user_hash', 'exact Pi user entry'),
    ('duplicate_id', 'identity is ambiguous'),
    ('ambiguous_assistant', 'final assistant entry'),
    ('non_final', 'finalized Pi assistant entry'),
])
def test_malformed_final_evidence_fails_without_partial_canonical_write(mutation, match):
    user_id, target, turn = create_owned_turn()
    payload = envelope(turn)
    if mutation == 'gap':
        payload['source_sequence'][3]['order'] += 1
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    elif mutation == 'parent':
        payload['source_sequence'][3]['parent_id'] = 'unrelated_parent'
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    elif mutation == 'sequence_hash':
        payload['source_sequence_sha256'] = '0' * 64
    elif mutation == 'content_hash':
        payload['visible_content_sha256'] = '0' * 64
    elif mutation == 'cursor':
        payload['source_cursor'] = payload['source_sequence'][0]['entry_id']
    elif mutation == 'other_user':
        extra = source_entry(
            42, 'another_user_entry', payload['source_sequence'][1]['entry_id'],
            'message', role='user',
        )
        payload['source_sequence'].insert(2, extra)
        for index in range(3, len(payload['source_sequence'])):
            payload['source_sequence'][index]['order'] += 1
            payload['source_sequence'][index]['parent_id'] = payload['source_sequence'][index - 1]['entry_id']
        payload['source_end_order'] = payload['source_sequence'][-1]['order']
        payload['pi_assistant_entry_order'] = payload['source_sequence'][-2]['order']
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    elif mutation == 'user_hash':
        payload['pi_user_content_sha256'] = '0' * 64
    elif mutation == 'duplicate_id':
        payload['source_sequence'][3]['entry_id'] = payload['source_sequence'][1]['entry_id']
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    elif mutation == 'ambiguous_assistant':
        final_marker = payload['source_sequence'].pop()
        owned_assistant = payload['source_sequence'][-1]
        extra = source_entry(
            owned_assistant['order'] + 1, f"extra_{uuid.uuid4().hex}",
            owned_assistant['entry_id'], 'message', role='assistant', stop_reason='stop',
        )
        final_marker['order'] = extra['order'] + 1
        final_marker['parent_id'] = extra['entry_id']
        payload['source_sequence'].extend([extra, final_marker])
        payload['source_end_order'] = final_marker['order']
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    elif mutation == 'non_final':
        payload['source_sequence'][-2]['stop_reason'] = 'length'
        payload['source_sequence_sha256'] = canonical_hash(payload['source_sequence'])
    with pytest.raises(PiOwnershipError, match=match):
        apply(user_id, target, turn, payload)
    state = get_pi_dispatch(user_id, target, turn['pi_dispatch']['dispatch_incarnation'])
    assert state['state'] == 'prepared'
    assert [message['role'] for message in list_messages(user_id, target)['messages']] == ['user']


def test_binding_prefix_cannot_change_at_finalization():
    user_id, target, turn = create_owned_turn()
    bound = envelope(turn, event_type='dispatch.bound')
    apply(user_id, target, turn, bound)
    final = envelope(turn)
    final['bound_at'] = bound['bound_at']
    final['source_sequence'][1]['parent_id'] = f'other_{uuid.uuid4().hex}'
    final['source_sequence_sha256'] = canonical_hash(final['source_sequence'])
    with pytest.raises(PiOwnershipError, match='parent chain|binding prefix'):
        apply(user_id, target, turn, final)
    assert get_pi_dispatch(user_id, target, turn['pi_dispatch']['dispatch_incarnation'])['state'] == 'bound'


def test_finalization_cannot_change_accepted_binding_time():
    user_id, target, turn = create_owned_turn()
    bound = envelope(turn, event_type='dispatch.bound')
    apply(user_id, target, turn, bound)
    final = envelope(turn)
    final['bound_at'] = bound['bound_at'] + 1
    with pytest.raises(PiOwnershipError, match='binding prefix'):
        apply(user_id, target, turn, final)
    assert get_pi_dispatch(
        user_id, target, turn['pi_dispatch']['dispatch_incarnation'],
    )['state'] == 'bound'


def test_capability_must_bind_before_expiry_and_cannot_be_renewed_by_replay():
    user_id, target, turn = create_owned_turn(ttl_ms=5_000)
    payload = envelope(turn, event_type='dispatch.bound')
    payload['bound_at'] = turn['pi_dispatch']['expires_at'] + 1
    with pytest.raises(PiOwnershipError, match='expired'):
        apply(user_id, target, turn, payload)
    replay = record_prompt(
        user_id, target, turn['messages'][0]['client_message_id'], 'exact prompt',
        submitted_text='exact prompt', pi_semantic=True, pi_capability_ttl_ms=600_000,
    )
    assert replay['pi_dispatch']['expires_at'] == turn['pi_dispatch']['expires_at']


def test_prepare_immediately_disables_terminal_and_structured_ingestion():
    user_id, target, turn = create_owned_turn('owned question')
    assert not has_terminal_fallback_candidates(user_id, target)
    rows = [
        {'role': 'user', 'text': 'owned question', 'kind': 'conversation'},
        {'role': 'assistant', 'text': 'forged terminal result', 'kind': 'conversation'},
    ]
    assert ingest_terminal_rows(user_id, target, rows) == []
    assert [message['role'] for message in list_messages(user_id, target)['messages']] == ['user']

    event = TypeAdapter(MagiEventContract).validate_python({
        'schema_version': 'magi.event.v1', 'event_id': f'evt_{uuid.uuid4().hex}',
        'turn_id': turn['turn_id'], 'message_id': turn['assistant_message_id'],
        'event_type': 'assistant.completed', 'revision': 1,
        'response': {
            'schema_version': 'magi.response.v1',
            'blocks': [{
                'type': 'paragraph', 'block_id': 'other-producer',
                'content': [{'type': 'text', 'text': 'other producer'}],
            }],
        },
    })
    with pytest.raises(MagiEventConflict, match='owned'):
        apply_magi_event(user_id, target, event)
    with pytest.raises(MagiEventConflict, match='owned'):
        reserve_assistant_message(
            user_id, target, turn['turn_id'], 'owned-progress-key', kind='progress',
        )


def test_pi_prepare_cannot_retrofit_a_previously_dispatched_unowned_turn():
    suffix = uuid.uuid4().hex
    user_id, target, client_id = f'pending-upgrade-{suffix}', 'captain', f'client-{suffix}'
    legacy = record_prompt(user_id, target, client_id, 'already dispatched')
    with pytest.raises(PiOwnershipError, match='first created'):
        record_prompt(
            user_id, target, client_id, 'already dispatched', pi_semantic=True,
        )
    assert not has_pi_ownership(user_id, target, legacy['turn_id'])


def test_pi_prepare_cannot_retrofit_or_overwrite_an_answered_legacy_turn():
    suffix = uuid.uuid4().hex
    user_id, target, client_id = f'upgrade-{suffix}', 'captain', f'client-{suffix}'
    legacy = record_prompt(user_id, target, client_id, 'already answered')
    ingest_terminal_rows(user_id, target, [
        {'role': 'user', 'text': 'already answered', 'kind': 'conversation'},
        {'role': 'assistant', 'text': 'accepted legacy response', 'kind': 'conversation'},
    ])
    with pytest.raises(PiOwnershipError, match='first created'):
        record_prompt(
            user_id, target, client_id, 'already answered', pi_semantic=True,
        )
    assert not has_pi_ownership(user_id, target, legacy['turn_id'])
    [assistant] = [
        item for item in list_messages(user_id, target)['messages']
        if item['role'] == 'assistant'
    ]
    assert assistant['id'] == legacy['assistant_message_id']
    assert assistant['text'] == 'accepted legacy response'
    assert assistant['content_source'] == 'terminal-fallback'


def test_existing_structured_owner_cannot_be_replaced_by_pi_prepare():
    suffix = uuid.uuid4().hex
    user_id, target = f'structured-first-{suffix}', 'captain'
    turn = record_prompt(user_id, target, f'client-{suffix}', 'structured first')
    started = TypeAdapter(MagiEventContract).validate_python({
        'schema_version': 'magi.event.v1', 'event_type': 'assistant.started',
        'event_id': f'evt_{suffix}', 'turn_id': turn['turn_id'],
        'message_id': turn['assistant_message_id'], 'revision': 1,
    })
    apply_magi_event(user_id, target, started)
    with pytest.raises(PiOwnershipError, match='first created'):
        record_prompt(
            user_id, target, f'client-{suffix}', 'structured first',
            pi_semantic=True,
        )
    assert not has_pi_ownership(user_id, target, turn['turn_id'])


def test_legacy_unowned_turn_keeps_terminal_fallback():
    suffix = uuid.uuid4().hex
    user_id = f'legacy-{suffix}'
    target = 'captain'
    record_prompt(user_id, target, f'client-{suffix}', 'legacy question')
    assert has_terminal_fallback_candidates(user_id, target)
    changed = ingest_terminal_rows(user_id, target, [
        {'role': 'user', 'text': 'legacy question', 'kind': 'conversation'},
        {'role': 'assistant', 'text': 'legacy terminal answer', 'kind': 'conversation'},
    ])
    assert changed and changed[-1]['content_source'] == 'terminal-fallback'


def test_cancelled_owned_turn_is_frozen_before_finalization():
    user_id, target, turn = create_owned_turn()
    set_turn_status(
        user_id, target, turn['messages'][0]['client_message_id'], 'cancelled',
    )
    final = envelope(turn)
    discarded = apply(user_id, target, turn, final)
    assert discarded['state'] == 'failed'
    assert discarded['status'] == 'discarded'
    replay = apply(
        user_id, target, turn,
        dict(final, request_nonce=f'discard_retry_{uuid.uuid4().hex}'),
    )
    assert replay['status'] == 'duplicate'
    assert [message['role'] for message in list_messages(user_id, target)['messages']] == ['user']


def test_recovery_retains_open_and_unacknowledged_dispatches_without_plaintext_leaks():
    user_id, target, turn = create_owned_turn('never expose this plaintext')
    recoverable = {item['dispatch_incarnation']: item for item in get_recoverable_dispatches()}
    item = recoverable[turn['pi_dispatch']['dispatch_incarnation']]
    assert item['prompt'] == 'never expose this plaintext'
    assert item['capability'] == turn['pi_dispatch']['capability']
    accepted = apply(user_id, target, turn, envelope(turn))
    assert turn['pi_dispatch']['dispatch_incarnation'] in {
        item['dispatch_incarnation'] for item in get_recoverable_dispatches()
    }
    with pytest.raises(PiOwnershipError, match='different canonical evidence'):
        mark_pi_adapter_acknowledged(
            user_id, target, turn['pi_dispatch']['dispatch_incarnation'], '0' * 64,
        )
    mark_pi_adapter_acknowledged(
        user_id, target, turn['pi_dispatch']['dispatch_incarnation'],
        accepted['accepted_envelope_sha256'],
    )
    assert turn['pi_dispatch']['dispatch_incarnation'] not in {
        item['dispatch_incarnation'] for item in get_recoverable_dispatches()
    }
    with sqlite3.connect(db.DB_PATH) as conn:
        erased = conn.execute(
            '''SELECT capability_enc, prompt_enc FROM pi_semantic_dispatches
               WHERE dispatch_incarnation = ?''',
            (turn['pi_dispatch']['dispatch_incarnation'],),
        ).fetchone()
    assert erased == ('', '')


def test_reset_waits_for_adapter_receipt_before_removing_ownership():
    user_id, target, turn = create_owned_turn()
    with pytest.raises(MagiEventConflict, match='adapter receipt'):
        reset_conversation(user_id, target)
    accepted = apply(user_id, target, turn, envelope(turn))
    with pytest.raises(MagiEventConflict, match='adapter receipt'):
        reset_conversation(user_id, target)
    mark_pi_adapter_acknowledged(
        user_id, target, turn['pi_dispatch']['dispatch_incarnation'],
        accepted['accepted_envelope_sha256'],
    )
    assert reset_conversation(user_id, target)['status'] == 'reset'


def test_failure_is_idempotent_and_never_accepts_visible_content():
    user_id, target, turn = create_owned_turn()
    failed = envelope(turn, event_type='dispatch.failed')
    assert apply(user_id, target, turn, failed)['state'] == 'failed'
    duplicate = apply(
        user_id, target, turn,
        dict(failed, request_nonce=f'retry_{uuid.uuid4().hex}'),
    )
    assert duplicate['status'] == 'duplicate'
    leaked = dict(failed, request_nonce=f'leaked_{uuid.uuid4().hex}')
    leaked['assistant_content'] = [{'index': 0, 'type': 'text', 'text': 'not accepted'}]
    with pytest.raises(ValidationError):
        apply(user_id, target, turn, leaked)
    with pytest.raises(PiOwnershipError, match='cannot finalize'):
        apply(user_id, target, turn, envelope(turn))
    conversation = list_messages(user_id, target)
    assert conversation['messages'][0]['lifecycle_state'] == 'failed'
    assert len(conversation['messages']) == 1


def test_python_and_adapter_share_canonical_hash_vectors():
    vector_path = Path(__file__).parents[2] / 'pi-extension/tests/protocol-vector.json'
    vector = json.loads(vector_path.read_text())
    assert canonical_hash(vector['source_sequence']) == vector['source_sequence_sha256']
    assert canonical_hash(vector['visible_blocks']) == vector['visible_content_sha256']
    normalized = PiOwnershipEnvelope.model_validate(
        vector['ownership_envelope'],
    ).model_dump(mode='json')
    normalized.pop('request_nonce')
    assert canonical_hash(normalized) == vector['semantic_envelope_sha256']


def test_pi_channel_modules_have_hard_architectural_source_prohibition():
    root = Path(__file__).parents[2]
    files = [
        root / 'gateway/app/pi_ownership.py',
        root / 'gateway/app/pi_adapter_ipc.py',
        root / 'pi-extension/index.ts',
    ]
    forbidden = {
        'herdr_client', 'firstmate_client', 'HerdrClient', 'FirstmateClient',
        'read_agent_output', 'read_typed_rows', 'parse_agent_history',
        'fm-fleet-snapshot', 'getcwd', 'getmtime', 'st_mtime', 'pane_id',
    }
    for path in files:
        source = path.read_text()
        assert not any(token in source for token in forbidden), path
    tree = ast.parse(files[0].read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith(('app.herdr', 'app.firstmate')) for name in imports)
