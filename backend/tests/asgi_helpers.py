"""Small test-only ASGI client, without Channels' eager Daphne live-server import.

The production ASGI application and its auth/origin middleware are unchanged.
Real Uvicorn HTTP/WebSocket compatibility is tested separately on loopback.
"""
import json

from asgiref.testing import ApplicationCommunicator


class WebsocketCommunicator(ApplicationCommunicator):
    def __init__(self, application, path, *, headers):
        super().__init__(application, {
            'type': 'websocket', 'asgi': {'version': '3.0', 'spec_version': '2.5'},
            'path': path, 'raw_path': path.encode(), 'query_string': b'',
            'headers': headers, 'subprotocols': [],
        })

    async def connect(self):
        await self.send_input({'type': 'websocket.connect'})
        event = await self.receive_output()
        assert event['type'] in {'websocket.accept', 'websocket.close'}
        if event['type'] == 'websocket.close':
            return False, event.get('code', 1000)
        return True, event.get('subprotocol')

    async def send_to(self, *, text_data):
        await self.send_input({'type': 'websocket.receive', 'text': text_data})

    async def send_json_to(self, data):
        await self.send_to(text_data=json.dumps(data))

    async def receive_json_from(self):
        event = await self.receive_output()
        assert event['type'] == 'websocket.send' and isinstance(event['text'], str)
        return json.loads(event['text'])

    async def disconnect(self):
        await self.send_input({'type': 'websocket.disconnect', 'code': 1000})
        await self.wait()
