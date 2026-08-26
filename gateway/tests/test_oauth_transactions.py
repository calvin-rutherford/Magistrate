from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import app.db as database
import app.main as gateway
from app.main import app
from app.oauth_transactions import (
    DEFAULT_REDIRECT_URI,
    OAuthTransactionError,
    OAuthTransactionStore,
    REDIRECT_URI_ENV,
)


client = TestClient(app)
HEADERS = {'X-Magistrate-Token': 'magistrate-device-token-12345'}


@pytest.fixture
def transaction_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'oauth.sqlite3'))
    return OAuthTransactionStore()


def state_from_connect(user_id='alice', redirect_uri=DEFAULT_REDIRECT_URI, provider='github'):
    response = client.get(
        f'/api/v1/auth/{provider}/connect',
        params={'user_id': user_id, 'redirect_uri': redirect_uri},
        headers=HEADERS,
        follow_redirects=False,
    )
    return response, parse_qs(urlsplit(response.headers['location']).query)['state'][0]


def test_connect_uses_opaque_state_and_allowlisted_redirect(transaction_db):
    response, state = state_from_connect()

    assert response.status_code == 307
    assert '::' not in state
    transaction = transaction_db.consume(state, 'github', now=1)
    assert transaction.principal_id == 'alice'
    assert transaction.redirect_uri == DEFAULT_REDIRECT_URI


def test_connect_rejects_disallowed_redirect(transaction_db):
    for redirect_uri in ('https://attacker.example/callback', 'javascript://account'):
        response = client.get(
            '/api/v1/auth/github/connect',
            params={'redirect_uri': redirect_uri},
            headers=HEADERS,
            follow_redirects=False,
        )

        assert response.status_code == 400
        assert 'Disallowed OAuth redirect' in response.json()['detail']


def test_connect_allows_explicit_loopback_redirect(transaction_db, monkeypatch):
    redirect_uri = 'http://127.0.0.1:19006/account'
    monkeypatch.setenv(REDIRECT_URI_ENV, redirect_uri)

    response, state = state_from_connect(redirect_uri=redirect_uri)

    assert response.status_code == 307
    assert parse_qs(urlsplit(response.headers['location']).query)['state'][0] == state


def test_store_rejects_principal_mismatch_without_consuming(transaction_db):
    state = transaction_db.create('alice', 'github', DEFAULT_REDIRECT_URI, now=100)

    with pytest.raises(OAuthTransactionError, match='principal mismatch'):
        transaction_db.consume(state, 'github', expected_principal='bob', now=101)
    assert transaction_db.consume(state, 'github', expected_principal='alice', now=101).principal_id == 'alice'


@pytest.mark.parametrize(
    ('state', 'provider', 'expected_error'),
    [
        (None, 'github', 'Missing state'),
        ('not-a-state', 'github', 'Malformed OAuth state'),
        ('A' * 43, 'github', 'Unknown OAuth state'),
    ],
)
def test_callback_rejects_invalid_state_without_exchange_or_write(
    transaction_db, monkeypatch, state, provider, expected_error
):
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'exchange_code', AsyncMock(side_effect=AssertionError('exchange must not run')))
    monkeypatch.setattr(gateway, 'upsert_connected_account', lambda **kwargs: pytest.fail('credential write'))

    response = client.get(
        f'/api/v1/auth/{provider}/callback',
        params={'state': state} if state is not None else {},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()['error'] == expected_error


def test_callback_rejects_expired_state_without_exchange_or_write(transaction_db, monkeypatch):
    state = transaction_db.create('alice', 'github', DEFAULT_REDIRECT_URI, now=100, ttl_seconds=1)
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'exchange_code', AsyncMock(side_effect=AssertionError('exchange must not run')))
    monkeypatch.setattr(gateway.oauth_transaction_store, 'consume', lambda *args, **kwargs: transaction_db.consume(*args, now=102, **kwargs))
    monkeypatch.setattr(gateway, 'upsert_connected_account', lambda **kwargs: pytest.fail('credential write'))

    response = client.get('/api/v1/auth/github/callback', params={'state': state, 'code': 'code'})

    assert response.status_code == 400
    assert response.json()['error'] == 'Expired OAuth state'


def test_callback_rejects_provider_mismatch_without_exchange_or_write(transaction_db, monkeypatch):
    state = transaction_db.create('alice', 'github', DEFAULT_REDIRECT_URI, now=100)
    adapter = gateway.providers['twitter']
    monkeypatch.setattr(adapter, 'exchange_code', AsyncMock(side_effect=AssertionError('exchange must not run')))
    monkeypatch.setattr(gateway.oauth_transaction_store, 'consume', lambda *args, **kwargs: transaction_db.consume(*args, now=101, **kwargs))
    monkeypatch.setattr(gateway, 'upsert_connected_account', lambda **kwargs: pytest.fail('credential write'))

    response = client.get('/api/v1/auth/twitter/callback', params={'state': state, 'code': 'code'})

    assert response.status_code == 400
    assert response.json()['error'] == 'OAuth state provider mismatch'


def test_successful_callback_consumes_once_before_exchange_and_writes_once(transaction_db, monkeypatch):
    response, state = state_from_connect(user_id='alice')
    assert response.status_code == 307
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'exchange_code', AsyncMock(return_value={'access_token': 'access-token'}))
    monkeypatch.setattr(adapter, 'get_user_profile', AsyncMock(return_value={'username': 'alice-gh'}))
    write = Mock(wraps=gateway.upsert_connected_account)
    monkeypatch.setattr(gateway, 'upsert_connected_account', write)

    callback = client.get(
        '/api/v1/auth/github/callback',
        params={'state': state, 'code': 'authorization-code'},
        follow_redirects=False,
    )

    assert callback.status_code == 307
    assert callback.headers['location'] == 'magistrate://account?status=success'
    assert write.call_count == 1
    assert write.call_args.kwargs['user_id'] == 'alice'

    replay = client.get(
        '/api/v1/auth/github/callback',
        params={'state': state, 'code': 'authorization-code'},
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert replay.json()['error'] == 'OAuth state already consumed'
    assert adapter.exchange_code.call_count == 1


def test_exchange_failure_redirects_safely_without_credential_write(transaction_db, monkeypatch):
    response, state = state_from_connect(user_id='alice')
    assert response.status_code == 307
    adapter = gateway.providers['github']
    monkeypatch.setattr(adapter, 'exchange_code', AsyncMock(side_effect=RuntimeError('provider failure')))
    write = Mock()
    monkeypatch.setattr(gateway, 'upsert_connected_account', write)

    callback = client.get(
        '/api/v1/auth/github/callback',
        params={'state': state, 'code': 'authorization-code'},
        follow_redirects=False,
    )

    assert callback.status_code == 307
    assert callback.headers['location'] == 'magistrate://account?error=oauth_failed'
    write.assert_not_called()
