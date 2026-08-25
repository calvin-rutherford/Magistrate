from django.core.management.base import BaseCommand
import time
import json
from agents.services.arbiter import ArbiterService

class Command(BaseCommand):
    help = 'Boots the October Cognitive Engine (Hermes/Arbiter) to execute a specific objective.'

    def add_arguments(self, parser):
        parser.add_argument('--objective', type=str, help='The objective directive for the agent to execute.')

    def handle(self, *args, **options):
        objective = options.get('objective', 'No objective provided.')
        
        self.stdout.write(self.style.SUCCESS("========================================"))
        self.stdout.write(self.style.SUCCESS(" OCTOBER COGNITIVE ENGINE INITIALIZING "))
        self.stdout.write(self.style.SUCCESS("========================================"))
        
        self.stdout.write(f"[*] Parsing Objective: {objective}")
        self.stdout.write("[*] Loading Context...")
        time.sleep(1)
        
        self.stdout.write("[*] Invoking Hermes Model (ArbiterService)...")
        
        try:
            # We call the existing Arbiter logic
            decision = ArbiterService.process_presidential_directive(objective, current_state={})
            self.stdout.write("\n[+] DECISION PACKET RECEIVED:")
            self.stdout.write(json.dumps(decision, indent=2))
            
            # Live Autonomous Execution Engine
            self.stdout.write("\n[*] Engaging Autonomous OpenCode Subprocess...")
            
            import subprocess
            import sys
            
            # Use subprocess to launch OpenCode interactively and pipe output directly to stdout
            # The --auto flag gives the agent permission to autonomously execute shell commands
            opencode_cmd = [
                "/home/spectre/.opencode/bin/opencode",
                "run",
                "--auto",
                objective
            ]
            
            process = subprocess.Popen(
                opencode_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Stream the output line by line so tmux_streamer picks it up
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                
            process.wait()
            
            if process.returncode == 0:
                self.stdout.write(self.style.SUCCESS("[✓] Autonomous Execution Complete."))
            else:
                self.stdout.write(self.style.ERROR(f"[X] Execution failed with return code {process.returncode}"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[X] FATAL ERROR during cognitive processing: {str(e)}"))
