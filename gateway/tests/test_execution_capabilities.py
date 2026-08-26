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
