import pytest
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_profile, update_profile, get_connected_accounts, upsert_connected_account, disconnect_account
from app.providers.github import GitHubProviderAdapter
from app.github_service import github_service
from conftest import TEST_HEADERS

client = TestClient(app)
HEADERS = TEST_HEADERS

def test_health():
    res = client.get('/api/v1/health', headers=HEADERS)
    assert res.status_code == 200
    assert res.json()['status'] == 'healthy'

def test_account_profile_crud():
    # Get profile
    res = client.get('/api/v1/account/profile', headers=HEADERS)
    assert res.status_code == 200
    prof = res.json()
    assert prof['user_id'] == 'default_user'

    # Update profile
    res_up = client.post('/api/v1/account/profile', data={'name': 'Spectre Admin', 'email': 'admin@magistrate.io'}, headers=HEADERS)
    assert res_up.status_code == 200
    assert res_up.json()['name'] == 'Spectre Admin'

def test_oauth_providers():
    res = client.get('/api/v1/auth/providers', headers=HEADERS)
    assert res.status_code == 200
    provs = res.json()
    assert len(provs) >= 4
    p_names = [p['provider'] for p in provs]
    assert 'github' in p_names
    assert 'twitter' in p_names

    # No provider credentials are configured in the test environment, so the
    # gateway reports an honest unavailable state instead of inventing OAuth.
    res_conn = client.get('/api/v1/auth/github/connect', headers=HEADERS, follow_redirects=False)
    assert res_conn.status_code == 503

    # Disconnect GitHub provider
    res_dis = client.post('/api/v1/auth/github/disconnect', headers=HEADERS)
    assert res_dis.status_code == 200
    assert res_dis.json()['status'] == 'disconnected'

def test_live_github_prs():
    res = client.get('/api/v1/github/pulls', headers=HEADERS)
    assert res.status_code in (200, 503)
    if res.status_code == 200:
        prs = res.json()
        assert isinstance(prs, (list, dict))

def test_voice_capabilities_do_not_expose_credentials():
    res = client.get('/api/v1/voice/capabilities', headers=HEADERS)
    assert res.status_code == 200
    payload = res.json()
    assert payload['schema_version'] == 'voice-capabilities.v1'
    assert all('api_key' not in item for item in payload['modes'])
    assert 'OPENAI_API_KEY' not in str(payload)


def test_voice_transcribe():
    res = client.post('/api/v1/voice/transcribe', data={'source': 'iphone'}, headers=HEADERS)
    assert res.status_code == 400
    assert res.json()['detail'] == 'A microphone recording is required.'
