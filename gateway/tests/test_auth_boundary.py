import time
import pytest

from fastapi.testclient import TestClient

from app.auth import issue_session, revoke_session
from app.main import app

client = TestClient(app)


def headers(token):
    return {'Authorization': f'Bearer {token}'}


def test_session_requires_server_configured_bootstrap(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.delenv('MAGISTRATE_BOOTSTRAP_SECRET', raising=False)
    response = client.post('/api/v1/auth/session', json={})
    assert response.status_code == 503


def test_session_validation_is_protected_and_non_cacheable(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'validation-secret')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'validation-secret'})
    token = issued.json()['session_token']
    assert issued.headers['cache-control'] == 'no-store'
    validation = client.get('/api/v1/auth/session', headers=headers(token))
    assert validation.status_code == 200
    assert validation.headers['cache-control'] == 'no-store'
    assert validation.json()['authenticated'] is True
    assert validation.json()['expires_at'] > int(time.time())
    assert client.get('/api/v1/auth/session').status_code == 401


def test_session_is_opaque_scoped_and_revocable(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'correct-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'read,account')
    assert client.post('/api/v1/auth/session', json={'bootstrap_secret': 'wrong'}).status_code == 401
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'correct-secret'})
    assert issued.status_code == 200
    payload = issued.json()
    assert payload['token_type'] == 'Bearer'
    assert payload['scopes'] == ['account', 'read']
    assert 'correct-secret' not in issued.text

    assert client.get('/api/v1/health', headers=headers(payload['session_token'])).status_code == 200
    # Account scope is present, but command scope is not.
    assert client.post('/api/v1/captain/prompt', headers=headers(payload['session_token']), json={'text': 'status'}).status_code == 403
    assert client.post('/api/v1/auth/session/revoke', headers=headers(payload['session_token'])).status_code == 200
    assert client.get('/api/v1/health', headers=headers(payload['session_token'])).status_code == 401


def test_expired_session_is_rejected(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'expiry-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_TTL_SECONDS', '1')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'expiry-secret'}).json()
    now = time.time()
    monkeypatch.setattr('app.auth.time.time', lambda: now + 2)
    assert client.get('/api/v1/health', headers=headers(issued['session_token'])).status_code == 401


def test_events_authenticates_in_first_frame_without_query_secret(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'socket-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'read')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'socket-secret'}).json()

    async def history(target, lines):
        return {'target': target, 'messages': []}

    monkeypatch.setattr('app.main.herdr_client.get_agent_history', history)
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({'type': 'auth', 'token': issued['session_token'], 'target': 'captain'})
        assert websocket.receive_json() == {'type': 'connected', 'target': 'captain'}


def test_unconfigured_providers_are_honest(monkeypatch):
    for name in ('GITHUB_OAUTH_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_SECRET', 'GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET', 'TWITTER_OAUTH_CLIENT_ID', 'TWITTER_OAUTH_CLIENT_SECRET', 'DISCORD_OAUTH_CLIENT_ID', 'DISCORD_OAUTH_CLIENT_SECRET', 'JIRA_OAUTH_CLIENT_ID', 'JIRA_OAUTH_CLIENT_SECRET', 'TEAMS_OAUTH_CLIENT_ID', 'TEAMS_OAUTH_CLIENT_SECRET', 'MAGISTRATE_OAUTH_CALLBACK_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'provider-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'providers')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'provider-secret'}).json()
    response = client.get('/api/v1/auth/providers', headers=headers(issued['session_token']))
    assert response.status_code == 200
    # An unconfigured provider is now reported with the specific 'unavailable'
    # state rather than the ambiguous 'disconnected', and never with an identity.
    payload = response.json()
    assert all(item['status'] == 'unavailable' and item['username'] == '' and item['auth_url'] is None for item in payload)
    assert all(item['available'] is False and item['configuration'] == 'unavailable' for item in payload)
    assert all(item['unavailable_reason'] for item in payload)
    assert client.get('/api/v1/auth/github/connect', headers=headers(issued['session_token'])).status_code == 503


def test_authenticated_provider_connect_returns_stateful_authorization_url(monkeypatch):
    import app.main as gateway
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'oauth-secret')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'providers')
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'is_configured', lambda: True)
    monkeypatch.setattr(adapter, 'get_authorization_url', lambda state='': f'https://provider.example/authorize?state={state}')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'oauth-secret'}).json()
    response = client.get('/api/v1/auth/github/connect', params={'redirect_uri': 'magistrate://account'}, headers=headers(issued['session_token']))
    assert response.status_code == 200
    assert response.json()['provider'] == 'github'
    assert len(response.json()['auth_url'].split('state=')[1]) == 43


def test_identity_comes_from_session_not_query(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'owner-secret')
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'owner')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'account')
    issued = client.post('/api/v1/auth/session', json={'bootstrap_secret': 'owner-secret'}).json()
    response = client.get('/api/v1/account/profile?user_id=attacker', headers=headers(issued['session_token']))
    assert response.status_code == 200
    assert response.json()['user_id'] == 'owner'


def test_missing_or_query_only_credentials_are_rejected():
    assert client.get('/api/v1/fleet').status_code == 401
    assert client.get('/api/v1/fleet?token=not-a-credential').status_code == 401


@pytest.mark.parametrize('method,path,payload', [
    ('GET', '/api/v1/account/profile', None),
    ('GET', '/api/v1/auth/providers', None),
    ('GET', '/api/v1/github/pulls', None),
    ('GET', '/api/v1/fleet', None),
    ('GET', '/api/v1/attention', None),
    ('GET', '/api/v1/notifications/events', None),
    ('POST', '/api/v1/notifications/events/delivered', {'item_ids': []}),
    ('POST', '/api/v1/notifications/events/ack', {'item_ids': []}),
    ('GET', '/api/v1/voice/capabilities', None),
    ('POST', '/api/v1/voice/transcribe', None),
    ('POST', '/api/v1/voice/moves', {'utterance': 'status', 'idempotency_key': 'auth-test-1234'}),
    ('POST', '/api/v1/captain/prompt', {'text': 'status'}),
    ('GET', '/api/v1/agents', None),
    ('GET', '/api/v1/execution/settings', None),
])
def test_every_sensitive_route_family_requires_authentication(method, path, payload):
    response = client.request(method, path, json=payload)
    assert response.status_code == 401, path
