import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any

router = APIRouter()

class ARGlassesConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await websocket.send_json({"status": "connected", "message": "Magistrate AR Interface Ready"})

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def process_payload(self, data: Dict[str, Any], websocket: WebSocket):
        # Contract structure:
        # { "type": "input", "modality": "speech|gesture|gaze", "payload": "command string or coordinate data" }
        msg_type = data.get("type")
        modality = data.get("modality")
        payload = data.get("payload")
        
        if msg_type == "input":
            # Here we would route the command to Firstmate/Herdr
            # For MVP, we simply echo back acknowledgement
            response = {
                "status": "ack",
                "received_modality": modality,
                "action": "dispatched_to_firstmate",
                "summary": f"Processed {modality} input"
            }
            await websocket.send_json(response)
        else:
            await websocket.send_json({"error": "Unknown message type"})

manager = ARGlassesConnectionManager()

@router.websocket("/ws/ar-interface")
async def ar_websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            text_data = await websocket.receive_text()
            try:
                data = json.loads(text_data)
                await manager.process_payload(data, websocket)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
