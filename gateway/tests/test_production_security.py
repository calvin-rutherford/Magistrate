"""Adversarial HTTP and two-principal storage tests; no live credentials or workers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import sqlite3

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import db, telemetry, uploads
from app.auth import issue_session
from app.main import app
from app.magi_model import MagiModelError, MagiModelMessage, OpenAIMagiModel
from app.magi_providers import AnthropicMagiModel, GoogleMagiModel
from app.production_security import cors_origins, validate_production_configuration, validate_provider_url
from app.request_boundary import RequestBoundary, DEFAULT_BODY_LIMIT


@pytest.fixture
def tenants(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'security.sqlite3'))
    monkeypatch.setenv('MAGISTRATE_CHAT_UPLOAD_DIR', str(tmp_path / 'private'))
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'security-test')
    monkeypatch.setenv('MAGISTRATE_SESSION_SCOPES', 'read,account,command')
    headers = []
    for owner in ('tenant-a', 'tenant-b'):
        monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', owner)
        issued = issue_session('security-test')
        headers.append({'Authorization': 'Bearer ' + issued['session_token']})
    return TestClient(app), headers[0], headers[1]


def test_two_tenant_upload_theft_association_and_replay(tenants):
    client, a, b = tenants
    uploaded = client.post('/api/v1/uploads', headers=a,
                           files={'files': ('../../secret.txt', b'private-a', 'text/plain')}).json()['uploads'][0]
    path = '/api/v1/uploads/' + uploaded['upload_id']
    assert client.get(path, headers=b).status_code == 404
    assert client.get(path + '?user_id=tenant-a', headers=b).status_code == 404
    assert client.get(path, headers=a).content == b'private-a'
    assert client.post('/api/v1/magi/messages', headers=b, json={
        'client_message_id': 'attack-cross-tenant-0001', 'content': 'read this',
        'attachments': [{k: uploaded[k] for k in ('upload_id', 'filename', 'media_type', 'size')}],
    }).status_code == 404
    assert client.post('/api/v1/auth/session/revoke', headers=a).status_code == 200
    assert client.get(path, headers=a).status_code == 401
    # Revoking A must not retire B's independent session.
    assert client.get('/api/v1/account/profile', headers=b).status_code == 200


def test_two_tenant_quota_is_atomic_and_failed_batch_does_not_leak_disk(tenants, monkeypatch):
    client, a, b = tenants
    monkeypatch.setenv('MAGISTRATE_UPLOAD_USER_BYTES', '8')
    assert client.post('/api/v1/uploads', headers=a, files=[
        ('files', ('first.txt', b'1234', 'text/plain')),
        ('files', ('second.txt', b'12345', 'text/plain')),
    ]).status_code == 422
    assert not [path for path in uploads._root().rglob('*') if path.is_file()]
    with ThreadPoolExecutor(max_workers=4) as pool:
        def attempt(_):
            try:
                uploads.save_upload('tenant-a', 'ok.txt', 'text/plain', b'1234')
                return True
            except ValueError:
                return False
        assert sum(pool.map(attempt, range(6))) == 2
    assert len([path for path in uploads._root().rglob('*') if path.is_file()]) == 2
    assert client.post('/api/v1/uploads', headers=b,
                       files={'files': ('ok.txt', b'12345678', 'text/plain')}).status_code == 200


def test_upload_symlink_or_tampered_database_path_cannot_exfiltrate(tenants, tmp_path):
    client, a, _ = tenants
    saved = uploads.save_upload('tenant-a', 'ok.txt', 'text/plain', b'1234')
    row = uploads.get_upload('tenant-a', saved['upload_id'])
    path = Path(row['path'])
    assert path.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / 'operator-secret'
    outside.write_bytes(b'secret')
    path.unlink()
    path.symlink_to(outside)
    assert client.get('/api/v1/uploads/' + saved['upload_id'], headers=a).status_code == 404
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('UPDATE chat_uploads SET path=? WHERE upload_id=?', (str(outside), saved['upload_id']))
    assert uploads.get_upload('tenant-a', saved['upload_id']) is None
    assert outside.read_bytes() == b'secret'
    # Legacy rows cannot turn a database path into arbitrary file authority.
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('UPDATE chat_uploads SET object_key=NULL, path=? WHERE upload_id=?',
                     (str(outside), saved['upload_id']))
    assert uploads.get_upload('tenant-a', saved['upload_id']) is None


def test_avatar_cannot_publish_html_or_choose_a_filesystem_path(tenants, monkeypatch, tmp_path):
    import app.main as main
    client, a, _ = tenants
    monkeypatch.setattr(main, 'UPLOADS_DIR', tmp_path)
    for name, media in [('attack.html', 'text/html'), ('attack.svg', 'image/svg+xml'), ('attack.png', 'image/png')]:
        assert client.post('/api/v1/account/avatar', headers=a,
                           files={'file': (name, b'<script>alert(1)</script>', media)}).status_code == 422
    response = client.post('/api/v1/account/avatar', headers=a, files={
        'file': ('../../attack.html', b'\x89PNG\r\n\x1a\nimage', 'image/png'),
    })
    assert response.status_code == 200
    assert response.json()['avatar_url'].endswith('.png')
    assert 'attack' not in response.json()['avatar_url']
    assert 'tenant-a' not in response.json()['avatar_url']
    assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [[], [(b'content-length', b'1')]])
async def test_actual_bytes_bounded_without_trusting_content_length(headers):
    inner = FastAPI()
    parsed = []

    @inner.post('/json')
    async def ingest(request: Request):
        parsed.append(await request.body())
        return {'ok': True}

    boundary = RequestBoundary(inner)
    messages = iter([
        {'type': 'http.request', 'body': b'x' * 600_000, 'more_body': True},
        {'type': 'http.request', 'body': b'x' * 600_000, 'more_body': False},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await boundary({'type': 'http', 'method': 'POST', 'path': '/json', 'headers': headers,
                    'query_string': b'', 'scheme': 'http', 'server': ('test', 80)}, receive, send)
    assert sent[0]['status'] == 413
    assert parsed == []
    assert boundary.inflight == 0


@pytest.mark.asyncio
async def test_slow_body_and_concurrency_fail_closed():
    inner = FastAPI()
    entered = asyncio.Event()
    release = asyncio.Event()

    @inner.get('/wait')
    async def wait():
        entered.set()
        await release.wait()
        return {}

    boundary = RequestBoundary(inner, max_inflight=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary), base_url='http://test') as client:
        first = asyncio.create_task(client.get('/wait'))
        await entered.wait()
        assert (await client.get('/wait')).status_code == 503
        release.set()
        assert (await first).status_code == 200
    assert boundary.inflight == 0

    @inner.post('/slow')
    async def slow(request: Request):
        await request.body()
        return {}

    async def receive():
        await asyncio.sleep(1)

    sent = []
    async def send(message):
        sent.append(message)

    await RequestBoundary(inner, body_timeout=0.01)(
        {'type': 'http', 'method': 'POST', 'path': '/slow', 'headers': [],
         'query_string': b'', 'scheme': 'http', 'server': ('test', 80)}, receive, send,
    )
    assert sent[0]['status'] == 408


def test_logs_metrics_and_errors_do_not_reflect_secrets(tenants, monkeypatch, caplog):
    client, a, _ = tenants
    caplog.set_level(logging.INFO, logger='magistrate.operations')
    secret = 'ghp_SUPER_SECRET_prompt_and_query'
    response = client.get('/api/v1/uploads/' + secret + '?code=' + secret,
                          headers={**a, 'X-Request-ID': secret})
    assert secret not in response.headers['x-request-id']
    records = [json.loads(r.message) for r in caplog.records if r.name == 'magistrate.operations']
    assert records[-1]['request_id'] == response.headers['x-request-id']
    assert secret not in json.dumps(records)
    assert secret not in telemetry.prometheus_metrics()
    monkeypatch.setenv('MAGISTRATE_METRICS_TOKEN', 'operator-scrape-secret' * 2)
    assert client.get('/internal/metrics', headers=a).status_code == 401
    assert client.get('/internal/metrics', headers={'Authorization': 'Bearer ' + 'operator-scrape-secret' * 2}).status_code == 200
    errors = []
    telemetry.set_error_reporter(errors.append)
    try:
        with pytest.raises(ValueError), telemetry.operation_span('provider'):
            raise ValueError(secret)
    finally:
        telemetry.set_error_reporter(None)
    assert errors[0]['outcome'] == 'error'
    assert secret not in json.dumps(errors)


@pytest.mark.parametrize('origin', ['http://localhost.evil.test', 'https://ok.test/path',
    'https://user:pass@ok.test', '*', '', 'ftp://ok.test', 'https://ok.test?code=secret'])
def test_production_cors_rejects_confused_origins(monkeypatch, origin):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.setenv('MAGISTRATE_CORS_ORIGINS', origin)
    with pytest.raises(RuntimeError):
        cors_origins()


@pytest.mark.parametrize('url', ['http://api.openai.com/v1', 'https://169.254.169.254/latest',
    'https://127.0.0.1/v1', 'https://api.openai.com.evil.com/v1',
    'https://api.openai.com:444/v1', 'https://user:pass@api.openai.com/v1'])
def test_provider_credentials_cannot_be_sent_to_unapproved_host(monkeypatch, url):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    with pytest.raises(RuntimeError):
        validate_provider_url(url)


def test_production_configuration_rejects_legacy_and_weak_authority(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.setenv('MAGISTRATE_CORS_ORIGINS', 'https://app.magistrate.com')
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'short')
    with pytest.raises(RuntimeError):
        validate_production_configuration()
    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_SECRET', 'a' * 48)
    validate_production_configuration()
    monkeypatch.setenv('MAGISTRATE_LEGACY_CHAT_ENABLED', 'true')
    with pytest.raises(RuntimeError):
        validate_production_configuration()


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', [OpenAIMagiModel, AnthropicMagiModel, GoogleMagiModel])
async def test_provider_redirect_and_huge_response_are_not_followed_or_parsed(adapter):
    calls = []
    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://attacker.test'})
    model = adapter(api_key='secret', transport=httpx.MockTransport(redirect))
    with pytest.raises(MagiModelError):
        await model.complete([MagiModelMessage('user', 'hello')], system_context='', request_id='id')
    assert len(calls) == 1
    model = adapter(api_key='secret', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b'x' * (4 * 1024 * 1024 + 1))))
    with pytest.raises(MagiModelError, match='provider_invalid_response'):
        await model.complete([MagiModelMessage('user', 'hello')], system_context='', request_id='id')


@pytest.mark.parametrize('adapter', [OpenAIMagiModel, AnthropicMagiModel, GoogleMagiModel])
def test_all_routed_adapters_enforce_operator_endpoint_allowlist(monkeypatch, adapter):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    with pytest.raises(RuntimeError):
        adapter(api_key='secret', base_url='https://attacker.com/v1')
    adapter(api_key='secret')  # Each built-in provider has an explicit default host.


def test_object_storage_cannot_overlap_public_avatars(tenants, monkeypatch):
    monkeypatch.setenv('MAGISTRATE_AVATAR_DIR', str(uploads._root().parent))
    with pytest.raises(ValueError, match='overlap'):
        uploads.save_upload('tenant-a', 'secret.txt', 'text/plain', b'secret')


def test_readiness_does_not_claim_upstream_health(tenants, monkeypatch):
    client, _, _ = tenants
    monkeypatch.setattr(app.state, 'startup_complete', False)
    assert client.get('/livez').status_code == 200
    assert client.get('/readyz').status_code == 503
    monkeypatch.setattr(app.state, 'startup_complete', True)
    monkeypatch.setenv('OPENAI_API_KEY', 'configured-not-probed')
    assert client.get('/readyz').json() == {'status': 'ready'}
    monkeypatch.setattr(db, 'DB_PATH', '/does-not-exist/unavailable.sqlite3')
    assert client.get('/readyz').status_code == 503
