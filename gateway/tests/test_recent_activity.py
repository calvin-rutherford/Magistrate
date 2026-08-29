import pytest
from fastapi.testclient import TestClient

from app.auth import MAGISTRATE_TOKEN
from app.main import app, recent_activity_service
from app.recent_activity import RecentActivityService


class StubFirstmate:
    async def get_recent_activity(self):
        return [{
            'id': 'firstmate:request', 'type': 'task_requested', 'title': 'New request',
            'description': 'Task requested', 'occurred_at': '2026-08-28T00:00:00Z',
            'source': 'firstmate', 'project': 'Magistrate', 'url': None,
            'pull_request_number': None,
        }, {
            'id': 'firstmate:merged', 'type': 'pull_request_merged', 'title': 'Merged work',
            'description': 'Merged pull request', 'occurred_at': '2026-08-27T00:00:00Z',
            'source': 'firstmate', 'project': 'Magistrate',
            'url': 'https://github.com/acme/ship/pull/42', 'pull_request_number': None,
        }]


class StubGitHub:
    async def get_merged_pull_requests(self, limit, refresh):
        return [{'number': 42, 'title': 'Merged work', 'merged_at': '2026-08-28T12:00:00Z', 'repository': 'acme/ship', 'url': 'https://github.com/acme/ship/pull/42'}]


@pytest.mark.asyncio
async def test_feed_is_newest_first_and_deduplicates_snapshot_pr():
    feed = await RecentActivityService(StubFirstmate(), StubGitHub()).get_recent_activity()
    assert [item['id'] for item in feed['items']] == ['github:pull:42:merged', 'firstmate:request']
    assert feed['sources'] == {'firstmate': 'available', 'github': 'available'}


@pytest.mark.asyncio
async def test_feed_keeps_real_partial_source_data():
    class FailedGitHub:
        async def get_merged_pull_requests(self, limit, refresh):
            raise RuntimeError('offline')
    feed = await RecentActivityService(StubFirstmate(), FailedGitHub()).get_recent_activity(limit=1)
    assert feed['items'][0]['title'] == 'New request'
    assert feed['sources']['github'] == 'unavailable'


def test_recent_activity_endpoint_is_authenticated_and_returns_feed(monkeypatch):
    async def fake_feed(limit, refresh):
        assert (limit, refresh) == (8, True)
        return {'items': [], 'sources': {'firstmate': 'available', 'github': 'available'}}
    monkeypatch.setattr(recent_activity_service, 'get_recent_activity', fake_feed)
    client = TestClient(app)
    assert client.get('/api/v1/recent-activity').status_code == 401
    response = client.get(
        '/api/v1/recent-activity?limit=8&refresh=true',
        headers={'X-Magistrate-Token': MAGISTRATE_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()['items'] == []
