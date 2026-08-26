from fastapi.testclient import TestClient

from app.main import app, voice_move_service

HEADERS = {'X-Magistrate-Token': 'magistrate-device-token-12345'}
client = TestClient(app)

class FakeHerdr:
    def __init__(self): self.calls = []
    async def list_agents(self):
        return [{'id': 'pane-api', 'pane_id': 'pane-api', 'name': 'api-agent', 'harness': 'codex', 'status': 'idle'},
                {'id': 'pane-firstmate', 'pane_id': 'pane-firstmate', 'name': 'captain', 'harness': 'codex', 'status': 'working'}]
    async def prompt_agent(self, target, text):
        self.calls.append(('prompt', target, text))
        return {'status': 'submitted', 'target': target, 'response': f'Accepted by {target}.'}
    async def interrupt_agent(self, target):
        self.calls.append(('interrupt', target))
        return {'status': 'interrupted', 'target': target}

def payload(**updates):
    value = {'schema_version': 'voice-move.v1', 'utterance': 'summarize the failing tests',
             'target': 'pane-api', 'source': 'voice-page', 'session_id': 'voice-session-test-1',
             'idempotency_key': 'voice-test-1234'}
    value.update(updates)
    return value

def test_prompt_requires_bound_confirmation(monkeypatch):
    fake = FakeHerdr(); monkeypatch.setattr(voice_move_service, 'herdr', fake)
    resolved = client.post('/api/v1/voice/moves', json=payload(), headers=HEADERS)
    assert resolved.status_code == 200
    move = resolved.json(); assert move['status'] == 'confirmation_required'; assert fake.calls == []
    executed = client.post('/api/v1/voice/moves', json=payload(execute=True, confirmation_token=move['confirmation_token']), headers=HEADERS)
    assert executed.json()['status'] == 'acknowledged'
    assert executed.json()['move_id'] == move['move_id']
    assert fake.calls == [('prompt', 'pane-api', 'summarize the failing tests')]

def test_invented_target_is_rejected(monkeypatch):
    monkeypatch.setattr(voice_move_service, 'herdr', FakeHerdr())
    response = client.post('/api/v1/voice/moves', json=payload(target='invented-agent', idempotency_key='voice-test-5678'), headers=HEADERS)
    assert response.status_code == 422
    assert 'live fleet' in response.json()['detail']

def test_firstmate_alias_resolves_by_name_before_harness(monkeypatch):
    fake = FakeHerdr(); monkeypatch.setattr(voice_move_service, 'herdr', fake)
    response = client.post('/api/v1/voice/moves', json=payload(target='firstmate', idempotency_key='voice-firstmate-1'), headers=HEADERS)
    assert response.json()['target'] == 'pane-firstmate'

def test_status_is_read_only_and_destructive_text_is_prohibited(monkeypatch):
    fake = FakeHerdr(); monkeypatch.setattr(voice_move_service, 'herdr', fake)
    ready = client.post('/api/v1/voice/moves', json=payload(utterance='what is the fleet status', idempotency_key='voice-status-1'), headers=HEADERS).json()
    assert ready['status'] == 'ready'; assert ready['impact'] == 'read'
    completed = client.post('/api/v1/voice/moves', json=payload(utterance='what is the fleet status', idempotency_key='voice-status-1', execute=True), headers=HEADERS).json()
    assert completed['status'] == 'completed'; assert '2 live agents' in completed['response']; assert fake.calls == []
    blocked = client.post('/api/v1/voice/moves', json=payload(utterance='run shell rm -rf build', idempotency_key='voice-block-1'), headers=HEADERS).json()
    assert blocked['status'] == 'prohibited'; assert fake.calls == []


def test_voice_contract_rejects_whitespace_and_navigation_is_not_prompt(monkeypatch):
    monkeypatch.setattr(voice_move_service, 'herdr', FakeHerdr())
    invalid = client.post('/api/v1/voice/moves', json=payload(utterance='   ', idempotency_key='voice-whitespace-1'), headers=HEADERS)
    assert invalid.status_code == 422
    navigation = client.post('/api/v1/voice/moves', json=payload(utterance='open the chat', idempotency_key='voice-navigation-1'), headers=HEADERS)
    assert navigation.json()['status'] == 'unsupported'


def test_confirmation_replay_and_mutation_are_bound(monkeypatch):
    fake = FakeHerdr(); monkeypatch.setattr(voice_move_service, 'herdr', fake)
    first = client.post('/api/v1/voice/moves', json=payload(idempotency_key='voice-replay-1'), headers=HEADERS).json()
    replay = client.post('/api/v1/voice/moves', json=payload(idempotency_key='voice-replay-1'), headers=HEADERS).json()
    assert replay['confirmation_token'] == first['confirmation_token']
    conflict = client.post('/api/v1/voice/moves', json=payload(idempotency_key='voice-replay-1', utterance='send a different request'), headers=HEADERS)
    assert conflict.status_code == 409
    cancelled = client.post(f"/api/v1/voice/moves/{first['move_id']}/cancel", json={'session_id': 'voice-session-test-1'}, headers=HEADERS)
    assert cancelled.json()['status'] == 'cancelled'
    assert fake.calls == []


def test_result_polling_is_correlated_and_session_bound(monkeypatch):
    fake = FakeHerdr(); monkeypatch.setattr(voice_move_service, 'herdr', fake)
    resolved = client.post('/api/v1/voice/moves', json=payload(idempotency_key='voice-result-1'), headers=HEADERS).json()
    completed = client.post('/api/v1/voice/moves', json=payload(idempotency_key='voice-result-1', execute=True, confirmation_token=resolved['confirmation_token']), headers=HEADERS).json()
    result = client.get(f"/api/v1/voice/moves/{completed['move_id']}?session_id=voice-session-test-1", headers=HEADERS)
    assert result.status_code == 200
    assert result.json()['move_id'] == completed['move_id']
    wrong_session = client.get(f"/api/v1/voice/moves/{completed['move_id']}?session_id=other-session-test-1", headers=HEADERS)
    assert wrong_session.status_code == 404
