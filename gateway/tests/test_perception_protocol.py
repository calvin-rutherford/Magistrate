import time

from fastapi.testclient import TestClient

from app.auth import issue_session
from app.main import app
from app.uploads import get_upload
from conftest import TEST_HEADERS

client = TestClient(app)


def event(**overrides):
    payload = {
        'schema_version': 'magistrate.perception-event.v1',
        'event_id': 'pev_protocoltest_0001',
        'client': {
            'client_id': 'device-client-0001', 'device_class': 'headset',
            'adapter_id': 'vendor.spatial', 'adapter_version': '1.0',
        },
        'modality': 'gesture',
        'observed_at_ms': int(time.time() * 1000),
        'context': {'project_id': 'Magistrate', 'surface': 'attention'},
        'confidence': 0.95,
        'consent': {
            'captured': True, 'purpose': 'command-draft',
            'retention_seconds': 600, 'biometric_processing': False,
        },
        'intent': {
            'kind': 'attention.open', 'impact': 'low',
            'provenance': {'adapter_transform': 'gesture-map'},
        },
    }
    payload.update(overrides)
    return payload


def test_perception_identity_is_server_bound_and_contract_is_idempotent():
    response = client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=event())
    assert response.status_code == 200
    result = response.json()
    assert result['principal'] == {'id': 'default_user'}
    assert result['authorization'] == {
        'state': 'draft', 'reason': None, 'revision': 1, 'executes_action': False,
    }
    duplicate = client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=event(observed_at_ms=result['observed_at_ms']))
    assert duplicate.status_code == 200
    assert duplicate.json()['duplicate'] is True


def test_event_identity_is_scoped_to_authenticated_owner(monkeypatch):
    shared = event(event_id='pev_protocoltest_shared1')
    assert client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=shared).status_code == 200
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'perception_other_owner')
    other = issue_session('test-bootstrap-secret')['session_token']
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'default_user')
    response = client.post('/api/v1/perception/events', headers={'Authorization': f'Bearer {other}'}, json=shared)
    assert response.status_code == 200
    assert response.json()['principal']['id'] == 'perception_other_owner'


def test_low_confidence_and_neural_high_impact_never_self_authorize():
    low = event(event_id='pev_protocoltest_low1', confidence=0.2)
    result = client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=low).json()
    assert result['authorization']['state'] == 'confirmation-required'
    assert result['authorization']['reason'] == 'low-confidence'

    neural = event(
        event_id='pev_protocoltest_neural1', modality='neural', confidence=0.99,
        consent={
            'captured': True, 'purpose': 'command-draft', 'retention_seconds': 600,
            'biometric_processing': True,
        },
        intent={
            'kind': 'execution.start', 'impact': 'high',
            'provenance': {'adapter_transform': 'neural-decoder'},
        },
    )
    response = client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=neural)
    assert response.status_code == 200
    assert response.json()['authorization']['state'] == 'confirmation-required'
    assert response.json()['authorization']['executes_action'] is False

    confirmation = client.post(
        '/api/v1/perception/events/pev_protocoltest_neural1/confirm', headers=TEST_HEADERS,
        json={'schema_version': 'magistrate.perception-confirmation.v1', 'revision': 1, 'confirmed': True},
    )
    assert confirmation.status_code == 200
    assert confirmation.json()['authorization']['state'] == 'confirmed'
    assert confirmation.json()['authorization']['executes_action'] is False


def test_perception_artifact_is_owner_bound_and_retained_with_event(monkeypatch, tmp_path):
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    upload = client.post(
        '/api/v1/uploads', headers=TEST_HEADERS,
        files={'files': ('context.txt', b'ambient context', 'text/plain')},
    ).json()['uploads'][0]
    payload = event(
        event_id='pev_protocoltest_artifact1', modality='ambient', artifact_ref=upload['upload_id'],
        consent={
            'captured': True, 'purpose': 'context', 'retention_seconds': 30 * 24 * 60 * 60,
            'biometric_processing': False,
        },
    )
    response = client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=payload)
    assert response.status_code == 200
    stored = get_upload('default_user', upload['upload_id'])
    assert stored and stored['expires_at'] >= response.json()['retention']['expires_at']


def test_perception_request_envelope_is_bounded():
    oversized = event(
        event_id='pev_protocoltest_large1',
        intent={
            'kind': 'text.draft', 'impact': 'none',
            'provenance': {'adapter_transform': 'none', 'transcript': 'x' * 40_000},
        },
    )
    assert client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=oversized).status_code == 413


def test_neural_requires_explicit_biometric_consent_and_unknown_fields_fail_closed():
    missing_consent = event(event_id='pev_protocoltest_neural2', modality='neural')
    assert client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=missing_consent).status_code == 422
    with_principal = event(event_id='pev_protocoltest_extra1', principal={'id': 'forged'})
    assert client.post('/api/v1/perception/events', headers=TEST_HEADERS, json=with_principal).status_code == 422
