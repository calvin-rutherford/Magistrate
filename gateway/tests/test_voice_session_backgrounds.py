import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_profile, update_profile

client = TestClient(app)
HEADERS = {'X-Magistrate-Token': 'magistrate-device-token-12345'}

def test_voice_prompt_session():
    res = client.post('/api/v1/captain/prompt', json={'source': 'iphone', 'modality': 'voice', 'type': 'prompt', 'text': 'Firstmate, what needs my attention?', 'target': 'captain'}, headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)

def test_captain_output_stream():
    res = client.get('/api/v1/captain/output?lines=50', headers=HEADERS)
    assert res.status_code == 200
    assert 'output' in res.json()

def test_background_customization_persistence():
    prof = get_profile('default_user')
    assert prof['user_id'] == 'default_user'
    up = update_profile('default_user', bio='Voice-first operator')
