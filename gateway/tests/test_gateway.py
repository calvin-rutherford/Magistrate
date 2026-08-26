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
