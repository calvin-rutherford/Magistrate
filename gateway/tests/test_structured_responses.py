import copy
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app import db
from app.auth import issue_session
from app.contracts import MAGI_MAX_RESPONSE_BYTES, MagiEventContract, MagiResponseV1
from app.conversation_store import (
    MagiEventConflict,
    apply_magi_event,
    get_turn_lifecycle,
    ingest_terminal_rows,
    list_messages,
    record_primary_reply,
    record_prompt,
    replay_messages,
    reserve_assistant_message,
    reset_conversation,
    set_turn_status,
)
from app.main import app, herdr_client
from conftest import TEST_HEADERS, TEST_SESSION_TOKEN


USER = 'structured-response-user'
TARGET = 'captain'
EVENT_ADAPTER = TypeAdapter(MagiEventContract)


def rich_response():
    return {
        'schema_version': 'magi.response.v1',
        'blocks': [
            {
                'type': 'heading', 'block_id': 'overview', 'level': 1,
                'content': [
                    {'type': 'text', 'text': 'A '},
                    {'type': 'strong', 'text': 'structured'},
                    {'type': 'text', 'text': ' response with '},
                    {'type': 'emphasis', 'text': 'native'},
                    {'type': 'text', 'text': ' rendering and '},
                    {'type': 'inline_code', 'text': 'stable_ids'},
                    {'type': 'text', 'text': '. See '},
                    {'type': 'link', 'text': 'the guide', 'url': 'https://example.com/guide?q=1#safe'},
                ],
            },
            {
                'type': 'paragraph', 'block_id': 'detail',
                'content': [{'type': 'text', 'text': 'Canonical plain text remains available to legacy clients.'}],
            },
            {
                'type': 'list', 'block_id': 'steps', 'style': 'ordered',
                'items': [
                    [{'type': 'text', 'text': 'Validate the closed contract.'}],
                    [{'type': 'strong', 'text': 'Render'}, {'type': 'text', 'text': ' native blocks.'}],
                ],
            },
            {'type': 'code', 'block_id': 'sample', 'language': 'python', 'code': 'print("safe")'},
            {
                'type': 'quote', 'block_id': 'quote',
                'content': [{'type': 'text', 'text': 'Unknown instructions fail closed.'}],
            },
            {'type': 'divider', 'block_id': 'end'},
        ],
        'actions': [
            {'type': 'open_url', 'action_id': 'docs', 'label': 'Open documentation', 'url': 'https://example.com/docs'},
        ],
    }


def event(event_type, event_id, turn, revision, **fields):
    return EVENT_ADAPTER.validate_python({
        'schema_version': 'magi.event.v1',
        'event_type': event_type,
        'event_id': event_id,
        'turn_id': turn['turn_id'],
        'message_id': turn['assistant_message_id'],
        'revision': revision,
        **fields,
    })


def test_response_contract_accepts_only_closed_bounded_native_blocks():
    parsed = MagiResponseV1.model_validate(rich_response())
    assert [block.type for block in parsed.blocks] == [
        'heading', 'paragraph', 'list', 'code', 'quote', 'divider',
    ]

    malformed = []
    missing_version = rich_response()
    missing_version.pop('schema_version')
    malformed.append(missing_version)
    unknown_block = rich_response()
    unknown_block['blocks'][0] = {'type': 'html', 'block_id': 'bad', 'html': '<script />'}
    malformed.append(unknown_block)
    unknown_node = rich_response()
    unknown_node['blocks'][0]['content'][0] = {'type': 'image', 'src': 'https://example.com/x'}
    malformed.append(unknown_node)
    unsafe_link = rich_response()
    unsafe_link['blocks'][0]['content'][-1]['url'] = 'javascript:alert(1)'
    malformed.append(unsafe_link)
    credential_link = rich_response()
    credential_link['actions'][0]['url'] = 'https://user:secret@example.com/'
    malformed.append(credential_link)
    whitespace_link = rich_response()
    whitespace_link['actions'][0]['url'] = 'https://example.com/a b'
    malformed.append(whitespace_link)
    invalid_unicode = rich_response()
    invalid_unicode['blocks'][1]['content'][0]['text'] = '\ud800'
    malformed.append(invalid_unicode)
    duplicate_id = rich_response()
    duplicate_id['blocks'][1]['block_id'] = 'overview'
    malformed.append(duplicate_id)
    extra_field = rich_response()
    extra_field['blocks'][0]['component'] = 'AdminPanel'
    malformed.append(extra_field)
    oversized_code = rich_response()
    oversized_code['blocks'][3]['code'] = 'x' * 65_537
    malformed.append(oversized_code)
    too_many_inline = rich_response()
    too_many_inline['blocks'] = [{
        'type': 'list', 'block_id': 'wide-list', 'style': 'unordered',
        'items': [
            [{'type': 'text', 'text': 'x'} for _ in range(128)]
            for _ in range(9)
        ],
    }]
    malformed.append(too_many_inline)

    for candidate in malformed:
        with pytest.raises(ValidationError):
            MagiResponseV1.model_validate(candidate)


def test_event_contract_is_discriminated_strict_and_bounded():
    base = {
        'schema_version': 'magi.event.v1', 'event_type': 'assistant.started',
        'event_id': 'evt-1', 'turn_id': 'ct_turn', 'message_id': 'cm_message', 'revision': 1,
    }
    assert EVENT_ADAPTER.validate_python(base).event_type == 'assistant.started'
    for patch in [
        {'schema_version': None},
        {'revision': '1'},
        {'event_type': 'assistant.magic'},
        {'html': '<button>run</button>'},
    ]:
        with pytest.raises(ValidationError):
            EVENT_ADAPTER.validate_python({**base, **patch})
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python({
            **base, 'event_type': 'assistant.block.upsert', 'revision': 2,
            'block_index': 0,
            'block': {'type': 'code', 'block_id': 'large', 'code': 'x' * MAGI_MAX_RESPONSE_BYTES},
        })


def test_event_replay_updates_one_stable_message_and_structured_completion_wins():
    reset_conversation(USER, TARGET)
    turn = record_prompt(USER, TARGET, 'u-structured-main', 'Give me the structured report')
    assistant_id = turn['assistant_message_id']

    started = event('assistant.started', 'evt-main-1', turn, 1)
    assert apply_magi_event(USER, TARGET, started)['status'] == 'applied'
    # Once semantic evidence starts, terminal rows are fallback-only and cannot
    # mint or overwrite the primary assistant record.
    assert ingest_terminal_rows(USER, TARGET, [
        {'role': 'user', 'kind': 'conversation', 'text': 'Give me the structured report'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'terminal race should not win'},
    ], response_complete=True) == []

    first = event(
        'assistant.block.upsert', 'evt-main-2', turn, 2, block_index=0,
        block={
            'type': 'heading', 'block_id': 'overview', 'level': 2,
            'content': [{'type': 'text', 'text': 'Draft overview'}],
        },
    )
    applied = apply_magi_event(USER, TARGET, first)
    assert applied['message']['id'] == assistant_id
    assert applied['message']['structured_revision'] == 2
    canonical_revision = applied['message']['revision']

    duplicate = apply_magi_event(USER, TARGET, first)
    assert duplicate['status'] == 'duplicate'
    assert duplicate['message']['revision'] == canonical_revision

    update = event(
        'assistant.block.upsert', 'evt-main-3', turn, 3, block_index=0,
        block={
            'type': 'heading', 'block_id': 'overview', 'level': 1,
            'content': [{'type': 'text', 'text': 'Updated overview'}],
        },
    )
    assert apply_magi_event(USER, TARGET, update)['message']['id'] == assistant_id
    second = event(
        'assistant.block.upsert', 'evt-main-4', turn, 4, block_index=1,
        block={
            'type': 'paragraph', 'block_id': 'temporary',
            'content': [{'type': 'text', 'text': 'Explicitly removable correction.'}],
        },
    )
    apply_magi_event(USER, TARGET, second)
    apply_magi_event(USER, TARGET, event(
        'assistant.block.remove', 'evt-main-5', turn, 5, block_id='temporary',
    ))

    completed = event(
        'assistant.completed', 'evt-main-6', turn, 6, response=rich_response(),
    )
    final = apply_magi_event(USER, TARGET, completed)
    assert final['turn_status'] == 'answered'
    assert final['message']['structured_content'] == rich_response()
    assert final['message']['content_source'] == 'structured'
    assert final['message']['structured_revision'] == 6
    assert final['message']['text'].startswith('A structured response with native rendering')
    assert '# ' not in final['message']['text']

    # Both synchronous text and later snapshots are permanently subordinate to
    # the authoritative structured completion.
    assert record_primary_reply(USER, TARGET, turn['turn_id'], 'late plain reply') == []
    assert ingest_terminal_rows(USER, TARGET, [
        {'role': 'user', 'kind': 'conversation', 'text': 'Give me the structured report'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'later terminal replacement'},
    ], response_complete=True) == []
    messages = list_messages(USER, TARGET)['messages']
    assistant = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert len(assistant) == 1
    assert assistant[0]['id'] == assistant_id
    assert assistant[0]['structured_content'] == rich_response()


def test_event_identity_order_and_explicit_remove_fail_closed():
    reset_conversation(USER, TARGET)
    turn = record_prompt(USER, TARGET, 'u-structured-conflicts', 'Ordering')
    wrong_message = copy.copy(turn)
    wrong_message['assistant_message_id'] = 'cm_wrong'
    with pytest.raises(MagiEventConflict, match='message id'):
        apply_magi_event(USER, TARGET, event('assistant.started', 'evt-wrong-id', wrong_message, 1))
    with pytest.raises(MagiEventConflict, match='first event'):
        apply_magi_event(USER, TARGET, event(
            'assistant.block.upsert', 'evt-not-started', turn, 1, block_index=0,
            block={'type': 'paragraph', 'block_id': 'p', 'content': [{'type': 'text', 'text': 'x'}]},
        ))

    apply_magi_event(USER, TARGET, event('assistant.started', 'evt-conflict-1', turn, 1))
    with pytest.raises(MagiEventConflict, match='Expected event revision 2'):
        apply_magi_event(USER, TARGET, event(
            'assistant.block.upsert', 'evt-gap', turn, 3, block_index=0,
            block={'type': 'paragraph', 'block_id': 'p', 'content': [{'type': 'text', 'text': 'x'}]},
        ))
    block = event(
        'assistant.block.upsert', 'evt-conflict-2', turn, 2, block_index=0,
        block={'type': 'paragraph', 'block_id': 'p', 'content': [{'type': 'text', 'text': 'x'}]},
    )
    apply_magi_event(USER, TARGET, block)
    altered = event(
        'assistant.block.upsert', 'evt-conflict-2', turn, 2, block_index=0,
        block={'type': 'paragraph', 'block_id': 'p', 'content': [{'type': 'text', 'text': 'changed'}]},
    )
    with pytest.raises(MagiEventConflict, match='event id'):
        apply_magi_event(USER, TARGET, altered)
    with pytest.raises(MagiEventConflict, match='final response block'):
        apply_magi_event(USER, TARGET, event(
            'assistant.block.remove', 'evt-conflict-3', turn, 3, block_id='p',
        ))


def test_failed_and_cancelled_are_explicit_terminal_lifecycle_events():
    reset_conversation(USER, TARGET)
    failed_turn = record_prompt(USER, TARGET, 'u-structured-failed', 'Fail')
    apply_magi_event(USER, TARGET, event('assistant.started', 'evt-failed-1', failed_turn, 1))
    failed = apply_magi_event(USER, TARGET, event(
        'assistant.failed', 'evt-failed-2', failed_turn, 2,
        error_code='provider.unavailable', error_message='Provider unavailable.',
    ))
    assert failed['turn_status'] == 'failed'
    with pytest.raises(MagiEventConflict, match='terminal'):
        apply_magi_event(USER, TARGET, event(
            'assistant.completed', 'evt-failed-3', failed_turn, 3, response=rich_response(),
        ))

    cancelled_turn = record_prompt(USER, TARGET, 'u-structured-cancelled', 'Cancel')
    apply_magi_event(USER, TARGET, event('assistant.started', 'evt-cancelled-1', cancelled_turn, 1))
    cancelled = apply_magi_event(USER, TARGET, event(
        'assistant.cancelled', 'evt-cancelled-2', cancelled_turn, 2,
    ))
    assert cancelled['turn_status'] == 'cancelled'


def test_one_objective_can_emit_ordered_progress_messages_before_its_stable_final_reply():
    owner = 'structured-multi-message-owner'
    reset_conversation(owner, TARGET)
    turn = record_prompt(owner, TARGET, 'u-multi-message', 'Carry out the durable objective')
    assert turn['objective_id'].startswith('obj_')
    assert turn['run_id'].startswith('run_')
    assert len({turn['turn_id'], turn['objective_id'], turn['run_id']}) == 3

    first = reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-step-one', kind='progress',
    )
    retry = reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-step-one', kind='progress',
    )
    second = reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-step-two', kind='progress',
    )
    assert retry['status'] == 'existing'
    assert retry['message_id'] == first['message_id']
    assert [first['ordinal'], second['ordinal']] == [1, 2]

    progress_one = {**turn, 'assistant_message_id': first['message_id']}
    progress_two = {**turn, 'assistant_message_id': second['message_id']}
    apply_magi_event(owner, TARGET, event('assistant.started', 'evt-progress-1-start', progress_one, 1))
    apply_magi_event(owner, TARGET, event(
        'assistant.completed', 'evt-progress-1-done', progress_one, 2,
        response=MagiResponseV1.model_validate({
            'schema_version': 'magi.response.v1',
            'blocks': [{'type': 'paragraph', 'block_id': 'p1', 'content': [{'type': 'text', 'text': 'First durable progress.'}]}],
        }),
    ))
    assert get_turn_lifecycle(owner, TARGET, turn['turn_id'])['state'] == 'active'
    # Once any semantic stream owns the objective, terminal/synchronous prose
    # cannot race into the primary slot.
    assert record_primary_reply(owner, TARGET, turn['turn_id'], 'unsafe terminal fallback') == []

    apply_magi_event(owner, TARGET, event('assistant.started', 'evt-progress-2-start', progress_two, 1))
    apply_magi_event(owner, TARGET, event(
        'assistant.completed', 'evt-progress-2-done', progress_two, 2,
        response=MagiResponseV1.model_validate({
            'schema_version': 'magi.response.v1',
            'blocks': [{'type': 'paragraph', 'block_id': 'p2', 'content': [{'type': 'text', 'text': 'Second durable progress.'}]}],
        }),
    ))
    failed_progress = reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-render-failure', kind='progress',
    )
    failed_stream = {**turn, 'assistant_message_id': failed_progress['message_id']}
    apply_magi_event(owner, TARGET, event('assistant.started', 'evt-progress-failed-start', failed_stream, 1))
    failed_update = apply_magi_event(owner, TARGET, event(
        'assistant.failed', 'evt-progress-failed-done', failed_stream, 2,
        error_code='progress.render-failed', error_message='A non-final update failed.',
    ))
    assert failed_update['lifecycle_state'] == 'active', 'a failed optional update is not a failed objective'
    apply_magi_event(owner, TARGET, event('assistant.started', 'evt-primary-start', turn, 1))
    completed = apply_magi_event(owner, TARGET, event(
        'assistant.completed', 'evt-primary-done', turn, 2, response=rich_response(),
    ))
    assert completed['lifecycle_state'] == 'completed'
    assert completed['objective_id'] == turn['objective_id']
    assert completed['run_id'] == turn['run_id']

    messages = list_messages(owner, TARGET)['messages']
    assistant = [message for message in messages if message['role'] == 'assistant']
    assert [message['assistant_kind'] for message in assistant] == ['progress', 'progress', 'response']
    assert [message['text'] for message in assistant[:2]] == [
        'First durable progress.', 'Second durable progress.',
    ]
    assert all(message['objective_id'] == turn['objective_id'] for message in assistant)
    assert all(message['run_id'] == turn['run_id'] for message in assistant)
    assert reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-step-one', kind='progress',
    )['message_id'] == first['message_id']
    with pytest.raises(MagiEventConflict, match='terminal'):
        reserve_assistant_message(
            owner, TARGET, turn['turn_id'], 'late-new-message', kind='progress',
        )

    before_restart = [(row['id'], row['revision'], row['sequence_index']) for row in messages]
    db.init_db()
    replayed = replay_messages(owner, TARGET, after=-1, limit=20)['messages']
    assert [(row['id'], row['revision'], row['sequence_index']) for row in replayed] == before_restart


def test_keyed_awaiting_user_lifecycle_is_replayed_without_inferred_decision_outcome():
    owner = 'structured-awaiting-owner'
    reset_conversation(owner, TARGET)
    turn = record_prompt(owner, TARGET, 'u-awaiting-user', 'Ask only if a choice is required')
    apply_magi_event(owner, TARGET, event('assistant.started', 'evt-awaiting-start', turn, 1))
    waiting = apply_magi_event(owner, TARGET, event(
        'assistant.awaiting_user', 'evt-awaiting-key', turn, 2,
        decision_key='release-channel', prompt='Choose beta or production.',
    ))
    assert waiting['lifecycle_state'] == 'awaiting-user'
    assert waiting['decision_key'] == 'release-channel'
    inspected = get_turn_lifecycle(owner, TARGET, turn['turn_id'])
    assert inspected['state'] == 'awaiting-user'
    assert inspected['decision_key'] == 'release-channel'
    assert inspected['objective_id'] == turn['objective_id']
    assert inspected['run_id'] == turn['run_id']
    set_turn_status(
        owner, TARGET, 'u-awaiting-user', 'failed', terminal_fallback_only=True,
    )
    assert get_turn_lifecycle(owner, TARGET, turn['turn_id'])['state'] == 'awaiting-user'

    # Independently delayed optional progress cannot resolve or clear the exact
    # objective-level decision merely because its own message stream finishes.
    progress = reserve_assistant_message(
        owner, TARGET, turn['turn_id'], 'progress-after-decision', kind='progress',
    )
    progress_turn = {**turn, 'assistant_message_id': progress['message_id']}
    apply_magi_event(owner, TARGET, event(
        'assistant.started', 'evt-progress-after-decision-1', progress_turn, 1,
    ))
    delayed = apply_magi_event(owner, TARGET, event(
        'assistant.completed', 'evt-progress-after-decision-2', progress_turn, 2,
        response=MagiResponseV1.model_validate({
            'schema_version': 'magi.response.v1',
            'blocks': [{'type': 'paragraph', 'block_id': 'late-progress', 'content': [
                {'type': 'text', 'text': 'Background validation is still running.'},
            ]}],
        }),
    ))
    assert delayed['lifecycle_state'] == 'awaiting-user'
    assert delayed['decision_key'] == 'release-channel'
    assert {message['lifecycle_state'] for message in replay_messages(owner, TARGET, after=-1)['messages']} == {'awaiting-user'}
    db.init_db()
    assert get_turn_lifecycle(owner, TARGET, turn['turn_id'])['state'] == 'awaiting-user'


def test_schema_migration_and_event_ledger_are_additive(monkeypatch, tmp_path):
    legacy_db = tmp_path / 'canonical-before-magi.db'
    with sqlite3.connect(legacy_db) as conn:
        conn.execute('''CREATE TABLE conversations (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, target TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(user_id, target)
        )''')
        conn.execute('''CREATE TABLE conversation_turns (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, client_message_id TEXT,
            prompt_key TEXT, status TEXT NOT NULL, sequence_index INTEGER NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(conversation_id, client_message_id)
        )''')
        conn.execute('''CREATE TABLE conversation_messages (
            id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, type TEXT NOT NULL, slot TEXT NOT NULL, text TEXT NOT NULL,
            visible_in_chat INTEGER NOT NULL, sequence_index INTEGER NOT NULL,
            revision INTEGER NOT NULL, source TEXT NOT NULL, attachments_json TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(turn_id, slot)
        )''')
        conn.execute("INSERT INTO conversations VALUES ('cv_old', 'legacy-user', 'captain', 1756000000000, 1756000000000)")
        conn.execute("INSERT INTO conversation_turns VALUES ('ct_old', 'cv_old', 'u-old', 'old prompt', 'answered', 0, 1756000000000, 1756000000000)")
        conn.execute("""INSERT INTO conversation_messages VALUES (
            'cm_old_reply', 'ct_old', 'cv_old', 'assistant', 'conversation', 'primary',
            'Existing terminal reply.', 1, 999, 3, 'terminal', '[]', 1756000000000, 1756000000000
        )""")
    monkeypatch.setattr(db, 'DB_PATH', str(legacy_db))

    db.init_db()

    with sqlite3.connect(legacy_db) as conn:
        turn_columns = {row[1] for row in conn.execute('PRAGMA table_info(conversation_turns)')}
        message_columns = {row[1] for row in conn.execute('PRAGMA table_info(conversation_messages)')}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        turn_identity = conn.execute(
            '''SELECT assistant_message_id, objective_id, run_id, lifecycle_state
               FROM conversation_turns WHERE id = ?''', ('ct_old',),
        ).fetchone()
        fallback = conn.execute(
            '''SELECT text, revision, content_source, structured_content_json, structured_revision
               FROM conversation_messages WHERE id = ?''', ('cm_old_reply',),
        ).fetchone()
    assert {'assistant_message_id', 'objective_id', 'run_id', 'lifecycle_state'} <= turn_columns
    assert {'content_source', 'structured_content_json', 'structured_revision', 'assistant_kind'} <= message_columns
    assert {'magi_response_events', 'magi_additional_response_events', 'conversation_assistant_reservations', 'activity_records', 'activity_sources', 'activity_changes'} <= tables
    assert turn_identity[0] == 'cm_old_reply'
    assert turn_identity[1].startswith('obj_legacy_')
    assert turn_identity[2].startswith('run_legacy_')
    assert turn_identity[3] == 'completed'
    assert fallback == ('Existing terminal reply.', 3, 'terminal-fallback', None, None)


def test_websocket_broadcasts_the_validated_canonical_document(monkeypatch):
    client = TestClient(app)
    reset_conversation('default_user', TARGET)
    turn = record_prompt('default_user', TARGET, 'u-structured-socket', 'Socket document')
    apply_magi_event('default_user', TARGET, event('assistant.started', 'evt-socket-1', turn, 1))
    apply_magi_event('default_user', TARGET, event(
        'assistant.completed', 'evt-socket-2', turn, 2, response=rich_response(),
    ))
    monkeypatch.setattr('app.main._ingest_target_snapshot', AsyncMock(return_value=None))

    with client.websocket_connect('/api/v1/events') as socket:
        socket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': TARGET})
        assert socket.receive_json()['type'] == 'connected'
        payload = socket.receive_json()
        assert payload['type'] == 'conversation_messages'
        assistant = next(item for item in payload['messages'] if item['role'] == 'assistant')
        assert assistant['id'] == turn['assistant_message_id']
        assert assistant['content_source'] == 'structured'
        assert assistant['structured_content'] == rich_response()


def test_authenticated_gateway_event_ingestion_and_payload_bound(monkeypatch):
    client = TestClient(app)
    client.post('/api/v1/conversations/captain/reset', headers=TEST_HEADERS)
    monkeypatch.setattr(herdr_client, 'prompt_agent', AsyncMock(return_value={'status': 'accepted'}))
    prompt = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={
        'target': 'captain', 'text': 'API structured response', 'message_id': 'u-api-structured-1',
    })
    assert prompt.status_code == 200
    conversation = prompt.json()['conversation']
    reservation_url = f"/api/v1/conversations/captain/turns/{conversation['turn_id']}/assistant-messages"
    assert client.post(reservation_url, headers=TEST_HEADERS, json={
        'idempotency_key': 'api-progress-one', 'kind': 'progress',
        'user_id': 'another-owner',
    }).status_code == 422
    reserved = client.post(reservation_url, headers=TEST_HEADERS, json={
        'idempotency_key': 'api-progress-one', 'kind': 'progress',
    })
    assert reserved.status_code == 200
    assert reserved.json()['status'] == 'reserved'
    assert client.post(reservation_url, headers=TEST_HEADERS, json={
        'idempotency_key': 'api-progress-one', 'kind': 'progress',
    }).json()['message_id'] == reserved.json()['message_id']
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'response')
    response_token = issue_session('test-bootstrap-secret')['session_token']
    response_headers = {'Authorization': f'Bearer {response_token}'}
    assert client.post(reservation_url, headers=response_headers, json={
        'idempotency_key': 'api-progress-two', 'kind': 'progress',
    }).status_code == 200
    assert client.get('/api/v1/conversations/captain/messages', headers=response_headers).status_code == 403

    identity = {
        'schema_version': 'magi.event.v1', 'event_id': 'evt-api-1',
        'event_type': 'assistant.started', 'turn_id': conversation['turn_id'],
        'message_id': conversation['assistant_message_id'], 'revision': 1,
    }
    assert client.post('/api/v1/conversations/captain/events', json=identity).status_code in {401, 403}
    assert client.post('/api/v1/conversations/worker/events', headers=TEST_HEADERS, json=identity).status_code == 422
    assert client.post('/api/v1/conversations/captain/events', headers=TEST_HEADERS, json={**identity, 'html': '<b>no</b>'}).status_code == 422
    accepted = client.post('/api/v1/conversations/captain/events', headers=TEST_HEADERS, json=identity)
    assert accepted.status_code == 200
    assert accepted.json()['status'] == 'applied'
    duplicate = client.post('/api/v1/conversations/captain/events', headers=TEST_HEADERS, json=identity)
    assert duplicate.status_code == 200
    assert duplicate.json()['status'] == 'duplicate'

    oversized = b'{' + b' ' * MAGI_MAX_RESPONSE_BYTES + b'}'
    response = client.post(
        '/api/v1/conversations/captain/events', headers={**TEST_HEADERS, 'Content-Type': 'application/json'},
        content=oversized,
    )
    assert response.status_code == 413
    lied_about_size = client.post(
        '/api/v1/conversations/captain/events',
        headers={**TEST_HEADERS, 'Content-Type': 'application/json', 'Content-Length': '1'},
        content=oversized,
    )
    assert lied_about_size.status_code == 413
