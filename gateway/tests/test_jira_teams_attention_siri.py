import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter
from app.attention_service import attention_service
from app.notifications import register_push_token

client = TestClient(app)
HEADERS = {'X-Magistrate-Token': 'magistrate-device-token-12345'}

def test_jira_provider():
    adapter = JiraProviderAdapter()
    assert adapter.provider_name() == 'jira'
    assert 'read_assigned_issues' in adapter.capabilities()
    res = client.get('/api/v1/jira/issues', headers=HEADERS)
    assert res.status_code == 200
    issues = res.json()
    assert isinstance(issues, list)
    assert len(issues) > 0
    assert 'key' in issues[0]

def test_teams_provider():
    adapter = TeamsProviderAdapter()
    assert adapter.provider_name() == 'teams'
    assert 'read_mentions' in adapter.capabilities()
    res = client.get('/api/v1/teams/mentions', headers=HEADERS)
    assert res.status_code == 200
    mentions = res.json()
    assert isinstance(mentions, list)
    assert len(mentions) > 0
    assert 'sender' in mentions[0]

def test_unified_attention_service():
    res = client.get('/api/v1/attention/unified', headers=HEADERS)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    providers_in_items = [i['provider'] for i in items]
    assert 'jira' in providers_in_items
    assert 'teams' in providers_in_items

def test_push_notification_registration():
    res = client.post('/api/v1/notifications/register', data={'push_token': 'ExponentPushToken[demo_123]', 'platform': 'ios'}, headers=HEADERS)
    assert res.status_code == 200
    assert res.json()['status'] == 'registered'
