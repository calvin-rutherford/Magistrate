import asyncio
import sqlite3

import pytest

from app import db
from app.auth import issue_session
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_model import MagiModelError, MagiModelResult
from app.main import app
from conftest import TEST_HEADERS
from fastapi.testclient import TestClient


client = TestClient(app)


class FakeModel:
    def __init__(self, *, delay: float = 0, fail_once: set[str] | None = None):
        self.delay = delay
        self.fail_once = set(fail_once or set())
        self.calls: list[tuple[str, str]] = []

    async def complete(self, messages, *, system_context, request_id):
        content = messages[-1].content
        self.calls.append((request_id, content))
        if self.delay:
            await asyncio.sleep(self.delay)
        if content in self.fail_once:
            self.fail_once.remove(content)
            raise MagiModelError('synthetic_provider_failure')
        return MagiModelResult(f"# Native reply\n\n{content}\n\n✓ café 🚀")


@pytest.fixture
def native_flags(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_NATIVE_CHAT_ENABLED', 'true')
    monkeypatch.setenv('MAGISTRATE_LEGACY_CHAT_ENABLED', 'false')


@pytest.mark.parametrize(('native', 'legacy'), [('true', 'true'), ('false', 'false'), ('maybe', 'false')])
def test_chat_transport_configuration_fails_closed(monkeypatch, native, legacy):
    monkeypatch.setenv('MAGISTRATE_NATIVE_CHAT_ENABLED', native)
    monkeypatch.setenv('MAGISTRATE_LEGACY_CHAT_ENABLED', legacy)
    response = client.get('/api/v1/magi/conversations/current', headers=TEST_HEADERS)
    assert response.status_code == 503


def test_native_api_is_authenticated_owned_and_independent_of_execution_infrastructure(native_flags, monkeypatch):
    import app.magi_chat_api as native_api
    import app.main as gateway

    fake = FakeModel()
    native_api.magi_chat_service.store.reset_diagnostics('default_user')
    monkeypatch.setattr(native_api.magi_chat_service, 'model', fake)

    async def forbidden(*args, **kwargs):
        raise AssertionError('native chat consulted execution infrastructure')

    monkeypatch.setattr(gateway.herdr_client, 'read_typed_rows', forbidden)
    monkeypatch.setattr(gateway.herdr_client, 'get_agent_history', forbidden)
    monkeypatch.setattr(gateway.herdr_client, 'interrupt_agent', forbidden)
    monkeypatch.setattr(gateway.herdr_client, 'prompt_agent', forbidden)
    monkeypatch.setattr(gateway.fm_client, 'get_snapshot', forbidden)
    monkeypatch.setattr(gateway, '_exchange_pi_dispatch', forbidden)

    body = {
        'client_message_id': 'native-api-0001',
        'content': 'Unicode: naïve 東京 👩🏽\u200d💻\n\n1. one\n2. two',
    }
    assert client.post('/api/v1/magi/messages', json=body).status_code == 401
    assert client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={
        'message_id': 'legacy-must-stay-off', 'text': 'do not dispatch',
    }).status_code == 404
    assert client.post('/api/v1/voice/moves', headers=TEST_HEADERS, json={
        'utterance': 'do not dispatch', 'idempotency_key': 'legacy-voice-off',
    }).status_code == 404
    assert client.get('/api/v1/captain/output', headers=TEST_HEADERS).status_code == 404
    assert client.get('/api/v1/agents/captain/history', headers=TEST_HEADERS).status_code == 404
    assert client.post('/api/v1/agents/captain/interrupt', headers=TEST_HEADERS).status_code == 404
    response = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload['schema_version'] == 'magi.native-chat.v1'
    assert payload['status'] == 'completed'
    assert payload['user_message']['content'] == body['content']
    expected = f"# Native reply\n\n{body['content']}\n\n✓ café 🚀"
    assert payload['assistant_message']['content'].encode() == expected.encode()
    assert len(fake.calls) == 1

    duplicate = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json=body).json()
    assert duplicate['duplicate'] is True
    assert duplicate['messages'] == payload['messages']
    assert len(fake.calls) == 1

    conversation_id = payload['conversation']['id']
    replay = client.get(
        f'/api/v1/magi/conversations/{conversation_id}', headers=TEST_HEADERS,
    ).json()
    assert [message['content'] for message in replay['messages']] == [body['content'], expected]
    changes = client.get(
        f'/api/v1/magi/conversations/{conversation_id}/replay?after=0', headers=TEST_HEADERS,
    ).json()
    assert changes['latest_change'] == 3
    assert changes['messages'][-1]['content'].encode() == expected.encode()
    diagnostics = client.get('/api/v1/magi/diagnostics', headers=TEST_HEADERS).json()
    assert diagnostics['magi_messages_submitted'] >= 1
    assert diagnostics['magi_messages_completed'] >= 1
    assert diagnostics['magi_duplicate_submissions'] >= 1
    assert diagnostics['legacy_chat_reads'] == 0
    assert diagnostics['terminal_chat_reads'] == 0
    assert diagnostics['pi_ownership_chat_reads'] == 0


def test_native_websocket_replays_sqlite_messages_without_terminal_reads(native_flags, monkeypatch):
    import app.magi_chat_api as native_api
    import app.main as gateway

    async def forbidden(*args, **kwargs):
        raise AssertionError('native WebSocket read terminal infrastructure')

    monkeypatch.setattr(gateway.herdr_client, 'read_typed_rows', forbidden)
    prepared = native_api.magi_chat_store.prepare_submission(
        'default_user', 'native-websocket-0001', 'socket native prompt',
    )
    native_api.magi_chat_store.complete_submission(
        'default_user', prepared.assistant_message_id, prepared.attempt,
        'socket native response', latency_ms=1,
    )
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({
            'type': 'auth', 'token': TEST_HEADERS['Authorization'].removeprefix('Bearer '),
            'target': 'captain', 'chat_mode': 'native',
        })
        assert websocket.receive_json() == {'type': 'connected', 'target': 'captain'}
        event = websocket.receive_json()
        assert event['type'] == 'magi_messages'
        assert event['schema_version'] == 'magi.native-chat.v1'
        [first_response] = [message for message in event['messages'] if message['content'] == 'socket native response']
        assert all(message['source'] in {'text', 'voice', 'magi-native'} for message in event['messages'])
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({
            'type': 'auth', 'token': TEST_HEADERS['Authorization'].removeprefix('Bearer '),
            'target': 'captain', 'chat_mode': 'native',
        })
        websocket.receive_json()
        reconnect = websocket.receive_json()
        [restored_response] = [
            message for message in reconnect['messages'] if message['content'] == 'socket native response'
        ]
        assert restored_response['id'] == first_response['id']
        assert restored_response['revision'] == first_response['revision']


def test_native_api_rejects_client_identity_and_cross_tenant_conversation(native_flags, monkeypatch):
    import app.magi_chat_api as native_api

    monkeypatch.setattr(native_api.magi_chat_service, 'model', FakeModel())
    created = client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json={
        'client_message_id': 'native-owner-0001', 'content': 'owner A',
    }).json()
    assert client.post('/api/v1/magi/messages', headers=TEST_HEADERS, json={
        'client_message_id': 'native-owner-0002', 'content': 'owner A', 'user_id': 'attacker',
    }).status_code == 422

    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'other_owner')
    other_token = issue_session('test-bootstrap-secret')['session_token']
    other_headers = {'Authorization': f'Bearer {other_token}'}
    same_client_other_tenant = client.post('/api/v1/magi/messages', headers=other_headers, json={
        'client_message_id': 'native-owner-0001', 'content': 'owner B independent idempotency',
    })
    assert same_client_other_tenant.status_code == 200
    assert same_client_other_tenant.json()['user_message']['id'] != created['user_message']['id']
    assert client.get(
        f"/api/v1/magi/conversations/{created['conversation']['id']}", headers=other_headers,
    ).status_code == 404
    assert client.post('/api/v1/magi/messages', headers=other_headers, json={
        'conversation_id': created['conversation']['id'],
        'client_message_id': 'native-other-0001', 'content': 'steal it',
    }).status_code == 404


@pytest.mark.asyncio
async def test_model_context_never_crosses_principals(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'context-isolation.sqlite3'))
    observed: list[tuple[str, list[str]]] = []

    class ContextModel:
        async def complete(self, messages, *, system_context, request_id):
            observed.append((system_context, [message.content for message in messages]))
            return MagiModelResult('isolated reply')

    service = MagiChatService(
        ContextModel(), store=MagiChatStore(),
        profile_loader=lambda owner: {'name': f'profile-{owner}'},
    )
    await service.submit('tenant-a', 'shared-client-0001', 'tenant A private marker')
    await service.submit('tenant-b', 'shared-client-0001', 'tenant B question')
    assert len(observed) == 2
    assert 'profile-tenant-b' in observed[1][0]
    assert all('tenant A private marker' not in content for content in observed[1][1])


@pytest.mark.asyncio
async def test_concurrent_duplicate_claims_invoke_model_once(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'concurrent.sqlite3'))
    model = FakeModel(delay=0.08)
    service = MagiChatService(model, store=MagiChatStore(), profile_loader=lambda _: {'name': 'Tester'})
    calls = [service.submit('concurrent-owner', 'concurrent-client-0001', 'same request') for _ in range(24)]
    results = await asyncio.gather(*calls)
    assert len(model.calls) == 1
    assert sum(result['duplicate'] is False for result in results) == 1
    assert {result['user_message']['id'] for result in results} == {results[0]['user_message']['id']}
    # Calls arriving while the one provider request runs may truthfully return
    # pending; replay converges on the one complete canonical pair.
    final = service.store.submission('concurrent-owner', 'concurrent-client-0001')
    assert final['status'] == 'completed'
    assert final['assistant_message']['content'] == '# Native reply\n\nsame request\n\n✓ café 🚀'


@pytest.mark.asyncio
async def test_failure_keeps_user_and_explicit_retry_reuses_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'retry.sqlite3'))
    model = FakeModel(fail_once={'fail once'})
    service = MagiChatService(model, store=MagiChatStore(), profile_loader=lambda _: {})
    failed = await service.submit('retry-owner', 'retry-client-0001', 'fail once')
    assert failed['status'] == 'failed'
    assert failed['user_message']['content'] == 'fail once'
    assert failed['assistant_message']['content'] == ''
    assert failed['assistant_message']['status'] == 'failed'

    duplicate = await service.submit('retry-owner', 'retry-client-0001', 'fail once')
    assert duplicate['status'] == 'failed'
    assert len(model.calls) == 1
    completed = await service.submit(
        'retry-owner', 'retry-client-0001', 'fail once', retry_failed=True,
    )
    assert completed['status'] == 'completed'
    assert completed['retry'] is True
    assert completed['user_message']['id'] == failed['user_message']['id']
    assert completed['assistant_message']['id'] == failed['assistant_message']['id']
    assert len(model.calls) == 2
    diagnostics = await service.diagnostics('retry-owner')
    assert diagnostics['magi_messages_submitted'] == 1
    assert diagnostics['magi_messages_completed'] == 1
    assert diagnostics['magi_messages_failed'] == 1
    assert diagnostics['magi_retries'] == 1


@pytest.mark.asyncio
async def test_http_caller_cancellation_does_not_lose_shielded_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'disconnect.sqlite3'))
    started = asyncio.Event()
    release = asyncio.Event()

    class DisconnectModel:
        async def complete(self, messages, *, system_context, request_id):
            started.set()
            await release.wait()
            return MagiModelResult('completed after mobile disconnect 🚀')

    store = MagiChatStore()
    service = MagiChatService(DisconnectModel(), store=store, profile_loader=lambda _: {})
    request = asyncio.create_task(service.submit(
        'disconnect-owner', 'disconnect-client-0001', 'keep working',
    ))
    await started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release.set()
    for _ in range(100):
        if store.submission('disconnect-owner', 'disconnect-client-0001')['status'] == 'completed':
            break
        await asyncio.sleep(0.005)
    restored = store.submission('disconnect-owner', 'disconnect-client-0001')
    assert restored['status'] == 'completed'
    assert restored['assistant_message']['content'] == 'completed after mobile disconnect 🚀'


@pytest.mark.asyncio
async def test_explicit_cancel_is_durable_and_late_completion_cannot_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'cancel.sqlite3'))
    started = asyncio.Event()

    class BlockingModel:
        async def complete(self, messages, *, system_context, request_id):
            started.set()
            await asyncio.Event().wait()
            return MagiModelResult('must never persist')

    store = MagiChatStore()
    service = MagiChatService(BlockingModel(), store=store, profile_loader=lambda _: {})
    submission = asyncio.create_task(service.submit(
        'cancel-owner', 'cancel-client-0001', 'stop this response',
    ))
    await started.wait()
    cancelled = await service.cancel('cancel-owner', 'cancel-client-0001')
    assert cancelled['status'] == 'cancelled'
    assert cancelled['assistant_message']['content'] == ''
    with pytest.raises(asyncio.CancelledError):
        await submission
    restored = store.submission('cancel-owner', 'cancel-client-0001')
    assert restored['status'] == 'cancelled'
    assert restored['assistant_message']['content'] == ''


@pytest.mark.asyncio
async def test_restart_marks_only_orphaned_pending_pair_failed_and_retryable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'restart.sqlite3'))
    store = MagiChatStore()
    prepared = store.prepare_submission('restart-owner', 'restart-client', 'survive me')
    assert prepared.claimed is True
    assert store.recover_orphaned_pending() == 1
    orphaned = store.submission('restart-owner', 'restart-client')
    assert orphaned['status'] == 'failed'
    assert orphaned['assistant_message']['content'] == ''

    model = FakeModel()
    service = MagiChatService(model, store=store, profile_loader=lambda _: {})
    retried = await service.submit(
        'restart-owner', 'restart-client', 'survive me', retry_failed=True,
    )
    assert retried['status'] == 'completed'
    assert retried['assistant_message']['id'] == prepared.assistant_message_id
    assert retried['assistant_message']['content'] == '# Native reply\n\nsurvive me\n\n✓ café 🚀'


@pytest.mark.asyncio
async def test_deterministic_native_reliability_gate_is_perfect(monkeypatch, tmp_path):
    from scripts.magi_native_reliability import run_gate

    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'reliability.sqlite3'))
    report = await run_gate()
    assert report['result'] == 'PASS'
    assert report['submissions'] == report['complete'] == 120
    assert report['truncation'] == report['duplication'] == report['cross_attribution'] == 0
    assert report['raw_internals'] == 0
    assert report['legacy_chat_reads'] == 0
    assert report['terminal_chat_reads'] == 0
    assert report['pi_ownership_chat_reads'] == 0
    assert report['maximum_response_characters'] > 80_000


def test_old_gateway_database_migrates_additively_without_deleting_rows(monkeypatch, tmp_path):
    migrated = tmp_path / 'old-gateway.sqlite3'
    monkeypatch.setattr(db, 'DB_PATH', str(migrated))
    # Bring all pre-native additive migrations to their baseline state, then
    # remove only the four new native tables to model an immediately prior DB.
    db.init_db()
    with sqlite3.connect(migrated) as connection:
        connection.execute('DROP TABLE magi_message_changes')
        connection.execute('DROP TABLE magi_messages')
        connection.execute('DROP TABLE magi_conversations')
        connection.execute('DROP TABLE magi_chat_diagnostics')
        timestamp = 1_789_000_000_000
        connection.execute(
            'INSERT INTO conversations VALUES (?, ?, ?, ?, ?)',
            ('legacy-conversation-proof', 'legacy-owner', 'captain', timestamp, timestamp),
        )
        connection.execute(
            '''INSERT INTO conversation_turns
               (id, conversation_id, client_message_id, prompt_key, status, sequence_index,
                created_at, updated_at, assistant_message_id, lifecycle_state, lifecycle_revision,
                objective_id, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            ('legacy-turn-proof', 'legacy-conversation-proof', 'legacy-client-proof',
             'legacy-prompt-key', 'completed', 0, timestamp, timestamp,
             'legacy-assistant-proof', 'completed', 2, 'legacy-objective-proof', 'legacy-run-proof'),
        )
        connection.executemany(
            '''INSERT INTO conversation_messages
               (id, turn_id, conversation_id, role, type, slot, text, visible_in_chat,
                sequence_index, revision, source, attachments_json, created_at, updated_at,
                content_source, assistant_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [
                ('legacy-user-proof', 'legacy-turn-proof', 'legacy-conversation-proof', 'user',
                 'conversation', 'user', 'legacy café 東京', 1, 0, 1, 'text', '[]',
                 timestamp, timestamp, 'terminal-fallback', 'response'),
                ('legacy-assistant-proof', 'legacy-turn-proof', 'legacy-conversation-proof', 'assistant',
                 'conversation', 'primary', '# Legacy\n\nPreserved byte-for-byte.\n', 1, 1, 1,
                 'terminal', '[]', timestamp, timestamp, 'terminal-fallback', 'response'),
            ],
        )
        connection.execute(
            '''INSERT INTO conversation_assistant_reservations
               (message_id, turn_id, ordinal, slot, message_kind, idempotency_key, created_at)
               VALUES (?, ?, 0, 'primary', 'response', 'reserved-primary', ?)''',
            ('legacy-assistant-proof', 'legacy-turn-proof', timestamp),
        )
        old_tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        old_rows = {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in old_tables
        }
    db.init_db()
    with sqlite3.connect(migrated) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {'magi_conversations', 'magi_messages', 'magi_message_changes', 'magi_chat_diagnostics'} <= tables
        for table, rows in old_rows.items():
            assert connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() == rows
        assert connection.execute(
            "SELECT text FROM conversation_messages WHERE id = 'legacy-assistant-proof'"
        ).fetchone()[0].encode('utf-8') == b'# Legacy\n\nPreserved byte-for-byte.\n'
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)

    store = MagiChatStore()
    prepared = store.prepare_submission('native-after-migration', 'native-client-proof', 'new native turn')
    assert store.complete_submission(
        'native-after-migration', prepared.assistant_message_id, prepared.attempt,
        'new native response', latency_ms=1,
    ) is True
    assert store.submission('native-after-migration', 'native-client-proof')['status'] == 'completed'
