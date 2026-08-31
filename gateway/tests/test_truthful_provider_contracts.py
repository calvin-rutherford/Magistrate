"""Negative coverage proving a fake 'connected' provider state is unreachable.

Each test drives one class of failure that a demo build would happily paper
over - missing configuration, a stale database row, a wrong/expired credential,
and a provider error - and asserts the gateway reports the specific honest state
instead. See `_provider_connection_state` in `app/main.py` for the rule.
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as gateway
from app.db import disconnect_account, get_connected_accounts, upsert_connected_account
from app.providers.github import GitHubProviderAdapter
from conftest import TEST_HEADERS

client = TestClient(gateway.app)


def _providers() -> dict:
    response = client.get('/api/v1/auth/providers', headers=TEST_HEADERS)
    assert response.status_code == 200
    return {item['provider']: item for item in response.json()}


@pytest.fixture(autouse=True)
def _clean_github_account():
    disconnect_account('default_user', 'github')
    yield
    disconnect_account('default_user', 'github')


def _configure(monkeypatch, provider='github'):
    monkeypatch.setattr(gateway.providers[provider], 'is_configured', lambda: True)


# --- Missing configuration -------------------------------------------------

def test_missing_configuration_renders_unavailable_never_connected(monkeypatch):
    for name in ('GITHUB_OAUTH_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_SECRET', 'MAGISTRATE_OAUTH_CALLBACK_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    # Even with a fully "connected" database row and a live credential, an
    # unconfigured deployment cannot present a connection.
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'],
                             access_token='gho_configuration_removed')
    entry = _providers()['github']
    assert entry['status'] == 'unavailable'
    assert entry['available'] is False
    assert entry['configuration'] == 'unavailable'
    assert entry['username'] == ''
    assert 'not configured' in entry['unavailable_reason']


def test_missing_configuration_refuses_to_mint_a_connect_url(monkeypatch):
    for name in ('GITHUB_OAUTH_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_SECRET', 'MAGISTRATE_OAUTH_CALLBACK_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    response = client.get('/api/v1/auth/github/connect', headers=TEST_HEADERS)
    assert response.status_code == 503
    assert 'unavailable' in response.json()['detail']


# --- Stale rows and missing/expired credentials ----------------------------

def test_connected_row_without_a_credential_is_not_connected(monkeypatch):
    _configure(monkeypatch)
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'],
                             access_token='')
    entry = _providers()['github']
    assert entry['status'] == 'disconnected'
    assert entry['username'] == ''
    assert 'missing' in entry['unavailable_reason']


def test_expired_credential_renders_expired_not_connected(monkeypatch):
    _configure(monkeypatch)
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'],
                             access_token='gho_expired')
    from app import db
    import sqlite3
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('UPDATE oauth_credentials SET expires_at = ? WHERE connected_account_id = ?',
                     (int(time.time()) - 60, 'default_user_github'))
    entry = _providers()['github']
    assert entry['status'] == 'expired'
    assert 'expired' in entry['unavailable_reason']


def test_configured_unexpired_credential_is_the_only_connected_path(monkeypatch):
    _configure(monkeypatch)
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'],
                             access_token='gho_live')
    entry = _providers()['github']
    assert entry['status'] == 'connected'
    assert entry['username'] == 'octocat'
    assert entry['unavailable_reason'] is None


def test_disconnect_removes_the_credential_so_it_cannot_be_reused(monkeypatch):
    _configure(monkeypatch)
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'],
                             access_token='gho_live')
    assert client.post('/api/v1/auth/github/disconnect', headers=TEST_HEADERS).status_code == 200
    account = next(item for item in get_connected_accounts('default_user') if item['provider'] == 'github')
    assert account['status'] == 'disconnected'
    assert account['has_credential'] is False
    # Re-marking the row connected without a new grant must still not connect.
    upsert_connected_account(user_id='default_user', provider='github', provider_username='octocat',
                             provider_user_id='583231', status='connected', scopes=['repo'], access_token='')
    assert _providers()['github']['status'] == 'disconnected'


# --- Deferred providers ----------------------------------------------------

@pytest.mark.parametrize('provider', ['jira', 'teams'])
def test_deferred_providers_are_visibly_unavailable(provider):
    entry = _providers()[provider]
    assert entry['deferred'] is True
    assert entry['available'] is False
    assert entry['status'] == 'unavailable'
    assert 'deferred' in entry['unavailable_reason']


@pytest.mark.parametrize('provider', ['jira', 'teams'])
def test_deferred_providers_cannot_be_connected_by_a_database_row(provider):
    upsert_connected_account(user_id='default_user', provider=provider, provider_username='someone',
                             provider_user_id='abc123', status='connected', scopes=['read'],
                             access_token='definitely-not-real')
    try:
        entry = _providers()[provider]
        assert entry['status'] == 'unavailable'
        assert entry['username'] == ''
    finally:
        disconnect_account('default_user', provider)


@pytest.mark.parametrize('provider', ['jira', 'teams'])
def test_deferred_providers_contribute_no_records(provider):
    path = '/api/v1/jira/issues' if provider == 'jira' else '/api/v1/teams/mentions'
    response = client.get(path, headers=TEST_HEADERS)
    assert response.status_code == 200
    assert response.json() == []


# --- Provider upstream errors ---------------------------------------------

def _transport(handler):
    return httpx.MockTransport(handler)


def _patch_httpx(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs['transport'] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', factory)


@pytest.mark.asyncio
async def test_rejected_authorization_code_raises_instead_of_decoding(monkeypatch):
    _patch_httpx(monkeypatch, lambda request: httpx.Response(200, json={'error': 'bad_verification_code'}))
    adapter = GitHubProviderAdapter(client_id='id', client_secret='secret', redirect_uri='https://example.test')
    with pytest.raises(RuntimeError, match='refused the authorization code'):
        await adapter.exchange_code('nope')


@pytest.mark.asyncio
async def test_non_2xx_token_response_raises(monkeypatch):
    _patch_httpx(monkeypatch, lambda request: httpx.Response(502, text='bad gateway'))
    adapter = GitHubProviderAdapter(client_id='id', client_secret='secret', redirect_uri='https://example.test')
    with pytest.raises(RuntimeError, match='HTTP 502'):
        await adapter.exchange_code('code')


@pytest.mark.asyncio
async def test_wrong_or_expired_credential_is_rejected_not_stored(monkeypatch):
    _patch_httpx(monkeypatch, lambda request: httpx.Response(401, json={'message': 'Bad credentials'}))
    adapter = GitHubProviderAdapter(client_id='id', client_secret='secret', redirect_uri='https://example.test')
    with pytest.raises(RuntimeError, match='wrong, expired, or lacks'):
        await adapter.get_user_profile('gho_wrong')


@pytest.mark.asyncio
async def test_profile_without_an_identity_is_rejected(monkeypatch):
    _patch_httpx(monkeypatch, lambda request: httpx.Response(200, json={'message': 'nothing useful'}))
    adapter = GitHubProviderAdapter(client_id='id', client_secret='secret', redirect_uri='https://example.test')
    with pytest.raises(RuntimeError, match='authenticated identity'):
        await adapter.get_user_profile('gho_odd')


def test_numeric_provider_identity_is_accepted(monkeypatch):
    """A real GitHub profile returns an int id; that must still connect."""
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'is_configured', lambda: True)

    async def exchange(code):
        return {'access_token': 'gho_real'}

    async def profile(token):
        return {'login': 'octocat', 'id': 583231}

    monkeypatch.setattr(adapter, 'exchange_code', exchange)
    monkeypatch.setattr(adapter, 'get_user_profile', profile)
    state = gateway.oauth_transaction_store.create(
        principal_id='default_user', provider='github', redirect_uri='magistrate://account')
    response = client.get('/api/v1/auth/github/callback', params={'code': 'ok', 'state': state},
                          follow_redirects=False)
    assert response.status_code in (302, 307)
    assert 'status=success' in response.headers['location']
    assert _providers()['github']['status'] == 'connected'


def test_provider_error_during_callback_never_stores_a_connection(monkeypatch):
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'is_configured', lambda: True)

    async def exchange(code):
        raise RuntimeError('GitHub refused the authorization code exchange.')

    monkeypatch.setattr(adapter, 'exchange_code', exchange)
    state = gateway.oauth_transaction_store.create(
        principal_id='default_user', provider='github', redirect_uri='magistrate://account')
    response = client.get('/api/v1/auth/github/callback', params={'code': 'bad', 'state': state},
                          follow_redirects=False)
    assert response.status_code in (302, 307)
    assert 'error=oauth_failed' in response.headers['location']
    # The redirect carries no provider text and no connection was recorded.
    assert 'refused' not in response.headers['location']
    assert _providers()['github']['status'] != 'connected'


# --- Health / runtime honesty ---------------------------------------------

def test_health_never_reports_an_unobserved_herdr_version(monkeypatch):
    async def empty_snapshot():
        return {}

    monkeypatch.setattr(gateway.herdr_client, 'get_snapshot', empty_snapshot)
    payload = client.get('/api/v1/health', headers=TEST_HEADERS).json()
    assert payload['status'] == 'degraded'
    assert 'herdr' in payload['degraded_sources']
    assert payload['herdr_version'] is None
    assert payload['herdr_socket_connected'] is False


def test_runtime_never_reports_an_unobserved_version_or_protocol(monkeypatch):
    async def empty_snapshot():
        return {}

    monkeypatch.setattr(gateway.herdr_client, 'get_snapshot', empty_snapshot)
    payload = client.get('/api/v1/runtime', headers=TEST_HEADERS).json()
    assert payload['herdr']['status'] == 'disconnected'
    assert payload['herdr']['version'] is None
    assert payload['herdr']['protocol'] is None


# --- Attachment processing state ------------------------------------------

@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    """Keep upload bytes in pytest's tmp tree, never in the checkout."""
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'files'))
    return tmp_path


def test_upload_reports_the_real_stored_state_not_bare_success(upload_root):
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                           files=[('files', ('note.txt', b'truthful bytes', 'text/plain'))])
    assert response.status_code == 200
    record = response.json()['uploads'][0]
    assert record['status'] == 'stored'
    assert record['attached'] is False
    assert record['size'] == len(b'truthful bytes')


def test_upload_attached_state_reflects_a_real_association(upload_root):
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                           data={'message_id': 'u-truthful-attach'},
                           files=[('files', ('note.txt', b'attached bytes', 'text/plain'))])
    assert response.status_code == 200
    record = response.json()['uploads'][0]
    assert record['status'] == 'stored'
    assert record['attached'] is True


def test_rejected_upload_reports_an_error_rather_than_a_stored_state(upload_root):
    response = client.post('/api/v1/uploads', headers=TEST_HEADERS,
                           files=[('files', ('evil.png', b'not really a png', 'image/png'))])
    assert response.status_code == 422
    assert 'uploads' not in response.json()
