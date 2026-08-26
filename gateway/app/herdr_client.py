import asyncio
import json
import os
import subprocess
from typing import Dict, Any, List, Optional

HERDR_SOCKET_PATH = os.getenv('HERDR_SOCKET_PATH', os.path.expanduser('~/.config/herdr/herdr.sock'))
HERDR_MAX_READ_LINES = 2**32 - 1

class HerdrClient:
    def __init__(self, socket_path: str = HERDR_SOCKET_PATH):
        self.socket_path = socket_path

    async def send_rpc_request(self, method: str, params: Optional[Dict[str, Any]] = None, req_id: str = 'magistrate:rpc') -> Dict[str, Any]:
        payload = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or {}}
        if not os.path.exists(self.socket_path):
            return await self._cli_rpc_fallback(method, params)

        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            writer.write(json.dumps(payload).encode('utf-8') + bytes([10]))
            await writer.drain()

            line = await reader.readline()
            writer.close()
            await writer.wait_closed()

            if not line:
                return {}
            return json.loads(line.decode('utf-8'))
        except Exception:
            return await self._cli_rpc_fallback(method, params)

    async def _cli_rpc_fallback(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            'herdr', 'api', 'snapshot',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 and stdout:
            try:
                return json.loads(stdout.decode('utf-8'))
            except json.JSONDecodeError:
                pass
        return {'error': {'message': stderr.decode('utf-8') if stderr else 'CLI fallback failed'}}

    async def get_snapshot(self) -> Dict[str, Any]:
        res = await self.send_rpc_request('session.snapshot')
        if 'result' in res and 'snapshot' in res['result']:
            return res['result']['snapshot']
        
        proc = await asyncio.create_subprocess_exec(
            'herdr', 'api', 'snapshot',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if stdout:
            try:
                data = json.loads(stdout.decode('utf-8'))
                if 'result' in data and 'snapshot' in data['result']:
                    return data['result']['snapshot']
            except json.JSONDecodeError:
                pass
        return {'agents': [], 'workspaces': [], 'tabs': [], 'panes': [], 'version': '0.8.2'}

    async def list_agents(self) -> List[Dict[str, Any]]:
        snapshot = await self.get_snapshot()
        agents = snapshot.get('agents', [])
        formatted_agents = []
        for ag in agents:
            raw_status = ag.get('agent_status')
            status = raw_status.get('state') if isinstance(raw_status, dict) else raw_status
            status = status or 'unknown'
            agent_id = ag.get('pane_id') or ag.get('id') or ag.get('name')
            if not agent_id:
                continue
            formatted_agents.append({
                'id': agent_id,
                'name': ag.get('name') or ag.get('label') or ag.get('terminal_title_stripped') or agent_id,
                'harness': ag.get('agent') or ag.get('harness'),
                'status': status,
                'pane_id': ag.get('pane_id'),
                'tab_id': ag.get('tab_id'),
                'workspace_id': ag.get('workspace_id')
            })
        return formatted_agents

    async def resolve_target(self, target: str) -> str:
        if target in ('captain', 'codex', 'firstmate'):
            agents = await self.list_agents()
            if agents:
                for ag in agents:
                    if ag.get('harness') == 'codex' or ag.get('name') in ('captain', 'codex'):
                        return ag.get('pane_id') or ag.get('id')
                return agents[0].get('pane_id') or agents[0].get('id')
        return target

    async def prompt_agent(self, target: str, text: str, harness: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        submitted_text = text
        if harness and model:
            # Herdr's prompt command has no launch-time selection flags. Carry
            # the validated selection to Firstmate as structured prompt context
            # while leaving its full-permissions launch behavior unchanged.
            submitted_text = f'[Magistrate execution: harness={harness}; model={model}]\n{text}'
        cmd = ['herdr', 'agent', 'prompt', resolved_target, submitted_text]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        output_str = stdout.decode('utf-8') if stdout else ''
        err_str = stderr.decode('utf-8') if stderr else ''

        if proc.returncode == 0:
            return {'status': 'submitted', 'target': resolved_target, 'response': output_str.strip(), 'harness': harness, 'model': model}
        else:
            return {'status': 'error', 'target': resolved_target, 'error': err_str.strip() or output_str.strip(), 'harness': harness, 'model': model}

    async def read_agent_output(self, target: str, lines: int = HERDR_MAX_READ_LINES, source: str = 'recent-unwrapped') -> str:
        resolved_target = await self.resolve_target(target)
        # Herdr's API models lines as an unsigned 32-bit value. Asking for its
        # maximum returns every row still retained by the configured byte-sized
        # scrollback buffer without imposing a second, arbitrary gateway cap.
        lines = min(max(lines, 0), HERDR_MAX_READ_LINES)
        cmd = [
            'herdr', 'agent', 'read', resolved_target,
            '--source', source, '--lines', str(lines), '--format', 'text'
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode('utf-8', errors='replace') if stdout else ''

    async def interrupt_agent(self, target: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'C-c']
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return {'status': 'interrupted', 'target': resolved_target}
        else:
            return {'status': 'error', 'target': resolved_target, 'error': stderr.decode('utf-8').strip()}

    async def send_agent_key(self, target: str, key: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        if key in ('Enter', 'Return', 'ENTER'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Enter']
        elif key in ('Escape', 'Esc', 'ESC'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Escape']
        elif key in ('C-c', 'Ctrl+C', 'CTRL+C'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'C-c']
        elif key in ('Up', 'UP', '↑'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Up']
        elif key in ('Down', 'DOWN', '↓'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Down']
        elif key in ('Tab', 'TAB'):
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'Tab']
        elif key in ('y', 'Y', 'YES'):
            cmd = ['herdr', 'agent', 'prompt', resolved_target, 'y']
        elif key in ('n', 'N', 'NO'):
            cmd = ['herdr', 'agent', 'prompt', resolved_target, 'n']
        else:
            cmd = ['herdr', 'agent', 'send-keys', resolved_target, key]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return {
            'status': 'submitted',
            'target': resolved_target,
            'key': key,
            'response': stdout.decode('utf-8').strip(),
            'error': stderr.decode('utf-8').strip() if proc.returncode != 0 else None
        }
