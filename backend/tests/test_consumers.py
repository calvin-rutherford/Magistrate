import asyncio
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import Permission, User
from django.test import Client

from agents import consumers
from og_broker.asgi import application


WS_PATH = '/ws/magistrate/'
ALLOWED_ORIGIN = 'http://localhost:8000'


def run(coro):
    return async_to_sync(coro)()


def session_cookie(user):
    client = Client()
    client.force_login(user)
    return client.cookies['sessionid'].value


def communicator(user, origin=ALLOWED_ORIGIN):
    headers = [(b'origin', origin.encode())]
    if user is not None:
        headers.append((b'cookie', f'sessionid={session_cookie(user)}'.encode()))
    return WebsocketCommunicator(application, WS_PATH, headers=headers)


@pytest.fixture
def observer(db):
    return User.objects.create_user(username='observer', password='password')


@pytest.fixture
def dispatcher(db):
    user = User.objects.create_user(username='dispatcher', password='password')
    permission = Permission.objects.get(
        content_type__app_label='agents',
        content_type__model='fleet',
        codename='add_fleet',
    )
    user.user_permissions.add(permission)
    return user


@pytest.mark.django_db(transaction=True)
def test_anonymous_socket_is_rejected_before_joining_a_group(monkeypatch):
    layer = get_channel_layer()
    group_add = AsyncMock()
    monkeypatch.setattr(layer, 'group_add', group_add)

    async def scenario():
        socket = communicator(None)
        connected, close_code = await socket.connect()
        assert connected is False
        assert close_code == consumers.CLOSE_UNAUTHENTICATED
        await socket.disconnect()

    run(scenario)
    group_add.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_authenticated_observer_cannot_dispatch(observer, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    socket = communicator(observer)

    async def scenario():
        connected, _ = await socket.connect()
        assert connected is True
        await socket.send_json_to({'command': 'observe-only'})
        close = await socket.receive_output()
        assert close == {
            'type': 'websocket.close',
            'code': consumers.CLOSE_FORBIDDEN,
        }
        await socket.disconnect()

    run(scenario)
    dispatch.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_authorized_principal_dispatches_a_valid_command(dispatcher, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    socket = communicator(dispatcher)

    async def scenario():
        connected, _ = await socket.connect()
        assert connected is True
        await socket.send_json_to({'command': 'launch the audit fleet'})
        response = await socket.receive_json_from()
        assert response == {'message': 'Fleet Dispatched: launch the audit fleet'}
        await socket.disconnect()

    run(scenario)
    dispatch.assert_awaited_once_with('launch the audit fleet')


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    'payload, close_code',
    [
        ('not-json', consumers.CLOSE_INVALID_COMMAND),
        ({'command': ''}, consumers.CLOSE_INVALID_COMMAND),
        ({'command': 42}, consumers.CLOSE_INVALID_COMMAND),
        ({'command': 'valid', 'extra': True}, consumers.CLOSE_INVALID_COMMAND),
        ({'command': 'x\x00y'}, consumers.CLOSE_INVALID_COMMAND),
        ({'command': 'x' * (consumers.MAX_COMMAND_BYTES + 1)}, consumers.CLOSE_MESSAGE_TOO_LARGE),
    ],
)
def test_malformed_and_oversized_messages_close_predictably(
    dispatcher, payload, close_code, monkeypatch
):
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    socket = communicator(dispatcher)

    async def scenario():
        connected, _ = await socket.connect()
        assert connected is True
        if isinstance(payload, str):
            await socket.send_to(text_data=payload)
        else:
            await socket.send_json_to(payload)
        close = await socket.receive_output()
        assert close == {'type': 'websocket.close', 'code': close_code}
        await socket.disconnect()

    run(scenario)
    dispatch.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_message_size_limit_applies_to_json_envelope(dispatcher, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    socket = communicator(dispatcher)

    async def scenario():
        connected, _ = await socket.connect()
        assert connected is True
        oversized = '{"command":"' + ('x' * consumers.MAX_MESSAGE_BYTES) + '"}'
        await socket.send_to(text_data=oversized)
        close = await socket.receive_output()
        assert close == {
            'type': 'websocket.close',
            'code': consumers.CLOSE_MESSAGE_TOO_LARGE,
        }
        await socket.disconnect()

    run(scenario)
    dispatch.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_rate_limit_closes_after_bounded_messages(dispatcher, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(consumers, '_dispatch_fleet', dispatch)
    socket = communicator(dispatcher)

    async def scenario():
        connected, _ = await socket.connect()
        assert connected is True
        for _ in range(consumers.MAX_COMMANDS_PER_WINDOW):
            await socket.send_json_to({'command': 'bounded command'})
            response = await socket.receive_json_from()
            assert response['message'].startswith('Fleet Dispatched:')
        await socket.send_json_to({'command': 'rate limited command'})
        close = await socket.receive_output()
        assert close == {
            'type': 'websocket.close',
            'code': consumers.CLOSE_RATE_LIMITED,
        }
        await socket.disconnect()

    run(scenario)
    assert dispatch.await_count == consumers.MAX_COMMANDS_PER_WINDOW


@pytest.mark.django_db(transaction=True)
def test_browser_origin_must_be_allowlisted(dispatcher):
    allowed = communicator(dispatcher, ALLOWED_ORIGIN)
    denied = communicator(dispatcher, 'https://attacker.example')

    async def scenario():
        connected, _ = await allowed.connect()
        assert connected is True
        await allowed.disconnect()

        connected, close_code = await denied.connect()
        assert connected is False
        assert close_code == 1000
        await denied.disconnect()

    run(scenario)


@pytest.mark.django_db(transaction=True)
def test_events_are_isolated_to_the_authenticated_user(observer, dispatcher):
    observer_socket = communicator(observer)
    dispatcher_socket = communicator(dispatcher)

    async def scenario():
        assert (await observer_socket.connect())[0] is True
        assert (await dispatcher_socket.connect())[0] is True

        layer = get_channel_layer()
        await layer.group_send(
            f'magistrate_user_{observer.pk}',
            {'type': 'system_event', 'message': 'observer event'},
        )
        assert await observer_socket.receive_json_from() == {
            'message': 'observer event'
        }
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(dispatcher_socket.receive_output(), timeout=0.05)

        await observer_socket.disconnect()
        await dispatcher_socket.disconnect()

    run(scenario)
