import pytest
from app.firstmate_client import FirstmateClient


def make_snapshot(tasks, records=None):
    return {
        'schema': 'fm-fleet-snapshot.v1',
        'tasks': tasks,
        'backlog': {'records': records or []}
    }


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


async def _future(value):
    return value
