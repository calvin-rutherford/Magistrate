import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import MAGISTRATE_TOKEN

client = TestClient(app)

def test_health_unauthorized():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 401

def test_health_authorized():
    resp = client.get("/api/v1/health", headers={"X-Magistrate-Token": MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "magistrate-gateway"

def test_runtime():
    resp = client.get("/api/v1/runtime", headers={"X-Magistrate-Token": MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    data = resp.json()
    assert "herdr" in data
    assert "firstmate" in data

def test_fleet():
    resp = client.get("/api/v1/fleet", headers={"X-Magistrate-Token": MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("schema", "fm-fleet-snapshot.v1") == "fm-fleet-snapshot.v1"

def test_attention():
    resp = client.get("/api/v1/attention", headers={"X-Magistrate-Token": MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_static_spa_deep_links_serve_the_exported_frontend():
    for path in ('/chat', '/voice'):
        response = client.get(path)
        assert response.status_code == 200
        assert '<!DOCTYPE html>' in response.text
        assert 'Magistrate' in response.text


def test_unknown_api_route_is_not_captured_by_spa_fallback():
    response = client.get('/api/v1/does-not-exist', headers={'X-Magistrate-Token': MAGISTRATE_TOKEN})
    assert response.status_code == 404


def test_captain_prompt_empty():
    resp = client.post(
        "/api/v1/captain/prompt",
        headers={"X-Magistrate-Token": MAGISTRATE_TOKEN},
        json={"source": "iphone", "modality": "text", "type": "prompt", "text": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"

def test_agent_interrupt_requires_authentication():
    resp = client.post('/api/v1/agents/agent-1/interrupt')
    assert resp.status_code == 401

def test_agent_interrupt_delegates_to_herdr(monkeypatch):
    async def fake_interrupt(target):
        return {'status': 'interrupted', 'target': target}

    monkeypatch.setattr('app.main.herdr_client.interrupt_agent', fake_interrupt)
    resp = client.post('/api/v1/agents/agent-1/interrupt', headers={'X-Magistrate-Token': MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    assert resp.json() == {'status': 'interrupted', 'target': 'agent-1'}

def test_agent_history_delegates_to_herdr(monkeypatch):
    async def fake_history(target, lines):
        return {'target': target, 'messages': [{'role': 'user', 'kind': 'conversation', 'text': 'Hello'}]}

    monkeypatch.setattr('app.main.herdr_client.get_agent_history', fake_history)
    resp = client.get('/api/v1/agents/agent-1/history?lines=25', headers={'X-Magistrate-Token': MAGISTRATE_TOKEN})
    assert resp.status_code == 200
    assert resp.json()['messages'][0]['text'] == 'Hello'

def test_agent_rename_delegates_to_herdr_and_validates_name(monkeypatch):
    async def fake_rename(target, name):
        return {'status': 'renamed', 'target': target, 'name': name}

    monkeypatch.setattr('app.main.herdr_client.rename_agent', fake_rename)
    resp = client.post('/api/v1/agents/agent-1/rename', headers={'X-Magistrate-Token': MAGISTRATE_TOKEN}, json={'name': 'review_agent'})
    assert resp.status_code == 200
    assert resp.json() == {'status': 'renamed', 'target': 'agent-1', 'name': 'review_agent'}

    invalid = client.post('/api/v1/agents/agent-1/rename', headers={'X-Magistrate-Token': MAGISTRATE_TOKEN}, json={'name': 'Invalid Name'})
    assert invalid.status_code == 422
