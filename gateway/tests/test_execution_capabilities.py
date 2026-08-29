import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import MAGISTRATE_TOKEN


HEADERS = {'X-Magistrate-Token': MAGISTRATE_TOKEN}
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


def test_execution_credential_is_encrypted_and_changes_profile_auth(monkeypatch):
    configured = inventory()
    configured['harnesses'][0]['provider'] = 'openai-codex'
    configured['harnesses'][0]['models'][0]['auth'] = {'required': True, 'credential_key': 'openai-codex'}
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', json.dumps(configured))
    response = client.put('/api/v1/execution/credentials/openai-codex?user_id=credential-test', headers=HEADERS, json={'credential': 'secret-value'})
    assert response.status_code == 200
    assert response.json() == {'credential_key': 'openai-codex', 'configured': True, 'updated_at': response.json()['updated_at']}
    assert response.json()['updated_at']
    profile = client.get('/api/v1/execution/capabilities?user_id=credential-test', headers=HEADERS).json()['profiles'][0]
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
    prompt_agent.assert_awaited_once_with('captain', 'hello', harness='codex', model='gpt-5')


def test_invalid_inventory_is_reported_without_exposing_configuration(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_EXECUTION_INVENTORY', '{not-json')

    response = client.get('/api/v1/execution/capabilities', headers=HEADERS)

    assert response.status_code == 503
    assert response.json()['detail'] == 'Execution capability inventory is unavailable.'
