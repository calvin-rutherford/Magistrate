import pytest
from fastapi.testclient import TestClient
from app.main import app
from conftest import TEST_HEADERS, TEST_SESSION_TOKEN

client = TestClient(app)


def test_health_unauthorized():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 401


def test_health_authorized():
    resp = client.get("/api/v1/health", headers=TEST_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    # 'healthy' is now a claim about observed sources, so it depends on whether
    # Herdr/Firstmate are reachable where the suite runs. Assert the invariant
    # that holds either way and pin both verdicts in the tests below, rather
    # than an environment-dependent literal.
    assert data["status"] in ("healthy", "degraded")
    assert (data["status"] == "healthy") == (not data["degraded_sources"])
    assert data["service"] == "magistrate-gateway"
    assert data["firstmate_producer"]["schema_version"] == "firstmate-producer-readiness.v1"
    assert "fm_home" not in data["firstmate_producer"]
    assert data["pi_semantic_ownership"]["default_enabled"] is True
    assert data["pi_semantic_ownership"]["defaulted"] is False
    assert data["pi_semantic_ownership"]["enabled"] is False
    assert data["pi_semantic_ownership"]["adapter"]["status"] == "disabled"


def test_health_is_process_free_and_reports_configured_interfaces(monkeypatch):
    import app.main as gateway

    async def forbidden_probe():
        raise AssertionError("health must not inspect Herdr or run a fleet snapshot")

    monkeypatch.setattr(gateway.herdr_client, "get_snapshot", forbidden_probe)
    monkeypatch.setattr(gateway.fm_client, "get_snapshot", forbidden_probe)
    monkeypatch.setattr(gateway, "magi_chat_readiness", lambda: {
        "status": "configured", "enabled": True,
        "provider": "openai", "live_probe_performed": False,
    })
    monkeypatch.setattr(gateway.fm_client, "get_execution_interface_readiness", lambda: {
        "schema_version": "firstmate.execution-interface-readiness.v1",
        "status": "configured", "delegation": "explicit-authorized-action-only",
        "event_ingress": "ready", "live_probe_performed": False,
    })
    data = client.get("/api/v1/health", headers=TEST_HEADERS).json()
    assert data["status"] == "healthy"
    assert data["degraded_sources"] == []
    assert data["herdr_version"] is None
    assert data["herdr_socket_connected"] is False
    assert data["herdr_observation"] == "not-probed"
    assert data["firstmate_available"] is True
    assert data["persisted_runtime"]["live_process_probe"] is False


def test_health_names_unconfigured_bounded_interfaces(monkeypatch):
    import app.main as gateway

    monkeypatch.setattr(gateway, "magi_chat_readiness", lambda: {
        "status": "unconfigured", "enabled": True,
        "provider": "openai", "live_probe_performed": False,
    })
    monkeypatch.setattr(gateway.fm_client, "get_execution_interface_readiness", lambda: {
        "schema_version": "firstmate.execution-interface-readiness.v1",
        "status": "unavailable", "delegation": "explicit-authorized-action-only",
        "event_ingress": "ready", "live_probe_performed": False,
    })
    data = client.get("/api/v1/health", headers=TEST_HEADERS).json()
    assert data["status"] == "degraded"
    assert data["degraded_sources"] == [
        "magi-provider", "firstmate-execution-interface",
    ]
    assert data["last_execution_event_at"] is None


def test_health_degrades_if_a_required_producer_drifts_after_startup(monkeypatch):
    import app.main as gateway

    monkeypatch.setattr(gateway, "magi_chat_readiness", lambda: {
        "status": "configured", "enabled": True,
        "provider": "openai", "live_probe_performed": False,
    })
    monkeypatch.setattr(gateway.fm_client, "get_execution_interface_readiness", lambda: {
        "schema_version": "firstmate.execution-interface-readiness.v1",
        "status": "configured", "delegation": "explicit-authorized-action-only",
        "event_ingress": "ready", "live_probe_performed": False,
    })
    monkeypatch.setattr(gateway.fm_client, "get_producer_readiness", lambda: {
        "schema_version": "firstmate-producer-readiness.v1",
        "required": True,
        "expected_commit": "2af0d17014cb2e244aa441bfe6df16c4f630475b",
        "status": "contract-invalid",
        "activated": False,
    })

    data = client.get("/api/v1/health", headers=TEST_HEADERS).json()
    assert data["status"] == "degraded"
    assert data["degraded_sources"] == ["firstmate-producer"]


def test_health_never_reports_a_live_firstmate_home_or_herdr_identity(monkeypatch):
    import app.main as gateway

    monkeypatch.setattr(gateway.fm_client, "get_execution_interface_readiness", lambda: {
        "schema_version": "firstmate.execution-interface-readiness.v1",
        "status": "unavailable", "delegation": "explicit-authorized-action-only",
        "event_ingress": "ready", "live_probe_performed": False,
    })
    data = client.get("/api/v1/health", headers=TEST_HEADERS).json()
    assert data["status"] == "degraded"
    assert "firstmate-execution-interface" in data["degraded_sources"]
    assert data["firstmate_home"] is None
    assert data["firstmate_available"] is False
    assert data["herdr_socket_connected"] is False


def test_soak_diagnostics_include_only_bounded_pi_ownership_state():
    response = client.get('/api/v1/diagnostics/soak', headers=TEST_HEADERS)
    assert response.status_code == 200
    ownership = response.json()['pi_semantic_ownership']
    assert ownership['schema_version'] == 'pi-semantic-ownership-diagnostics.v1'
    assert set(ownership['dispatch_state_counts']) == {
        'prepared', 'bound', 'finalized', 'failed',
    }
    assert isinstance(ownership['recovery_backlog_count'], int)
    serialized = str(ownership)
    for secret_field in ('capability_enc', 'prompt_enc', 'assistant_content', 'source_sequence'):
        assert secret_field not in serialized


def test_runtime():
    resp = client.get("/api/v1/runtime", headers=TEST_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "herdr" in data
    assert "firstmate" in data


def test_fleet():
    resp = client.get("/api/v1/fleet", headers=TEST_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema"] == "magistrate.fleet-projection.v1"
    assert data["source"] == "persisted-structured-state"
    assert data["live_process_probe"] is False


def test_usage_returns_quota_axi_summary(monkeypatch):
    async def fake_usage(provider=None):
        return {'source': 'quota-axi', 'providers': [{'provider': 'codex', 'status': 'fresh', 'windows': []}]}

    monkeypatch.setattr('app.main.get_usage', fake_usage)
    resp = client.get('/api/v1/usage', headers=TEST_HEADERS)
    assert resp.status_code == 200
    assert resp.json()['source'] == 'quota-axi'


def test_attention():
    resp = client.get("/api/v1/attention", headers=TEST_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_static_spa_deep_links_serve_the_exported_frontend():
    for path in ('/chat', '/voice'):
        response = client.get(path)
        assert response.status_code == 200
        assert '<!DOCTYPE html>' in response.text
        assert 'Magistrate' in response.text


def test_unknown_api_route_is_not_captured_by_spa_fallback():
    response = client.get('/api/v1/does-not-exist', headers=TEST_HEADERS)
    assert response.status_code == 404


def test_captain_prompt_empty():
    resp = client.post(
        "/api/v1/captain/prompt",
        headers=TEST_HEADERS,
        json={"source": "iphone", "modality": "text", "type": "prompt", "text": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_events_requires_first_frame_authentication():
    with pytest.raises(Exception):
        with client.websocket_connect('/api/v1/events') as websocket:
            websocket.send_json({'type': 'auth', 'token': 'not-a-session'})
            websocket.receive_json()


def test_events_stream_normalized_history(monkeypatch):
    async def fake_history(target, lines):
        return {'target': target, 'messages': [
            {'id': 'turn-1', 'role': 'assistant', 'kind': 'conversation', 'text': 'ready'},
            {'id': 'turn-2', 'role': 'assistant', 'kind': 'conversation', 'text': 'ready'},
        ]}

    monkeypatch.setattr('app.main.herdr_client.get_agent_history', fake_history)
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': 'captain'})
        assert websocket.receive_json() == {'type': 'connected', 'target': 'captain'}
        websocket.send_text('{"target":"agent-1"}')
        assert websocket.receive_json() == {'type': 'subscribed', 'target': 'agent-1'}
        event = websocket.receive_json()
        assert event['type'] == 'agent_history'
        assert [message['id'] for message in event['messages']] == ['turn-1', 'turn-2']
        assert [message['text'] for message in event['messages']] == ['ready', 'ready']


def test_agent_interrupt_requires_authentication():
    resp = client.post('/api/v1/agents/agent-1/interrupt')
    assert resp.status_code == 401


def test_agent_interrupt_delegates_to_herdr(monkeypatch):
    async def fake_interrupt(target):
        return {'status': 'interrupted', 'target': target}

    monkeypatch.setattr('app.main.herdr_client.interrupt_agent', fake_interrupt)
    resp = client.post('/api/v1/agents/agent-1/interrupt', headers=TEST_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {'status': 'interrupted', 'target': 'agent-1'}


def test_agent_history_delegates_to_herdr(monkeypatch):
    async def fake_history(target, lines):
        return {'target': target, 'messages': [{'role': 'user', 'kind': 'conversation', 'text': 'Hello'}]}

    monkeypatch.setattr('app.main.herdr_client.get_agent_history', fake_history)
    resp = client.get('/api/v1/agents/agent-1/history?lines=25', headers=TEST_HEADERS)
    assert resp.status_code == 200
    assert resp.json()['messages'][0]['text'] == 'Hello'


def test_agent_rename_delegates_to_herdr_and_validates_name(monkeypatch):
    async def fake_rename(target, name):
        return {'status': 'renamed', 'target': target, 'name': name}

    monkeypatch.setattr('app.main.herdr_client.rename_agent', fake_rename)
    resp = client.post('/api/v1/agents/agent-1/rename', headers=TEST_HEADERS, json={'name': 'review_agent'})
    assert resp.status_code == 200
    assert resp.json() == {'status': 'renamed', 'target': 'agent-1', 'name': 'review_agent'}

    invalid = client.post('/api/v1/agents/agent-1/rename', headers=TEST_HEADERS, json={'name': 'Invalid Name'})
    assert invalid.status_code == 422
