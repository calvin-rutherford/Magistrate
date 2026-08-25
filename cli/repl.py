import asyncio
import json
import websockets
import subprocess
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

console = Console()

IS_STATEFUL = True

async def listen_to_broker(websocket):
    try:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")
            text = data.get("message", "")
            
            if msg_type in ["system", "event"]:
                # Print using rich. It will safely patch above the prompt bar.
                if text.startswith("Broker >"):
                    console.print(f"[bold cyan]{text}[/bold cyan]")
                elif text.startswith("You >"):
                    console.print(f"[dim]{text}[/dim]")
                else:
                    console.print(text)
            elif msg_type == "approval_request":
                approval_id = data.get("approval_id")
                command = data.get("command")
                console.print(f"\n[bold red blink]⚠️ APPROVAL REQUIRED ⚠️[/bold red blink]")
                console.print(f"[bold yellow]Agent requests to run bash command in sandbox:[/bold yellow] {command}")
                console.print(f"[dim]Type '!approve {approval_id}' or '!deny {approval_id}'[/dim]\n")
                
    except websockets.exceptions.ConnectionClosed:
        console.print("[bold red]Connection to broker closed.[/bold red]")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        console.print(f"[bold red]Listener Error:[/] {e}")

async def run_repl():
    global IS_STATEFUL
    uri = "ws://localhost:8000/ws/broker/"
    session = PromptSession()
    
    console.print("[bold green]Starting Magistrate CLI...[/bold green]")
    
    try:
        async with websockets.connect(uri, ping_interval=None) as websocket:
            console.print("[bold green]Connected to Django WebSocket API![/bold green]")
            
            # Start the background listener task
            listener_task = asyncio.create_task(listen_to_broker(websocket))
            
            # Run the prompt loop
            with patch_stdout():
                while True:
                    try:
                        # Wait for user input asynchronously
                        user_input = await session.prompt_async("Omni > ")
                        user_input = user_input.strip()
                        if not user_input:
                            continue
                            
                        if user_input.lower() in ["exit", "quit"]:
                            break
                            
                        if user_input.startswith("!stateless"):
                            IS_STATEFUL = False
                            console.print("[bold yellow]Switched to STATELESS mode. Broker will not retrieve past messages.[/bold yellow]")
                            continue
                            
                        if user_input.startswith("!stateful"):
                            IS_STATEFUL = True
                            console.print("[bold green]Switched to STATEFUL mode. Broker will retrieve past messages.[/bold green]")
                            continue
                            
                        if user_input.startswith("!approve "):
                            approval_id = user_input.split(" ")[1].strip()
                            await websocket.send(json.dumps({
                                "type": "approval_response",
                                "id": approval_id,
                                "approved": True
                            }))
                            console.print(f"[bold green]Approved request {approval_id}[/bold green]")
                            continue
                            
                        if user_input.startswith("!deny "):
                            approval_id = user_input.split(" ")[1].strip()
                            await websocket.send(json.dumps({
                                "type": "approval_response",
                                "id": approval_id,
                                "approved": False
                            }))
                            console.print(f"[bold red]Denied request {approval_id}[/bold red]")
                            continue
                            
                        # Handle local shell commands
                        if user_input.startswith("!"):
                            command = user_input[1:].strip()
                            console.print(f"[bold blue]Local Shell >[/bold blue] {command}")
                            try:
                                result = subprocess.run(
                                    command, shell=True, text=True, 
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                                )
                                output = result.stdout.strip()
                                if output:
                                    console.print(f"[dim]{output}[/dim]")
                                else:
                                    console.print("[dim](No output)[/dim]")
                            except Exception as e:
                                console.print(f"[bold red]Shell Error:[/] {e}")
                            continue

                        # Send normal message to broker
                        await websocket.send(json.dumps({
                            "message": user_input,
                            "stateful": IS_STATEFUL
                        }))
                        
                    except (EOFError, KeyboardInterrupt):
                        break
            
            listener_task.cancel()
            
    except ConnectionRefusedError:
        console.print("[bold red]Failed to connect to backend. Is Docker running?[/bold red]")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")

def main():
    try:
        asyncio.run(run_repl())
    except KeyboardInterrupt:
        pass
