import json
from channels.generic.websocket import AsyncWebsocketConsumer

class MagistrateConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = 'magistrate_events'

        # Join room group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from WebSocket (from the President/Terminal)
    async def receive(self, text_data):
        data = json.loads(text_data)
        command = data.get('command')
        
        if not command:
            return

        from channels.db import database_sync_to_async
        from .services.executive import ExecutiveService
        from .models import CivilServantAgent

        @database_sync_to_async
        def dispatch_fleet():
            # Get or create a default Captain for MVP
            captain, _ = CivilServantAgent.objects.get_or_create(
                name="AutoCaptain",
                defaults={'clearance_level': 5, 'rank': 'Senior'}
            )
            ExecutiveService.launch_fleet(
                fleet_name=f"Fleet-{command[:10].strip()}",
                objective=command,
                captain=captain
            )

        try:
            await dispatch_fleet()
            await self.send(text_data=json.dumps({
                'message': f"Fleet Dispatched: {command}"
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'message': f"Error Dispatching Fleet: {str(e)}"
            }))

    # Receive message from room group (from Firstmate/Judicial)
    async def system_event(self, event):
        message = event['message']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'message': message
        }))
