import os
import uuid
import libtmux
from ..models import EventLog, Fleet, ExecutionRun, Task, AuditEvent, CivilServantAgent

class FirstmateRuntimeAdapter:
    """
    The deterministic Broker for Firstmate execution.
    Responsible for creating isolated Git Worktrees and launching OpenCode.
    """

    @classmethod
    def get_or_create_session(cls, session_name: str):
        server = libtmux.Server()
        try:
            return server.sessions.get(session_name=session_name)
        except Exception:
            return server.new_session(session_name=session_name, detach=True)

    @classmethod
    def start_firstmate(cls, fleet: Fleet, task: Task, target_repo_path: str, model_provider: str = "ollama/hermes3:8b"):
        """
        Creates an isolated ExecutionRun worktree and spawns OpenCode.
        """
        run_id = str(uuid.uuid4())
        
        # 1. Create the ExecutionRun Record
        worktree_path = f"/home/spectre/worktrees/{run_id}"
        branch_name = f"magistrate-exec-{run_id[:8]}"
        
        exec_run = ExecutionRun.objects.create(
            id=run_id,
            task=task,
            worktree_path=worktree_path,
            branch_name=branch_name
        )
        
        # 2. Setup the Git Worktree
        import subprocess
        os.makedirs("/home/spectre/worktrees", exist_ok=True)
        
        # If the target repo doesn't exist, we can't create a worktree. For the MVP, we just clone Firstmate if it doesn't exist.
        if not os.path.exists(target_repo_path):
            subprocess.run(["git", "clone", "https://github.com/calvin-rutherford/Magistrate.git", target_repo_path])
        
        subprocess.run(["git", "-C", target_repo_path, "worktree", "add", "-b", branch_name, worktree_path])
        
        # 3. Dynamic Model Provisioning
        import json
        from dotenv import load_dotenv
        load_dotenv("/home/spectre/Magistrate/backend/.env")
        
        opencode_config = {
            "$schema": "https://opencode.ai/config.json",
            "model": model_provider
        }
        
        env_vars = ""
        if "gemini" in model_provider.lower():
            api_key = os.environ.get("GEMINI_API_KEY", "")
            env_vars = f"export GEMINI_API_KEY={api_key} && export GOOGLE_API_KEY={api_key} && "
        elif "claude" in model_provider.lower():
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            env_vars = f"export ANTHROPIC_API_KEY={api_key} && "
        elif "ollama" in model_provider.lower():
            opencode_config["provider"] = {
                "ollama": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Ollama Local",
                    "options": {
                        "baseURL": "http://127.0.0.1:11434/v1"
                    }
                }
            }
            
        with open(f"{worktree_path}/opencode.json", "w") as f:
            json.dump(opencode_config, f, indent=2)
            
        # 4. Create the Tmux Window for isolation in the active treehouse session
        server = libtmux.Server()
        try:
            core_session = server.sessions.get(session_name="magistrate-core")
            window_name = f"run-{run_id[:8]}"
            window = core_session.new_window(window_name=window_name, attach=False)
            pane = window.panes[0]
        except Exception:
            # Fallback if magistrate-core isn't running
            session_name = f"mag-run-{run_id[:8]}"
            session = cls.get_or_create_session(session_name)
            window = session.windows[0]
            pane = window.panes[0]
        
        # 5. Launch October Cognitive Engine inside the isolated worktree
        pane.send_keys(f"cd {worktree_path}")
        pane.send_keys(f"{env_vars}source /home/spectre/Magistrate/backend/venv/bin/activate && python /home/spectre/Magistrate/backend/manage.py run_october --objective \"{task.description}\"")
        
        AuditEvent.objects.create(
            event_type='FirstmateSessionStarted',
            payload={
                'fleet_id': str(fleet.id), 
                'run_id': run_id, 
                'worktree': worktree_path,
                'branch': branch_name
            }
        )
        
        # Broadcast to the WebSockets
        cls.broadcast_event(f"Worktree Isolated. ExecutionRun [{run_id[:8]}] Started for Fleet: {fleet.name}")
        
        # 5. Live Terminal Streaming Daemon
        import threading
        import time
        def stream_output(session_nm, r_id):
            last_line = ""
            for _ in range(30): # Stream for 30 seconds for MVP
                time.sleep(1)
                try:
                    out = cls.capture_output(session_nm)
                    lines = [l for l in out.strip().split("\n") if l.strip()]
                    if lines and lines[-1] != last_line:
                        last_line = lines[-1]
                        # Don't broadcast the prompt itself to avoid noise
                        if not last_line.endswith("$") and not last_line.endswith("#"):
                            cls.broadcast_event(f"[Stream {r_id[:8]}] {last_line}")
                            
                        # Telemetry monitor
                        if "tokens" in last_line.lower():
                            try:
                                from ..models import ModelInvocation
                                # Extract digits from the line
                                nums = [int(s) for s in last_line.split() if s.isdigit()]
                                if nums:
                                    ModelInvocation.objects.create(
                                        execution_run_id=r_id,
                                        provider="ollama",
                                        model_name="hermes3:8b",
                                        prompt_tokens=nums[0]
                                    )
                            except Exception:
                                pass
                except Exception:
                    pass
                    
        # Launch the stream daemon based on which session it landed in
        target_session = "magistrate-core" if "magistrate-core" in str(pane) else f"mag-run-{run_id[:8]}"
        threading.Thread(target=stream_output, args=(target_session, run_id), daemon=True).start()
        
        return exec_run

    @classmethod
    def broadcast_event(cls, message: str):
        """Sends a real-time message to all connected Presidents."""
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "magistrate_events",
                {
                    "type": "system_event",
                    "message": message
                }
            )
        except Exception as e:
            print(f"Failed to broadcast event to WebSockets (is Redis running?): {e}")

    @classmethod
    def send_directive(cls, session_name: str, directive: str):
        """Injects a Presidential directive into the Firstmate harness."""
        session = cls.get_or_create_session(session_name)
        pane = session.windows[0].panes[0]
        pane.send_keys(directive)
        
        AuditEvent.objects.create(
            event_type='FirstmateDirectiveSent',
            payload={'directive': directive, 'session': session_name}
        )
        cls.broadcast_event(f"Presidential Directive Sent: {directive}")

    @classmethod
    def capture_output(cls, session_name: str) -> str:
        """Reads the tmux pane output for parsing."""
        session = cls.get_or_create_session(session_name)
        pane = session.windows[0].panes[0]
        # Capture last 200 lines
        output = pane.cmd('capture-pane', '-p', '-S', '-200').stdout
        return "\n".join(output)

    @classmethod
    def enforce_judicial_intercept(cls, agent: CivilServantAgent, command: str) -> bool:
        """
        Intercepts dangerous bash commands. If a command is destructive, 
        it suspends execution and files a CourtCase.
        Returns True if allowed, False if blocked.
        """
        dangerous_keywords = ['rm -rf', 'drop table', 'secret', 'chmod 777']
        is_dangerous = any(keyword in command.lower() for keyword in dangerous_keywords)
        
        if is_dangerous:
            from .judicial import JudicialService
            JudicialService.file_case(
                court_name="Security Court",
                defendant=agent,
                charge=f"Attempted dangerous command execution: {command}"
            )
            # The session is effectively paused waiting for a ruling
            return False
            
        return True
