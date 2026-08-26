import json
import time
from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import CivilServantAgent
from .services.executive import ExecutiveService


# Command-socket close-code contract:
# 4400 invalid payload, 4401 missing authentication, 4403 missing dispatch
# permission, 1009 oversized payload, and 4429 rate limit exceeded.
CLOSE_INVALID_COMMAND = 4400
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_MESSAGE_TOO_LARGE = 1009
CLOSE_RATE_LIMITED = 4429

MAX_MESSAGE_BYTES = 4096
MAX_COMMAND_BYTES = 2048
RATE_LIMIT_WINDOW_SECONDS = 10.0
MAX_COMMANDS_PER_WINDOW = 5


@database_sync_to_async
def _dispatch_fleet(command):
    """Keep the existing fleet launch service behind the socket boundary."""
    captain, _ = CivilServantAgent.objects.get_or_create(
        name="AutoCaptain",
        defaults={'clearance_level': 5, 'rank': 'Senior'},
    )
    return ExecutiveService.launch_fleet(
        fleet_name=f"Fleet-{command[:10].strip()}",
        objective=command,
        captain=captain,
    )


@database_sync_to_async
def _user_can_dispatch(user):
    return user.has_perm('agents.add_fleet')


def _validate_command(text_data):
    if not isinstance(text_data, str):
        raise ValueError("command must be a text message")
    if len(text_data.encode('utf-8')) > MAX_MESSAGE_BYTES:
        raise OverflowError("message exceeds the maximum size")

    try:
        payload = json.loads(text_data)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("message must be valid JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {'command'}:
        raise ValueError("message must contain only a command field")

    command = payload['command']
    if not isinstance(command, str):
        raise ValueError("command must be a string")
    command = command.strip()
    if not command:
        raise ValueError("command must not be empty")
    if len(command.encode('utf-8')) > MAX_COMMAND_BYTES:
        raise OverflowError("command exceeds the maximum size")
    if any(ord(character) < 32 for character in command):
        raise ValueError("command contains a control character")
    return command

class MagistrateConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        # User-derived groups prevent one authenticated principal from
        # receiving another principal's command events.  Do not use a client
        # supplied group name here.
        self.group_name = f'magistrate_user_{user.pk}'
        self._command_timestamps = deque()

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            await self.close(code=CLOSE_INVALID_COMMAND)
            return

        now = time.monotonic()
        while (
            self._command_timestamps
            and now - self._command_timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS
        ):
            self._command_timestamps.popleft()
        if len(self._command_timestamps) >= MAX_COMMANDS_PER_WINDOW:
            await self.close(code=CLOSE_RATE_LIMITED)
            return
        self._command_timestamps.append(now)

        try:
            command = _validate_command(text_data)
        except OverflowError:
            await self.close(code=CLOSE_MESSAGE_TOO_LARGE)
            return
        except ValueError:
            await self.close(code=CLOSE_INVALID_COMMAND)
            return

        user = self.scope['user']
        if not await _user_can_dispatch(user):
            await self.close(code=CLOSE_FORBIDDEN)
            return

        try:
            await _dispatch_fleet(command)
            await self.send(text_data=json.dumps({
                'message': f"Fleet Dispatched: {command}"
            }))
        except Exception:
            await self.send(text_data=json.dumps({
                'message': "Error Dispatching Fleet"
            }))

    # Receive message from room group (from Firstmate/Judicial)
    async def system_event(self, event):
        message = event['message']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'message': message
        }))
