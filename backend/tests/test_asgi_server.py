"""Retained-lab server qualification only: task-owned loopback sockets and DB.

No Compose, Celery, provider, Docker socket, Fleet or live state is activated.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Permission, User
from django.test import Client
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from agents import consumers
from og_broker.asgi import application


def test_no_daphne_or_twisted_and_production_stays_quarantined():
    assert importlib.util.find_spec('daphne') is None
    assert importlib.util.find_spec('twisted') is None
    result = subprocess.run([sys.executable, '-c', 'import og_broker.settings'],
                            env={**os.environ, 'MAGISTRATE_ENV': 'production', 'PYTHON_DOTENV_DISABLED': '1'},
                            capture_output=True, timeout=5)
    assert result.returncode != 0
    assert b'Legacy Django is quarantined' in result.stderr
    root = Path(__file__).resolve().parents[2]
    compose = (root / 'docker-compose.yml').read_text()
    assert '127.0.0.1:8000:8000' in compose
    assert compose.count('profiles: [legacy-lab]') == 6
    for file in ['Dockerfile', 'docker-compose.yml']:
        text = (root / file).read_text()
        for option in ['h11', 'websockets-sansio', '--no-proxy-headers', '--no-access-log']:
            assert option in text


@pytest.mark.django_db(transaction=True)
def test_uvicorn_http_websocket_auth_origin_permission_and_shutdown(monkeypatch):
    observer = User.objects.create_user(username='socket-observer', password='fixture-password')
    dispatcher = User.objects.create_user(username='socket-dispatcher', password='fixture-password')
    dispatcher.user_permissions.add(Permission.objects.get(content_type__app_label='agents',
                                                           content_type__model='fleet', codename='add_fleet'))
    cookies = []
    for user in [observer, dispatcher]:
        client = Client()
        client.force_login(user)
        cookies.append(f"sessionid={client.cookies['sessionid'].value}")
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    scopes = []

    async def measured_app(scope, receive, send):
        scopes.append(scope)
        await application(scope, receive, send)

    async def scenario():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.setblocking(False)
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(measured_app, http='h11', ws='websockets-sansio',
                lifespan='off', proxy_headers=False, access_log=False, log_config=None,
                timeout_graceful_shutdown=2))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                for _ in range(200):
                    if server.started:
                        break
                    if task.done():
                        await task
                        raise AssertionError('Server stopped before readiness')
                    await asyncio.sleep(0.01)
                assert server.started

                async def request(raw):
                    reader, writer = await asyncio.open_connection('127.0.0.1', port)
                    try:
                        writer.write(raw)
                        await writer.drain()
                        return await asyncio.wait_for(reader.read(), timeout=3)
                    finally:
                        writer.close()
                        await writer.wait_closed()

                response = await request(b'GET /admin/login/ HTTP/1.1\r\nHost: localhost\r\nX-Forwarded-For: 198.51.100.17\r\nConnection: close\r\n\r\n')
                assert response.startswith(b'HTTP/1.1 200')
                assert scopes[-1]['client'][0] == '127.0.0.1'  # no trusted proxy expansion
                csrf = await request(b'POST /admin/login/ HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                assert csrf.startswith(b'HTTP/1.1 403')
                bad = await request(b'POST /admin/login/ HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nContent-Length: 1\r\nConnection: close\r\n\r\n')
                assert bad.startswith(b'HTTP/1.1 400')
                uri = f'ws://127.0.0.1:{port}/ws/magistrate/'
                for origin, headers in [('http://localhost:8000', {}),
                                        ('https://attacker.example', {'Cookie': cookies[1]})]:
                    with pytest.raises(InvalidStatus) as failure:
                        async with connect(uri, origin=origin, additional_headers=headers, open_timeout=3):
                            raise AssertionError('Unauthorized socket accepted')
                    assert failure.value.response.status_code == 403
                async with connect(uri, origin='http://localhost:8000', additional_headers={'Cookie': cookies[0]}) as ws:
                    await ws.send(json.dumps({'command': 'not permitted'}))
                    with pytest.raises(ConnectionClosedError) as failure:
                        await ws.recv()
                    assert failure.value.rcvd.code == consumers.CLOSE_FORBIDDEN
                dispatch.assert_not_awaited()
                async with connect(uri, origin='http://localhost:8000', additional_headers={'Cookie': cookies[1]}) as ws:
                    await ws.send(json.dumps({'command': 'synthetic authorized command'}))
                    assert json.loads(await ws.recv()) == {'message': 'Fleet Dispatched: synthetic authorized command'}
                    await ws.send(b'binary is not a command')
                    with pytest.raises(ConnectionClosedError) as failure:
                        await ws.recv()
                    assert failure.value.rcvd.code == consumers.CLOSE_INVALID_COMMAND
                dispatch.assert_awaited_once_with('synthetic authorized command')
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, timeout=5)
    async_to_sync(scenario)()
