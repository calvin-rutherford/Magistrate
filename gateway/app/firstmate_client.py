import asyncio
import json
import os
from datetime import datetime, timezone
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

        # fm-fleet-snapshot.sh's task contract carries captain-attention signal under
        # hints.pending_decision / hints.blocked_event, with the underlying keyed
        # decisions in hints.open_decisions (each {key, verb, summary}, verb one of
        # 'needs-decision' or 'blocked'). Older 'attention'/'requires_captain' fields
        # never existed in this schema, so attention items never surfaced. Readable
        # titles come from the matching backlog record (tasks carry only ids).
        records_by_id = {record.get('id'): record for record in snapshot.get('backlog', {}).get('records', []) if record.get('id')}

        for task in snapshot.get('tasks', []):
            task_id = task.get('id')
            record = records_by_id.get(task_id, {})
            title = record.get('title') or task.get('project') or task_id or 'Firstmate Task'
            project = record.get('repo') or task.get('project') or 'Firstmate'
            hints = task.get('hints') if isinstance(task.get('hints'), dict) else {}
            open_decisions = hints.get('open_decisions') or []

            if hints.get('pending_decision'):
                for decision in (d for d in open_decisions if d.get('verb') == 'needs-decision'):
                    target_id = decision.get('key') or task_id
                    attention_items.append({
                        'id': 'captain-question-' + str(target_id),
                        'title': title,
                        'subtitle': decision.get('summary') or hints.get('last_event_text') or 'Your decision is needed to continue.',
                        'type': 'captain_question',
                        'status': 'needs-decision',
                        'target_id': target_id,
                        'project': project,
                        'revision': hints.get('last_event_text') or target_id,
                        'url': f'/attention?item=captain-question-{target_id}'
                    })

            if hints.get('blocked_event'):
                for decision in (d for d in open_decisions if d.get('verb') == 'blocked'):
                    target_id = decision.get('key') or task_id
                    attention_items.append({
                        'id': 'captain-question-' + str(target_id),
                        'title': title,
                        'subtitle': decision.get('summary') or hints.get('last_event_text') or 'Agent is blocked and needs your input.',
                        'type': 'captain_question',
                        'status': 'awaiting_answer',
                        'target_id': target_id,
                        'project': project,
                        'revision': hints.get('last_event_text') or target_id,
                        'url': f'/attention?item=captain-question-{target_id}'
                    })

            # A pull request only needs the captain's merge decision once Firstmate
            # has actually recorded a keyed decision alongside a known PR link.
            pr = task.get('pr') if isinstance(task.get('pr'), dict) else {}
            pr_url = pr.get('url')
            if pr_url and any(d.get('verb') == 'needs-decision' for d in open_decisions):
                attention_items.append({
                    'id': 'pr-ready-' + str(task_id),
                    'title': 'PR Ready: ' + str(title),
                    'subtitle': 'Checks and review are complete. Your merge decision is needed.',
                    'type': 'pr_ready',
                    'status': 'ready',
                    'target_id': pr_url,
                    'project': project,
                    'revision': pr_url,
                    'url': f'/attention?item=pr-ready-{task_id}'
                })

        return attention_items

    @staticmethod
    def _activity_timestamp(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if len(value) == 10:
            value += 'T00:00:00Z'
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    async def get_recent_activity(self) -> List[Dict[str, Any]]:
        """Normalize real task requests and completions from the fleet snapshot."""
        snapshot = await self.get_snapshot()
        if snapshot.get('error'):
            raise RuntimeError(str(snapshot['error']))

        records = list(snapshot.get('backlog', {}).get('records', []))
        records.extend(snapshot.get('secondmate_landed', {}).get('records', []))
        items: List[Dict[str, Any]] = []
        seen = set()

        for record in records:
            if not isinstance(record, dict) or not record.get('id'):
                continue
            record_id = str(record['id'])
            completion = record.get('completion') if isinstance(record.get('completion'), dict) else {}
            verb = completion.get('verb')
            completed_at = self._activity_timestamp(completion.get('date'))
            requested_at = self._activity_timestamp(record.get('since'))

            if record.get('state') == 'done' or completed_at:
                if not completed_at or record_id in seen:
                    continue
                seen.add(record_id)
                activity_type = 'pull_request_merged' if verb == 'merged' else 'task_completed'
                description = 'Merged pull request' if verb == 'merged' else ('Completed report' if verb == 'reported' else 'Completed task')
                items.append({
                    'id': f'firstmate:{record_id}:{verb or "done"}',
                    'type': activity_type,
                    'title': record.get('title') or record_id,
                    'description': description,
                    'occurred_at': completed_at,
                    'source': 'firstmate',
                    'project': record.get('repo') or record.get('home_id') or 'Firstmate',
                    'url': record.get('pr_url'),
                    'pull_request_number': None,
                })
            elif record.get('state') in {'queued', 'in_flight'} and requested_at:
                if record_id in seen:
                    continue
                seen.add(record_id)
                items.append({
                    'id': f'firstmate:{record_id}:requested',
                    'type': 'task_requested',
                    'title': record.get('title') or record_id,
                    'description': 'Task requested',
                    'occurred_at': requested_at,
                    'source': 'firstmate',
                    'project': record.get('repo') or 'Firstmate',
                    'url': None,
                    'pull_request_number': None,
                })

        return sorted(items, key=lambda item: item['occurred_at'], reverse=True)
