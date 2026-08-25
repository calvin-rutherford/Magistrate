import asyncio
import websockets
import json
import libtmux
import sys

async def run_e2e_test():
    uri = "ws://localhost:8001/ws/magistrate/"
    test_command = "E2E_LIVE_TEST_RUN"
    
    print("========================================")
    print("MAGISTRATE END-TO-END LIVE TEST PROTOCOL")
    print("========================================")
    
    try:
        async with websockets.connect(uri) as ws:
            print("[✓] Connected to Magistrate WebSocket Broker")
            
            # Step 1: Send the command
            print(f"[*] Dispatching Presidential Directive: '{test_command}'")
            await ws.send(json.dumps({'command': test_command}))
            
            # Step 2: Listen for the expected broadcasts
            received_dispatch_ack = False
            received_execution_event = False
            received_stream_event = False
            run_id = None
            
            for _ in range(15):  # Wait for up to 15 events before timing out
                message = await asyncio.wait_for(ws.recv(), timeout=60.0)
                data = json.loads(message)
                
                if 'message' in data:
                    msg = data['message']
                    print(f"    <- Received Event: {msg}")
                    
                    if "Fleet Dispatched" in msg:
                        received_dispatch_ack = True
                    
                    if "ExecutionRun [" in msg and "Started" in msg:
                        received_execution_event = True
                        # Extract the run ID snippet
                        parts = msg.split("[")
                        if len(parts) > 1:
                            run_id = parts[1].split("]")[0]
                    
                    if run_id and f"[Stream {run_id}]" in msg:
                        received_stream_event = True
                        print(f"    [!] Confirmed Live OpenCode Output: {msg}")
                        break
                        
            if not received_dispatch_ack:
                print("[X] FAILED: Did not receive dispatch acknowledgement.")
                sys.exit(1)
            
            if not received_execution_event or not run_id:
                print("[X] FAILED: Did not receive ExecutionRun generation event.")
                sys.exit(1)
                
            if not received_stream_event:
                print("[X] FAILED: Did not receive live OpenCode stream logs.")
                sys.exit(1)
                
            print(f"[✓] ExecutionRun [{run_id}] successfully broadcasted by Broker")
            print(f"[✓] Live OpenCode streaming functional!")
            
            # Step 3: Verify the Tmux Window exists in Treehouse
            print("[*] Verifying Treehouse Tmux orchestration...")
            server = libtmux.Server()
            
            # The test must find the window inside magistrate-core OR a fallback session
            window_found = False
            
            # Check magistrate-core first
            try:
                core_session = server.sessions.get(session_name="magistrate-core")
                for w in core_session.windows:
                    if w.name == f"run-{run_id}":
                        window_found = True
                        print(f"[✓] SUCCESS: Agent window 'run-{run_id}' found inside 'magistrate-core' session!")
                        break
            except Exception:
                pass
                
            # Check fallback isolated session
            if not window_found:
                try:
                    mag_session = server.sessions.get(session_name=f"mag-run-{run_id}")
                    window_found = True
                    print(f"[✓] SUCCESS: Fallback isolated session 'mag-run-{run_id}' found!")
                except Exception:
                    pass
            
            if window_found:
                print("\n========================================")
                print("E2E LIVE TEST: PASSED")
                print("========================================")
                sys.exit(0)
            else:
                print(f"[X] FAILED: Could not find tmux window for run_id {run_id}")
                sys.exit(1)

    except ConnectionRefusedError:
        print("[X] Connection Refused. Is the Daphne backend running?")
        sys.exit(1)
    except asyncio.TimeoutError:
        print("[X] Timed out waiting for Broker response.")
        sys.exit(1)
    except Exception as e:
        print(f"[X] Unexpected Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_e2e_test())
