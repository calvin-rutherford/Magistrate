import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any

from app.auth import _principal_from_token
from fastapi import HTTPException

router = APIRouter()

class ARGlassesConnectionManager:
    def __init__(self): self.active_connections: list[WebSocket] = []
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections: self.active_connections.remove(websocket)
    async def register(self, websocket: WebSocket):
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            message = json.loads(raw)
            token = message.get('token') if isinstance(message, dict) else None
            principal = _principal_from_token(token) if isinstance(token, str) else None
            if principal is None or not principal.has('read'):
                await websocket.close(code=1008)
                return None
            self.active_connections.append(websocket)
            await websocket.send_json({'status': 'connected', 'message': 'Magistrate AR Interface Ready'})
            return principal
        except (asyncio.TimeoutError, json.JSONDecodeError, HTTPException):
            await websocket.close(code=1008)
            return None

    async def process_payload(self, data: Dict[str, Any], websocket: WebSocket, can_command: bool):
        if data.get('type') == 'input' and can_command:
            modality, payload = data.get('modality'), data.get('payload')
            if not isinstance(modality, str) or not isinstance(payload, str) or len(payload) > 4000:
                await websocket.send_json({'error': 'Invalid input payload'})
                return
            await websocket.send_json({'status': 'ack', 'received_modality': modality, 'action': 'dispatched_to_firstmate', 'summary': f'Processed {modality} input'})
        else:
            await websocket.send_json({'error': 'Command scope required'})

manager = ARGlassesConnectionManager()

@router.websocket('/ws/ar-interface')
async def ar_websocket_endpoint(websocket: WebSocket):
    principal = await manager.register(websocket)
    if principal is None: return
    try:
        while True:
            try: data = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                await websocket.send_json({'error': 'Invalid JSON format'}); continue
            await manager.process_payload(data, websocket, principal.has('command'))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
