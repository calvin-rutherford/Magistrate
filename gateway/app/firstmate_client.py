import asyncio
import json
import os
from typing import Dict, Any, List, Optional

FIRSTMATE_HOME = os.getenv('FM_HOME', '/home/spectre/firstmate')

class FirstmateClient:
    def __init__(self, fm_home: str = FIRSTMATE_HOME):
        self.fm_home = fm_home
        self.snapshot_script = os.path.join(fm_home, 'bin', 'fm-fleet-snapshot.sh')

    async def get_snapshot(self) -> Dict[str, Any]:
        if not os.path.exists(self.snapshot_script):
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'fm_home': self.fm_home,
                'tasks': [],
                'scout_reports': [],
                'secondmate_current': {'records': []},
                'error': 'Snapshot script not found'
            }

        try:
            proc = await asyncio.create_subprocess_exec(
                self.snapshot_script, '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.fm_home
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0 and stdout:
                return json.loads(stdout.decode('utf-8'))
            else:
                return {
                    'schema': 'fm-fleet-snapshot.v1',
                    'fm_home': self.fm_home,
                    'tasks': [],
                    'error': stderr.decode('utf-8') if stderr else 'Failed to run fleet snapshot'
                }
        except Exception as e:
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'fm_home': self.fm_home,
                'tasks': [],
                'error': str(e)
            }

    async def get_attention_items(self, herdr_agents: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        snapshot = await self.get_snapshot()
        attention_items = []

        if herdr_agents:
            for ag in herdr_agents:
                if ag.get('status') == 'blocked':
                    attention_items.append({
                        'id': 'herdr-blocked-' + str(ag.get('id')),
                        'title': 'Agent ' + str(ag.get('name', ag.get('id'))) + ' is Blocked',
                        'subtitle': 'Herdr agent requires captain decision or input',
                        'type': 'agent_blocked',
                        'status': 'blocked',
                        'target_id': ag.get('id'),
                        'project': ag.get('name', 'Firstmate')
                    })

        tasks = snapshot.get('tasks', [])
        for task in tasks:
            t_status = str(task.get('status', '')).lower()
            if 'blocked' in t_status or 'decision' in t_status or 'attention' in t_status:
                attention_items.append({
                    'id': 'task-blocked-' + str(task.get('id', task.get('name'))),
                    'title': task.get('title') or task.get('name') or 'Blocked Task',
                    'subtitle': task.get('summary') or 'Task requires captain decision',
                    'type': 'task_blocked',
                    'status': 'blocked',
                    'target_id': task.get('id'),
                    'project': task.get('project', 'Firstmate')
                })

            pr = task.get('pr') or task.get('pr_url')
            if pr and ('ready' in t_status or 'review' in t_status):
                attention_items.append({
                    'id': 'pr-ready-' + str(task.get('id')),
                    'title': 'PR Ready: ' + str(task.get('title', 'Pull Request')),
                    'subtitle': 'PR ' + str(pr) + ' ready for captain review',
                    'type': 'pr_ready',
                    'status': 'ready',
                    'target_id': pr,
                    'project': task.get('project', 'Firstmate')
                })

        return attention_items
