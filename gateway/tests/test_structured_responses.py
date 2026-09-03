import copy
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app import db
from app.contracts import MAGI_MAX_RESPONSE_BYTES, MagiEventContract, MagiResponseV1
from app.conversation_store import (
    MagiEventConflict,
    apply_magi_event,
    ingest_terminal_rows,
    list_messages,
    record_primary_reply,
    record_prompt,
    reset_conversation,
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
            'SELECT assistant_message_id FROM conversation_turns WHERE id = ?', ('ct_old',),
        ).fetchone()[0]
        fallback = conn.execute(
            '''SELECT text, revision, content_source, structured_content_json, structured_revision
               FROM conversation_messages WHERE id = ?''', ('cm_old_reply',),
        ).fetchone()
    assert 'assistant_message_id' in turn_columns
    assert {'content_source', 'structured_content_json', 'structured_revision'} <= message_columns
    assert 'magi_response_events' in tables
    assert turn_identity == 'cm_old_reply'
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
