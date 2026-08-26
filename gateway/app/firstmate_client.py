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

        tasks = snapshot.get('tasks', [])
        for task in tasks:
            t_status = str(task.get('status', '')).lower()
            attention = task.get('attention') if isinstance(task.get('attention'), dict) else {}
            attention_kind = attention.get('kind') or task.get('attention_kind')
            needs_captain = task.get('requires_captain') is True or attention.get('requires_captain') is True
            # A generic "blocked" state often means infrastructure or an external wait. Only
            # Firstmate's explicit captain-attention contract is actionable enough to notify.
            if needs_captain and attention_kind in ('question', 'decision', 'awaiting_answer'):
                target_id = task.get('id') or task.get('name')
                attention_items.append({
                    'id': 'captain-question-' + str(target_id),
                    'title': task.get('title') or task.get('name') or 'Blocked Task',
                    'subtitle': attention.get('summary') or task.get('summary') or 'Your answer is needed to continue.',
                    'type': 'captain_question',
                    'status': 'needs-decision' if attention_kind == 'decision' else 'awaiting_answer',
                    'target_id': target_id,
                    'project': task.get('project', 'Firstmate'),
                    'revision': attention.get('revision') or task.get('updated_at') or t_status,
                    'url': f'/attention?item=captain-question-{target_id}'
                })

            pr = task.get('pr') or task.get('pr_url')
            # Firstmate owns this readiness signal. Do not infer it from task status text.
            merge_ready = task.get('pr_merge_ready') is True or task.get('merge_decision_required') is True
            if pr and merge_ready:
                target_id = task.get('id') or pr
                attention_items.append({
                    'id': 'pr-ready-' + str(target_id),
                    'title': 'PR Ready: ' + str(task.get('title', 'Pull Request')),
                    'subtitle': 'Checks and review are complete. Your merge decision is needed.',
                    'type': 'pr_ready',
                    'status': 'ready',
                    'target_id': pr,
                    'project': task.get('project', 'Firstmate'),
                    'revision': task.get('pr_head_sha') or task.get('updated_at') or pr,
                    'url': f'/attention?item=pr-ready-{target_id}'
                })

        return attention_items
