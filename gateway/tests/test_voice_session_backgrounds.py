from fastapi.testclient import TestClient
from app.main import app
from app.db import get_profile, update_profile
from conftest import TEST_HEADERS

client = TestClient(app)
HEADERS = TEST_HEADERS

def test_captain_output_stream():
    res = client.get('/api/v1/captain/output?lines=50', headers=HEADERS)
    assert res.status_code == 200
    assert 'output' in res.json()

def test_background_customization_persistence():
    prof = get_profile('default_user')
    assert prof['user_id'] == 'default_user'
    up = update_profile('default_user', bio='Voice-first operator')
