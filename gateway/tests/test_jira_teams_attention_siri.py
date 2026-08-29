from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter

client = TestClient(app)
HEADERS = {'X-Magistrate-Token': 'magistrate-device-token-12345'}

def test_jira_provider():
    adapter = JiraProviderAdapter()
    assert adapter.provider_name() == 'jira'
    assert 'read_assigned_issues' in adapter.capabilities()
    res = client.get('/api/v1/jira/issues', headers=HEADERS)
    assert res.status_code == 200
    issues = res.json()
    assert issues == []

def test_teams_provider():
    adapter = TeamsProviderAdapter()
    assert adapter.provider_name() == 'teams'
    assert 'read_mentions' in adapter.capabilities()
    res = client.get('/api/v1/teams/mentions', headers=HEADERS)
    assert res.status_code == 200
    mentions = res.json()
    assert mentions == []

def test_unified_attention_service_has_no_placeholder_items(monkeypatch):
    monkeypatch.setattr('app.attention_service.herdr_client.list_agents', AsyncMock(return_value=[]))
    monkeypatch.setattr('app.attention_service.fm_client.get_attention_items', AsyncMock(return_value=[]))
    monkeypatch.setattr('app.attention_service.github_service.get_pull_requests', AsyncMock(return_value={'items': []}))
    res = client.get('/api/v1/attention/unified', headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == []

def test_push_notification_registration():
    res = client.post('/api/v1/notifications/register', data={'push_token': 'ExponentPushToken[demo_123]', 'platform': 'ios'}, headers=HEADERS)
    assert res.status_code == 200
    assert res.json()['status'] == 'registered'
