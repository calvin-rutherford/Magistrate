import pytest
from app.firstmate_client import FirstmateClient


def make_snapshot(tasks, records=None):
    return {
        'schema': 'fm-fleet-snapshot.v1',
        'tasks': tasks,
        'backlog': {'records': records or []}
    }


def test_firstmate_agent_names_prefer_matched_task_title_and_keep_unmatched_herdr_fallback():
    agents = [
        {'id': 'w1D:p2', 'pane_id': 'w1D:p2', 'name': 'raw-pane'},
        {'id': 'w2:p3', 'pane_id': 'w2:p3', 'name': 'Herdr worker'},
    ]
    snapshot = {'tasks': [{
        'id': 'chat-fix-c9',
        'endpoint': {'target': 'default:w1D:p2'},
        'backlog': {'title': 'Fix chat presentation'},
    }]}

    mapped = FirstmateClient.apply_agent_display_names(agents, snapshot)

    assert mapped[0]['name'] == 'Fix chat presentation'
    assert mapped[0]['display_name_source'] == 'firstmate'
    assert mapped[1]['name'] == 'Herdr worker'
    assert mapped[1]['display_name_source'] == 'herdr'


def test_firstmate_runtime_mapping_uses_only_observed_records_and_fails_closed():
    agents = [
        {'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'worker', 'harness': 'herdr-harness', 'model': 'observed-model'},
        {'id': 'w1:p2', 'pane_id': 'w1:p2', 'name': 'missing-runtime'},
    ]
    snapshot = {'tasks': [
        {'endpoint': {'target': 'default:w1:p1'}, 'harness': 'spawn-harness'},
        {'endpoint': {'target': 'default:w1:p2'}},
    ]}

    mapped = FirstmateClient.apply_agent_display_names(agents, snapshot)

    assert mapped[0]['harness'] == 'spawn-harness'
    assert mapped[0]['model'] == 'observed-model'
    assert mapped[0]['runtime_sources'] == {'harness': 'firstmate', 'model': 'herdr'}
    assert mapped[1]['harness'] is None
    assert mapped[1]['model'] is None
    assert mapped[1]['runtime_sources'] == {'harness': None, 'model': None}


def test_firstmate_agent_name_mapping_never_fabricates_missing_identity():
    agents = [{'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'w1:p1'}]
    snapshot = {'tasks': [{'endpoint': {'target': 'default:w1:p1'}, 'backlog': {}}]}
    assert FirstmateClient.apply_agent_display_names(agents, snapshot) == [
        {
            'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'w1:p1',
            'harness': None, 'model': None,
            'runtime_sources': {'harness': None, 'model': None},
            'display_name_source': 'herdr',
        }
    ]


@pytest.mark.asyncio
async def test_needs_decision_task_surfaces_as_attention_item(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(
        tasks=[{
            'id': 'demo-task-1',
            'project': '/home/user/Magistrate',
            'hints': {
                'pending_decision': True,
                'blocked_event': False,
                'open_decisions': [{'key': 'demo-task-1', 'verb': 'needs-decision', 'summary': 'Pick a rollout strategy.'}],
                'last_event_text': 'needs-decision: Pick a rollout strategy.'
            },
            'pr': None
        }],
        records=[{'id': 'demo-task-1', 'title': 'Ship the rollout', 'repo': 'Magistrate'}]
    )
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert len(items) == 1
    assert items[0]['title'] == 'Ship the rollout'
    assert items[0]['subtitle'] == 'Pick a rollout strategy.'
    assert items[0]['status'] == 'needs-decision'
    assert items[0]['project'] == 'Magistrate'
    assert items[0]['deep_link'] == '/attention?item=captain-question-demo-task-1'
    assert items[0]['context']['decision_key'] == 'demo-task-1'


@pytest.mark.asyncio
async def test_blocked_task_surfaces_as_attention_item(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-2',
        'project': '/home/user/Magistrate',
        'hints': {
            'pending_decision': False,
            'blocked_event': True,
            'open_decisions': [{'key': 'demo-task-2', 'verb': 'blocked', 'summary': 'Waiting on external API key.'}],
            'last_event_text': 'blocked: Waiting on external API key.'
        },
        'pr': None
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert len(items) == 1
    assert items[0]['status'] == 'awaiting_answer'
    assert items[0]['subtitle'] == 'Waiting on external API key.'


@pytest.mark.asyncio
async def test_pr_ready_requires_both_pr_url_and_needs_decision(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-3',
        'project': '/home/user/Magistrate',
        'hints': {
            'pending_decision': True,
            'blocked_event': False,
            'open_decisions': [{'key': 'demo-task-3', 'verb': 'needs-decision', 'summary': 'Merge decision needed.'}],
            'last_event_text': ''
        },
        'pr': {'url': 'https://github.com/example/repo/pull/9'}
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    kinds = {item['type'] for item in items}
    assert 'captain_question' in kinds
    assert 'pr_ready' in kinds
    pr_item = next(item for item in items if item['type'] == 'pr_ready')
    assert pr_item['target_id'] == 'https://github.com/example/repo/pull/9'


@pytest.mark.asyncio
async def test_task_with_no_open_decisions_is_not_attention_worthy(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-4',
        'project': '/home/user/Magistrate',
        'hints': {'pending_decision': False, 'blocked_event': False, 'open_decisions': [], 'last_event_text': 'working: on it'},
        'pr': None
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert items == []


@pytest.mark.asyncio
async def test_recent_activity_uses_only_dated_requests_and_completions(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[], records=[
        {'id': 'new-work', 'title': 'Captain request', 'repo': 'Magistrate', 'state': 'queued', 'since': '2026-08-28'},
        {'id': 'finished', 'title': 'Finished task', 'repo': 'Magistrate', 'state': 'done', 'completion': {'verb': 'done', 'date': '2026-08-27'}},
        {'id': 'undated', 'title': 'No invented timestamp', 'state': 'queued'},
    ])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_recent_activity()
    assert [item['id'] for item in items] == ['firstmate:new-work:requested', 'firstmate:finished:done']
    assert items[0]['type'] == 'task_requested'
    assert items[1]['description'] == 'Completed task'


@pytest.mark.asyncio
async def test_recent_activity_includes_and_deduplicates_landed_records(monkeypatch):
    client = FirstmateClient()
    completed = {'id': 'landed', 'title': 'Landed work', 'state': 'done', 'completion': {'verb': 'merged', 'date': '2026-08-28'}, 'pr_url': 'https://github.com/acme/ship/pull/7'}
    snapshot = make_snapshot(tasks=[], records=[completed])
    snapshot['secondmate_landed'] = {'records': [{**completed, 'home_id': 'secondmate'}]}
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_recent_activity()
    assert len(items) == 1
    assert items[0]['type'] == 'pull_request_merged'
    assert items[0]['url'].endswith('/pull/7')


async def _future(value):
    return value
