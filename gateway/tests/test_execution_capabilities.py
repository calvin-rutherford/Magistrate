import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.main as main_module
from app.usage import _summarize_provider
from conftest import TEST_HEADERS


HEADERS = TEST_HEADERS
client = TestClient(main_module.app)


def inventory():
    return {
        'harnesses': [
            {
                'id': 'codex',
                'label': 'Codex CLI',
                'verified': True,
                'models': [{'id': 'gpt-5', 'label': 'GPT-5'}],
            },
            {
                'id': 'unverified',
                'label': 'Unverified',
                'verified': False,
                'models': [{'id': 'unsafe', 'label': 'Unsafe'}],
            },
        ]
    }


def test_usage_summary_preserves_only_authenticated_quota_evidence():
    summary = _summarize_provider({
        'provider': 'codex', 'plan': 'plus',
        'state': {'status': 'fresh', 'stale': False},
        'windows': [{'label': 'week', 'percentRemaining': 20, 'resetsAt': 'tomorrow', 'ignored': 'not exposed'}],
    })
    assert summary == {
        'provider': 'codex', 'plan': 'plus', 'status': 'fresh', 'stale': False,
        'windows': [{'label': 'week', 'percentRemaining': 20, 'resetsAt': 'tomorrow'}],
    }


def test_capability_inventory_exposes_only_verified_harnesses(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))

    response = client.get('/api/v1/execution/capabilities', headers=HEADERS)

    assert response.status_code == 200
    assert response.json()['harnesses'] == [{
        'id': 'codex',
        'label': 'Codex CLI',
        'verified': True,
        'models': [{'id': 'gpt-5', 'label': 'GPT-5'}],
    }]


def test_unified_profiles_keep_harness_provider_model_variant_and_auth(monkeypatch):
    configured = inventory()
    configured['harnesses'][0].update({'provider': 'openai-codex'})
    configured['harnesses'][0]['models'][0].update({
        'variant': 'luna', 'profile_id': 'pi:luna',
        'auth': {'required': True, 'credential_key': 'openai-codex'},
    })
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(configured))

    response = client.get('/api/v1/execution/capabilities?user_id=unified-profile-test', headers=HEADERS)

    assert response.status_code == 200
    profile = response.json()['profiles'][0]
    assert profile['id'] == 'pi:luna'
    assert profile['harness'] == {'id': 'codex', 'label': 'Codex CLI'}
    assert profile['provider']['id'] == 'openai-codex'
    assert profile['variant'] == 'luna'
    assert profile['auth']['status'] == 'required'
    assert profile['availability'] == 'unavailable'
    assert response.json()['routing']['migration_supported'] is False


def test_execution_settings_persist_defaults_and_clear_selection(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    response = client.get('/api/v1/execution/settings?user_id=settings-test', headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['switching_behavior'] == 'migrate'
    assert response.json()['unavailable_behavior'] == 'error'

    response = client.put('/api/v1/execution/settings?user_id=settings-test', headers=HEADERS, json={
        'profile_id': 'codex:gpt-5', 'switching_behavior': 'new-session',
    })
    assert response.status_code == 200
    assert response.json()['profile_id'] == 'codex:gpt-5'
    assert response.json()['switching_behavior'] == 'new-session'

    response = client.put('/api/v1/execution/settings?user_id=settings-test', headers=HEADERS, json={'profile_id': None})
    assert response.status_code == 200
    assert response.json()['profile_id'] is None
    assert response.json()['switching_behavior'] == 'new-session'


def test_new_agent_routing_preference_round_trips_and_rejects_invalid_selection(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))

    saved = client.put('/api/v1/execution/routing-preference', headers=HEADERS, json={
        'harness': 'codex', 'model': 'gpt-5',
    })
    assert saved.status_code == 200
    assert saved.json()['default'] == {
        'profile_id': 'codex:gpt-5', 'harness': 'codex', 'model': 'gpt-5',
        'provider': 'unknown', 'variant': 'gpt-5',
    }
    assert saved.json()['applies_to'] == ['new', 'restarted']
    assert saved.json()['delivery']['automatic'] is False
    assert saved.json()['delivery']['status'] == 'pending-firstmate-integration'

    reloaded = client.get('/api/v1/execution/routing-preference', headers=HEADERS)
    assert reloaded.status_code == 200
    assert reloaded.json() == saved.json()

    invalid_model = client.put('/api/v1/execution/routing-preference', headers=HEADERS, json={
        'harness': 'codex', 'model': 'invented-model',
    })
    assert invalid_model.status_code == 422
    assert client.get('/api/v1/execution/routing-preference', headers=HEADERS).json() == saved.json()

    incomplete = client.put('/api/v1/execution/routing-preference', headers=HEADERS, json={'harness': 'codex'})
    assert incomplete.status_code == 422

    cleared = client.put('/api/v1/execution/routing-preference', headers=HEADERS, json={'harness': None, 'model': None})
    assert cleared.status_code == 200
    assert cleared.json()['default'] is None


def test_agent_migration_is_confirmed_durable_and_idempotent(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    monkeypatch.setattr(main_module.herdr_client, 'list_agents', AsyncMock(return_value=[{
        'id': 'w1:p7', 'pane_id': 'w1:p7', 'name': 'worker',
        'status': 'working', 'harness': 'pi', 'model': 'old-model',
    }]))
    monkeypatch.setattr(main_module.fm_client, 'get_snapshot', AsyncMock(return_value={
        'tasks': [{
            'id': 'migration-task', 'endpoint': {'target': 'default:w1:p7'}, 'harness': 'pi',
            'paths': {'worktree': {'present': True, 'path': '/tmp/worktree'}, 'status_log': {'last_event': 'working: tests'}},
            'backlog': {'title': 'Migrate this worker'},
        }],
    }))
    body = {'profile_id': 'codex:gpt-5', 'idempotency_key': 'migration_retry_0001', 'confirmed': True}

    unconfirmed = client.post('/api/v1/agents/w1:p7/migration-requests', headers=HEADERS, json={**body, 'confirmed': False})
    assert unconfirmed.status_code == 422

    requested = client.post('/api/v1/agents/w1:p7/migration-requests', headers=HEADERS, json=body)
    assert requested.status_code == 200
    migration = requested.json()
    assert migration['status'] == 'requested'
    assert migration['target'] == {'profile_id': 'codex:gpt-5', 'harness': 'codex', 'model': 'gpt-5'}
    assert migration['context']['current_runtime'] == {'harness': 'pi', 'model': 'old-model'}
    assert migration['context']['worktree'] == '/tmp/worktree'
    assert migration['context']['branch'] is None
    assert migration['context']['not_preserved'] == ['in-flight turn']
    assert migration['execution']['automatic'] is False
    assert migration['execution']['mode'] == 'operator-terminal'

    duplicate = client.post('/api/v1/agents/w1:p7/migration-requests', headers=HEADERS, json=body)
    assert duplicate.status_code == 200
    assert duplicate.json() == migration

    inspected = client.get(
        f"/api/v1/agents/w1:p7/migration-requests/{migration['request_id']}", headers=HEADERS,
    )
    assert inspected.status_code == 200
    assert inspected.json() == migration


def test_agent_migration_transitions_require_terminal_evidence_and_retry_idempotently(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    monkeypatch.setattr(main_module.herdr_client, 'list_agents', AsyncMock(return_value=[{
        'id': 'w2:p8', 'pane_id': 'w2:p8', 'name': 'worker', 'status': 'working', 'harness': 'pi', 'model': None,
    }]))
    monkeypatch.setattr(main_module.fm_client, 'get_snapshot', AsyncMock(return_value={'tasks': []}))
    key = 'migration_retry_0002'
    created = client.post('/api/v1/agents/w2:p8/migration-requests', headers=HEADERS, json={
        'profile_id': 'codex:gpt-5', 'idempotency_key': key, 'confirmed': True,
    }).json()
    endpoint = f"/api/v1/agents/w2:p8/migration-requests/{created['request_id']}/operator-transition"

    no_confirmation = client.post(endpoint, headers=HEADERS, json={
        'state': 'relaunching', 'idempotency_key': key, 'terminal_confirmed': False, 'evidence': 'operator command',
    })
    assert no_confirmation.status_code == 422

    relaunching = client.post(endpoint, headers=HEADERS, json={
        'state': 'relaunching', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'operator started relaunch',
    })
    assert relaunching.status_code == 200
    assert relaunching.json()['status'] == 'relaunching'
    assert client.post(endpoint, headers=HEADERS, json={
        'state': 'relaunching', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'duplicate report',
    }).json() == relaunching.json()

    failed = client.post(endpoint, headers=HEADERS, json={
        'state': 'failed', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'runtime exited before attach',
    })
    assert failed.status_code == 200
    assert failed.json()['status'] == 'failed'
    assert failed.json()['error'] == 'runtime exited before attach'

    retrying = client.post(endpoint, headers=HEADERS, json={
        'state': 'relaunching', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'operator retried same request',
    })
    assert retrying.status_code == 200
    assert retrying.json()['status'] == 'relaunching'
    assert retrying.json()['error'] is None

    running = client.post(endpoint, headers=HEADERS, json={
        'state': 'running-on-new', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'new pane observed on codex/gpt-5',
    })
    assert running.status_code == 200
    assert running.json()['status'] == 'running-on-new'
    invalid_retry = client.post(endpoint, headers=HEADERS, json={
        'state': 'relaunching', 'idempotency_key': key, 'terminal_confirmed': True, 'evidence': 'must not reopen success',
    })
    assert invalid_retry.status_code == 409


def test_execution_credential_is_encrypted_and_changes_profile_auth(monkeypatch):
    configured = inventory()
    configured['harnesses'][0]['provider'] = 'openai-codex'
    configured['harnesses'][0]['models'][0]['auth'] = {'required': True, 'credential_key': 'openai-codex'}
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(configured))
    response = client.put('/api/v1/execution/credentials/openai-codex', headers=HEADERS, json={'credential': 'secret-value'})
    assert response.status_code == 200
    assert response.json() == {'credential_key': 'openai-codex', 'configured': True, 'updated_at': response.json()['updated_at']}
    assert response.json()['updated_at']
    profile = client.get('/api/v1/execution/capabilities', headers=HEADERS).json()['profiles'][0]
    assert profile['auth']['status'] == 'configured'
    assert profile['availability'] == 'available'


def test_prompt_accepts_atomic_profile_selection(monkeypatch):
    configured = inventory()
    configured['harnesses'][0].update({'id': 'pi', 'label': 'Pi', 'provider': 'openai-codex'})
    configured['harnesses'][0]['models'][0].update({'id': 'gpt-5.6-luna', 'label': 'GPT-5.6 Luna', 'variant': 'default', 'profile_id': 'pi:default'})
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(configured))
    prompt_agent = AsyncMock(return_value={'status': 'submitted'})
    monkeypatch.setattr(main_module.herdr_client, 'prompt_agent', prompt_agent)

    response = client.post('/api/v1/captain/prompt', headers=HEADERS, json={
        'text': 'hello', 'profile_id': 'pi:default'
    })

    assert response.status_code == 200
    prompt_agent.assert_awaited_once_with('captain', 'hello', profile_id='pi:default', harness='pi', model='gpt-5.6-luna', provider='openai-codex', variant='default')


def test_prompt_rejects_selection_outside_inventory(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    prompt_agent = AsyncMock()
    monkeypatch.setattr(main_module.herdr_client, 'prompt_agent', prompt_agent)

    response = client.post('/api/v1/captain/prompt', headers=HEADERS, json={
        'text': 'hello', 'harness': 'codex', 'model': 'not-available'
    })

    assert response.status_code == 422
    prompt_agent.assert_not_awaited()


def test_prompt_rejects_unsafe_capability_identifier(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    prompt_agent = AsyncMock()
    monkeypatch.setattr(main_module.herdr_client, 'prompt_agent', prompt_agent)

    response = client.post('/api/v1/captain/prompt', headers=HEADERS, json={
        'text': 'hello', 'harness': 'codex; rm -rf /', 'model': 'gpt-5'
    })

    assert response.status_code == 422
    prompt_agent.assert_not_awaited()


def test_prompt_passes_validated_selection_to_real_prompt_path(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(inventory()))
    prompt_agent = AsyncMock(return_value={'status': 'submitted'})
    monkeypatch.setattr(main_module.herdr_client, 'prompt_agent', prompt_agent)

    response = client.post('/api/v1/captain/prompt', headers=HEADERS, json={
        'text': 'hello', 'harness': 'codex', 'model': 'gpt-5'
    })

    assert response.status_code == 200
    prompt_agent.assert_awaited_once_with('captain', 'hello', profile_id='codex:gpt-5', harness='codex', provider='unknown', model='gpt-5', variant='gpt-5')


def test_invalid_inventory_is_reported_without_exposing_configuration(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', '{not-json')

    response = client.get('/api/v1/execution/capabilities', headers=HEADERS)

    assert response.status_code == 503
    assert response.json()['detail'] == 'Execution capability inventory is unavailable.'
