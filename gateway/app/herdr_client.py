import asyncio
import json
import os
import re
from typing import Dict, Any, List, Optional

HERDR_SOCKET_PATH = os.getenv('HERDR_SOCKET_PATH', os.path.expanduser('~/.config/herdr/herdr.sock'))
_GENERIC_AGENT_NAMES = {'magistrate', 'firstmate', 'π - magistrate', 'π - firstmate'}


def _human_agent_name(value: Any, identifiers: set[str]) -> Optional[str]:
    """Keep only a real Herdr pane name; IDs and harness titles are not names."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    normalized = candidate.lower()
    if not candidate or normalized in _GENERIC_AGENT_NAMES or normalized in identifiers:
        return None
    if re.match(r'^(?:pane|tab|workspace)(?:[_ -]?id)?\s*[:=]', candidate, re.IGNORECASE):
        return None
    return candidate


async def _run_cli(*args: str) -> tuple[bytes, bytes, int]:
    """Run an explicit Herdr agent-control command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return b'', b'Herdr is unavailable.', 127
    stdout, stderr = await proc.communicate()
    return stdout or b'', stderr or b'', proc.returncode if proc.returncode is not None else 1


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
        stdout, stderr, returncode = await _run_cli('herdr', 'api', 'snapshot')
        if returncode == 0 and stdout:
            try:
                return json.loads(stdout.decode('utf-8'))
            except json.JSONDecodeError:
                pass
        return {'error': {'message': stderr.decode('utf-8') if stderr else 'CLI fallback failed'}}

    async def get_snapshot(self) -> Dict[str, Any]:
        res = await self.send_rpc_request('session.snapshot')
        if 'result' in res and 'snapshot' in res['result']:
            return res['result']['snapshot']
        
        stdout, _, _ = await _run_cli('herdr', 'api', 'snapshot')
        if stdout:
            try:
                data = json.loads(stdout.decode('utf-8'))
                if 'result' in data and 'snapshot' in data['result']:
                    return data['result']['snapshot']
            except json.JSONDecodeError:
                pass
        # Neither the socket nor the CLI answered. Returning a placeholder version
        # here made every downstream consumer read the socket as connected and
        # report a Herdr build nobody observed, so the empty snapshot carries no
        # version at all and callers see the real disconnected state.
        return {'agents': [], 'workspaces': [], 'tabs': [], 'panes': []}

    @staticmethod
    def _primary_workspace_ids(snapshot: Dict[str, Any]) -> set[str]:
        """Workspace identities carrying the primary conversation role."""
        return {
            workspace_id
            for workspace in snapshot.get('workspaces', [])
            for workspace_id in [str(workspace.get('workspace_id') or workspace.get('id') or '').strip()]
            if workspace_id and str(workspace.get('label') or workspace.get('name') or '').strip().lower()
            in ('captain', 'firstmate')
        }

    @classmethod
    def _workspace_role(cls, agent: Dict[str, Any], primary_workspaces: set[str]) -> str:
        if str(agent.get('workspace_id') or '') in primary_workspaces:
            return 'primary'
        # Older Herdr snapshots may omit workspace objects. Preserve the same
        # fail-closed legacy identity used by resolve_target rather than
        # exposing the explicitly named firstmate pane as Fleet work.
        name = str(agent.get('name') or agent.get('label') or '').strip().lower()
        return 'primary' if name in ('captain', 'firstmate') or name.endswith(' - firstmate') else 'worker'

    @classmethod
    def _format_agents(cls, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        formatted_agents = []
        primary_workspaces = cls._primary_workspace_ids(snapshot)
        for ag in snapshot.get('agents', []):
            raw_status = ag.get('agent_status')
            status = raw_status.get('state') if isinstance(raw_status, dict) else raw_status
            status = status or 'unknown'
            agent_id = ag.get('pane_id') or ag.get('id') or ag.get('name')
            if not agent_id:
                continue
            identifiers = {str(value).strip().lower() for value in (agent_id, ag.get('pane_id'), ag.get('tab_id'), ag.get('workspace_id')) if value}
            # Herdr can expose both the configured pane name and a terminal
            # title. Prefer the configured name, then other human-readable
            # labels. Never turn an ID or generic harness title into identity.
            display_name = None
            for candidate in (ag.get('name'), ag.get('label'), ag.get('terminal_title_stripped'), ag.get('terminal_title')):
                display_name = _human_agent_name(candidate, identifiers)
                if display_name:
                    break
            formatted_agents.append({
                'id': agent_id,
                'name': display_name,
                'harness': next((value.strip() for value in (ag.get('agent'), ag.get('harness')) if isinstance(value, str) and value.strip()), None),
                # Herdr may report the concrete model on the live agent row;
                # absence is meaningful and must remain unknown to clients.
                'model': next((value.strip() for value in (ag.get('model'),) if isinstance(value, str) and value.strip()), None),
                'status': status,
                'pane_id': ag.get('pane_id'),
                'tab_id': ag.get('tab_id'),
                'workspace_id': ag.get('workspace_id'),
                'workspace_role': cls._workspace_role(ag, primary_workspaces),
            })
        return formatted_agents

    async def list_agents(self) -> List[Dict[str, Any]]:
        """Return all live panes for explicit controls and operational consumers."""
        return self._format_agents(await self.get_snapshot())

    async def list_fleet_agents(self) -> List[Dict[str, Any]]:
        """Return subordinate panes only for the captain-visible Fleet UI."""
        agents = await self.list_agents()
        return [agent for agent in agents if agent.get('workspace_role') != 'primary']

    async def resolve_target(self, target: str) -> str:
        if target not in ('captain', 'codex', 'firstmate'):
            return target

        # A harness is not a routing role. The deployed host can have many Pi
        # and Codex workers, and selecting the first one could control whichever
        # task happened to be listed first. Herdr's stable role signal is the
        # parent workspace label (the primary agent lives in the "firstmate"
        # workspace), not a worker's harness or
        # terminal title (every Pi worker can render "π - Magistrate").
        snapshot = await self.get_snapshot()
        agents = self._format_agents(snapshot)
        captain_workspaces = self._primary_workspace_ids(snapshot)
        for ag in agents:
            if str(ag.get('workspace_id') or '') in captain_workspaces:
                return ag.get('pane_id') or ag.get('id')

        # Preserve an explicitly configured pane identity for older snapshots
        # that do not expose workspace labels. Use configured names/labels, not
        # terminal titles: every Pi worker can render "π - Magistrate".
        for raw in snapshot.get('agents', []):
            name = str(raw.get('name') or raw.get('label') or '').strip().lower()
            if name in ('captain', 'firstmate') or name.endswith(' - firstmate'):
                return raw.get('pane_id') or raw.get('id') or raw.get('name')
        # Never fall back from captain or firstmate to an arbitrary worker
        # merely because its harness is known.
        if target == 'codex':
            for ag in agents:
                if ag.get('harness') == 'codex':
                    return ag.get('pane_id') or ag.get('id')
        return target

    async def rename_agent(self, target: str, name: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        stdout, stderr, returncode = await _run_cli('herdr', 'agent', 'rename', resolved_target, name)
        if returncode == 0:
            return {'status': 'renamed', 'target': resolved_target, 'name': name}
        return {
            'status': 'error',
            'target': resolved_target,
            'error': (stderr or stdout).decode('utf-8', errors='replace').strip() or 'Herdr could not rename the agent.',
        }

    async def interrupt_agent(self, target: str) -> Dict[str, Any]:
        resolved_target = await self.resolve_target(target)
        cmd = ['herdr', 'agent', 'send-keys', resolved_target, 'C-c']
        stdout, stderr, returncode = await _run_cli(*cmd)
        if returncode == 0:
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

        stdout, stderr, returncode = await _run_cli(*cmd)
        return {
            'status': 'submitted',
            'target': resolved_target,
            'key': key,
            'response': stdout.decode('utf-8').strip(),
            'error': stderr.decode('utf-8').strip() if returncode != 0 else None
        }
