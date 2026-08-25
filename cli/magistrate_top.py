#!/usr/bin/env python3
import asyncio
import websockets
import json
import sys
import threading

async def listen_to_server():
    uri = "ws://localhost:8000/ws/magistrate/"
    try:
        async with websockets.connect(uri) as websocket:
            print("[Connected to Magistrate Government Secure Line]")
            print("Type a directive and press Enter to dispatch a Fleet.\n")
            
            # Start a background task to read from stdin (so we don't block asyncio)
            def read_input():
                while True:
                    cmd = sys.stdin.readline().strip()
                    if cmd:
                        # We use run_coroutine_threadsafe to send from another thread
                        asyncio.run_coroutine_threadsafe(websocket.send(json.dumps({'command': cmd})), asyncio.get_event_loop())
            
            threading.Thread(target=read_input, daemon=True).start()

            # Listen for incoming events
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                if 'message' in data:
                    print(f"\n[EVENT] {data['message']}")
                    
    except ConnectionRefusedError:
        print("Error: Could not connect to Magistrate backend. Is Daphne running on port 8000?")
    except Exception as e:
        print(f"Disconnected: {e}")

if __name__ == "__main__":
    print("=================================================")
    print("           MAGISTRATE COMMAND TERMINAL           ")
    print("=================================================")
    asyncio.run(listen_to_server())
