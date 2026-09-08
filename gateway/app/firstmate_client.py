import asyncio
import json
import os
import re
import signal
import stat as stat_module
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

FIRSTMATE_HOME = os.getenv('FM_HOME', '/home/spectre/firstmate')
FIRSTMATE_SNAPSHOT_TIMEOUT_SECONDS = 20.0
FIRSTMATE_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
_GENERIC_AGENT_NAMES = {'magistrate', 'firstmate', 'π - magistrate', 'π - firstmate'}


class _SnapshotOutputTooLarge(Exception):
    pass


def _strict_snapshot_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Fleet snapshot contains duplicate JSON keys')
        result[key] = value
    return result


def _reject_snapshot_constant(_: str):
    raise ValueError('Fleet snapshot contains a non-finite JSON number')


async def _read_bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
    chunks: List[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(min(64 * 1024, maximum + 1 - size))
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > maximum:
            raise _SnapshotOutputTooLarge


def _task_display_name(value: Any, target: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.lower() in _GENERIC_AGENT_NAMES or name.lower() in {target.lower(), f'default:{target}'.lower()}:
        return None
    if re.match(r'^(?:pane|tab|workspace)(?:[_ -]?id)?\s*[:=]', name, re.IGNORECASE):
        return None
    return name

class FirstmateClient:
    def __init__(
        self,
        fm_home: str = FIRSTMATE_HOME,
        *,
        snapshot_timeout: float = FIRSTMATE_SNAPSHOT_TIMEOUT_SECONDS,
        snapshot_max_bytes: int = FIRSTMATE_SNAPSHOT_MAX_BYTES,
    ):
        self.fm_home = fm_home
        self.snapshot_script = os.path.join(fm_home, 'bin', 'fm-fleet-snapshot.sh')
        self.snapshot_timeout = snapshot_timeout
        self.snapshot_max_bytes = snapshot_max_bytes

    async def get_snapshot(self) -> Dict[str, Any]:
        try:
            script_stat = os.lstat(self.snapshot_script)
        except FileNotFoundError:
            script_stat = None
        except OSError:
            return {
                'schema': 'fm-fleet-snapshot.v1', 'fm_home': self.fm_home,
                'tasks': [], 'available': False, 'error': 'Fleet snapshot reader is unavailable',
            }
        if script_stat is None:
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'fm_home': self.fm_home,
                'available': False,
                'tasks': [],
                'scout_reports': [],
                'secondmate_current': {'records': []},
                'error': 'Snapshot script not found'
            }
        if (
            not stat_module.S_ISREG(script_stat.st_mode) or stat_module.S_ISLNK(script_stat.st_mode)
            or script_stat.st_nlink != 1 or script_stat.st_uid != os.geteuid()
            or script_stat.st_mode & 0o002 or not os.access(self.snapshot_script, os.X_OK)
        ):
            return {
                'schema': 'fm-fleet-snapshot.v1', 'fm_home': self.fm_home,
                'tasks': [], 'scout_reports': [], 'available': False,
                'secondmate_current': {'records': []},
                'error': 'Fleet snapshot reader is not trusted',
            }

        environment = {
            'FM_HOME': self.fm_home,
            'PATH': '/usr/local/bin:/usr/bin:/bin',
            'HOME': '/nonexistent',
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                self.snapshot_script, '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.fm_home,
                env=environment,
                start_new_session=True,
            )
            async def collect_output():
                stdout, stderr, _ = await asyncio.gather(
                    _read_bounded(proc.stdout, self.snapshot_max_bytes),
                    _read_bounded(proc.stderr, 64 * 1024),
                    proc.wait(),
                )
                return stdout, stderr

            try:
                stdout, stderr = await asyncio.wait_for(
                    collect_output(), timeout=self.snapshot_timeout,
                )
            except asyncio.CancelledError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                raise
            except (asyncio.TimeoutError, _SnapshotOutputTooLarge) as exc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                reason = 'timed out' if isinstance(exc, asyncio.TimeoutError) else 'exceeded its bounded output size'
                return {
                    'schema': 'fm-fleet-snapshot.v1', 'fm_home': self.fm_home,
                    'tasks': [], 'available': False, 'error': f'Fleet snapshot {reason}',
                }

            if proc.returncode == 0 and stdout:
                parsed = json.loads(
                    stdout.decode('utf-8', errors='strict'),
                    object_pairs_hook=_strict_snapshot_object,
                    parse_constant=_reject_snapshot_constant,
                )
                if not isinstance(parsed, dict):
                    raise ValueError('Fleet snapshot is not an object')
                if parsed.get('schema') != 'fm-fleet-snapshot.v1' or not isinstance(parsed.get('tasks'), list):
                    raise ValueError('Fleet snapshot has an invalid schema')
                if parsed.get('error'):
                    return {**parsed, 'fm_home': self.fm_home, 'available': False}
                return {**parsed, 'fm_home': self.fm_home, 'available': True}
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'fm_home': self.fm_home,
                'tasks': [], 'available': False,
                'error': 'Fleet snapshot command failed' if stderr else 'Failed to run fleet snapshot'
            }
        except Exception as e:
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'fm_home': self.fm_home,
                'tasks': [], 'available': False,
                'error': f'{type(e).__name__}: fleet snapshot unavailable'[:240]
            }

    @staticmethod
    def _tasks_by_target(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        tasks: Dict[str, Dict[str, Any]] = {}
        for task in snapshot.get('tasks', []):
            if not isinstance(task, dict):
                continue
            endpoint = task.get('endpoint') if isinstance(task.get('endpoint'), dict) else {}
            target = endpoint.get('target') or task.get('agent_id') or task.get('pane_id')
            if not isinstance(target, str) or not target:
                continue
            if target.startswith('default:'):
                target = target[len('default:'):]
            tasks[target] = task
        return tasks

    @staticmethod
    def apply_agent_display_names(herdr_agents: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Join only observed Firstmate task identity and runtime metadata.

        The spawning record wins when it carries a harness/model. Missing fields
        remain ``None``; a selectable Gateway profile is never evidence of what
        a live process is actually running.
        """
        tasks_by_target = FirstmateClient._tasks_by_target(snapshot)
        result = []
        for agent in herdr_agents:
            target = str(agent.get('pane_id') or agent.get('id') or '')
            task = tasks_by_target.get(target)
            backlog = task.get('backlog') if task and isinstance(task.get('backlog'), dict) else {}
            assigned_name = None
            if task:
                for candidate in (task.get('worker_label'), task.get('label'), backlog.get('title')):
                    assigned_name = _task_display_name(candidate, target)
                    if assigned_name:
                        break
            task_harness = task.get('harness').strip() if task and isinstance(task.get('harness'), str) and task.get('harness').strip() else None
            task_model = task.get('model').strip() if task and isinstance(task.get('model'), str) and task.get('model').strip() else None
            herdr_harness = agent.get('harness').strip() if isinstance(agent.get('harness'), str) and agent.get('harness').strip() else None
            herdr_model = agent.get('model').strip() if isinstance(agent.get('model'), str) and agent.get('model').strip() else None
            harness = task_harness or herdr_harness
            model = task_model or herdr_model
            result.append({
                **agent,
                'harness': harness,
                'model': model,
                'runtime_sources': {
                    'harness': 'firstmate' if task_harness else ('herdr' if herdr_harness else None),
                    'model': 'firstmate' if task_model else ('herdr' if herdr_model else None),
                },
                **({'name': assigned_name, 'display_name_source': 'firstmate'} if assigned_name else {'display_name_source': 'herdr'}),
            })
        return result

    @staticmethod
    def migration_context(agent_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Describe real Firstmate context available to an operator relaunch."""
        task = FirstmateClient._tasks_by_target(snapshot).get(agent_id)
        if not task:
            return {
                'task_id': None, 'worktree': None, 'brief': None, 'progress': None,
                'preservation_plan': ['worktree', 'branch', 'brief', 'progress'],
                'not_preserved': ['in-flight turn'],
            }
        paths = task.get('paths') if isinstance(task.get('paths'), dict) else {}
        worktree = paths.get('worktree') if isinstance(paths.get('worktree'), dict) else {}
        backlog = task.get('backlog') if isinstance(task.get('backlog'), dict) else {}
        status_log = paths.get('status_log') if isinstance(paths.get('status_log'), dict) else {}
        return {
            'task_id': task.get('id'),
            'worktree': worktree.get('path') if worktree.get('present') is True else None,
            # Firstmate's snapshot exposes the durable brief and status record,
            # but not the checked-out branch name. The terminal operator must
            # verify/reuse that branch from the observed worktree.
            'branch': None,
            'brief': backlog.get('body_excerpt') or backlog.get('title'),
            'progress': status_log.get('last_event') or task.get('current_state'),
            'preservation_plan': ['worktree', 'branch', 'brief', 'progress'],
            'not_preserved': ['in-flight turn'],
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
            agent_target = task.get('agent_id') or task.get('agent') or task.get('pane_id')
            agent_link = f'/chat?agentId={agent_target}' if agent_target else None
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
                        'url': f'/attention?item=captain-question-{target_id}',
                        'deep_link': f'/attention?item=captain-question-{target_id}',
                        'agent_link': agent_link,
                        'context': {'task_id': task_id, 'agent_target': agent_target, 'decision_key': target_id, 'project': project}
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
                        'url': f'/attention?item=captain-question-{target_id}',
                        'deep_link': f'/attention?item=captain-question-{target_id}',
                        'agent_link': agent_link,
                        'context': {'task_id': task_id, 'agent_target': agent_target, 'decision_key': target_id, 'project': project}
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
                    'consequential': True,
                    'status': 'ready',
                    'target_id': pr_url,
                    'project': project,
                    'revision': pr_url,
                    'url': f'/attention?item=pr-ready-{task_id}',
                    'deep_link': (f'/pr-detail?number={match.group(1)}' if (match := re.search(r'/pull/(\d+)', pr_url)) else f'/attention?item=pr-ready-{task_id}'),
                    'agent_link': agent_link,
                    'external_url': pr_url,
                    'context': {'task_id': task_id, 'agent_target': agent_target, 'project': project, 'pull_request_url': pr_url}
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
