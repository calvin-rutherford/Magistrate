import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.attention_actions as actions
import app.main as main
from app.auth import issue_session
from conftest import TEST_HEADERS


async def _empty_items():
    return []


def decision_item(summary='Choose the rollout window.', revision='r1'):
    return {
        'id': 'captain-question-task-1',
        'provider': 'firstmate',
        'title': 'Rollout choice',
        'subtitle': summary,
        'status': 'needs-decision',
        'notification_kind': 'captain_question',
        'revision': revision,
        'requires_action': True,
        'context': {'task_id': 'task-1', 'decision_key': 'decision-1'},
    }


def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(actions.db, 'DB_PATH', str(tmp_path / 'actions.sqlite3'))
    actions.db.init_db()


def test_action_is_server_issued_and_binds_exact_target_and_revision(monkeypatch, tmp_path):
    fresh_db(monkeypatch, tmp_path)
    item = decision_item()
    contract = actions.action_for_item(item)
    assert contract['action_key'].startswith('aa1_')
    assert contract['target'] == {'provider': 'firstmate', 'task_id': 'task-1', 'decision_key': 'decision-1'}
    assert contract['allowed_actions'] == ['approve', 'reject']
    assert contract['confirmation_required'] is True
    assert contract['reversible'] is True
    assert actions.action_key_for(item) == contract['action_key']
    assert actions.action_key_for(decision_item(revision='r2')) != contract['action_key']


@pytest.mark.asyncio
async def test_confirmation_execution_audits_and_is_idempotent(monkeypatch, tmp_path):
    fresh_db(monkeypatch, tmp_path)
    item = decision_item()
    action = actions.action_for_item(item)
    confirmation = actions.prepare_confirmation(
        [item], action['action_key'], 'approve', 'task-1', 'owner', 'session-1'
    )
    calls = []

    async def executor(target, selected, home):
        calls.append((target, selected, home))
        return {'ok': True}

    result = await actions.execute_confirmation(
        [item], action['action_key'], 'approve', 'task-1', confirmation['confirmation_token'],
        'owner', 'session-1', '/fm', executor=executor,
    )
    assert result['status'] == 'succeeded'
    assert result['evidence']['decision_key'] == 'decision-1'
    assert result['evidence']['target_id'] == 'task-1'
    assert result['evidence']['recorded'] is True
    retry = await actions.execute_confirmation(
        [item], action['action_key'], 'approve', 'task-1', 'ignored-on-retry',
        'owner', 'session-2', '/fm', executor=executor,
    )
    assert retry['status'] == 'succeeded'
    assert retry['idempotent'] is True
    assert calls == [('task-1', 'approve', '/fm')]
    with sqlite3.connect(actions.db.DB_PATH) as conn:
        row = conn.execute('SELECT actor_session_id, action, status, evidence_json FROM attention_action_outcomes').fetchone()
    assert row[:3] == ('session-1', 'approve', 'succeeded')
    assert 'secret' not in row[3].lower()


@pytest.mark.asyncio
async def test_confirmation_cancel_missing_confirmation_stale_and_mismatch_are_rejected(monkeypatch, tmp_path):
    fresh_db(monkeypatch, tmp_path)
    item = decision_item()
    action = actions.action_for_item(item)
    with pytest.raises(actions.AttentionActionError) as missing:
        await actions.execute_confirmation([item], action['action_key'], 'reject', 'task-1', '', 'owner', 's1', '/fm')
    assert missing.value.code == 'confirmation_required'
    with pytest.raises(actions.AttentionActionError) as mismatch:
        actions.prepare_confirmation([item], action['action_key'], 'approve', 'other-task', 'owner', 's1')
    assert mismatch.value.code == 'mismatch'
    with pytest.raises(actions.AttentionActionError) as stale:
        actions.prepare_confirmation([], action['action_key'], 'approve', 'task-1', 'owner', 's1')
    assert stale.value.code == 'stale'


def test_destructive_or_security_sensitive_decisions_are_not_actionable(monkeypatch, tmp_path):
    fresh_db(monkeypatch, tmp_path)
    contract = actions.action_for_item(decision_item('Approve production deployment with the credential rotation.'))
    assert contract['status'] == 'unsupported'
    assert contract['allowed_actions'] == []
    with pytest.raises(actions.AttentionActionError) as error:
        actions.prepare_confirmation([decision_item('Approve production deployment with the credential rotation.')], contract['action_key'], 'approve', 'task-1', 'owner', 's1')
    assert error.value.code == 'unsupported_risk'


def test_gateway_requires_authenticated_command_owner_and_prepares_confirmation(monkeypatch):
    item = decision_item()

    async def live_items():
        return [item]

    monkeypatch.setattr(main.attention_service, 'get_unified_attention_items', live_items)
    # The attention service normally decorates items; use the same live shape
    # here to keep this contract test independent of provider processes.
    item['action'] = actions.action_for_item(item)
    client = TestClient(main.app)
    unauthenticated = client.post('/api/v1/attention/actions/aa1_aaaaaaaa/prepare', json={
        'action_key': 'aa1_aaaaaaaa', 'action': 'approve', 'target_id': 'task-1'
    })
    assert unauthenticated.status_code == 401
    prepared = client.post(
        f"/api/v1/attention/actions/{item['action']['action_key']}/prepare",
        headers=TEST_HEADERS,
        json={'action_key': item['action']['action_key'], 'action': 'approve', 'target_id': 'task-1'},
    )
    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload['status'] == 'confirmation_required'
    assert payload['target']['task_id'] == 'task-1'
    assert 'confirmation_token' in payload

    calls = []

    async def fake_executor(target, selected, home):
        calls.append((target, selected))
        return {'ok': True}

    monkeypatch.setattr(actions, 'execute_firstmate_action', fake_executor)
    executed = client.post(
        f"/api/v1/attention/actions/{item['action']['action_key']}/execute",
        headers=TEST_HEADERS,
        json={'action_key': item['action']['action_key'], 'action': 'approve', 'target_id': 'task-1', 'confirmation_token': payload['confirmation_token']},
    )
    assert executed.status_code == 200
    assert executed.json()['status'] == 'succeeded'
    assert calls == [('task-1', 'approve')]
    # A resolved source may disappear on the next snapshot; the durable
    # outcome still makes the exact retry idempotent rather than re-executing.
    monkeypatch.setattr(main.attention_service, 'get_unified_attention_items', lambda: _empty_items())
    replay = client.post(
        f"/api/v1/attention/actions/{item['action']['action_key']}/execute",
        headers=TEST_HEADERS,
        json={'action_key': item['action']['action_key'], 'action': 'approve', 'target_id': 'task-1', 'confirmation_token': 'not-used-on-replay'},
    )
    assert replay.status_code == 200 and replay.json()['idempotent'] is True
    reloaded = client.get('/api/v1/attention/actions/by-item/captain-question-task-1', headers=TEST_HEADERS)
    assert reloaded.status_code == 200 and reloaded.json()['status'] == 'succeeded'
    assert reloaded.json()['evidence']['target_id'] == 'task-1'


def test_notification_ack_is_not_an_attention_action(monkeypatch, tmp_path):
    fresh_db(monkeypatch, tmp_path)
    # The notification table's viewed flag is intentionally a separate store;
    # no action outcome is created by acknowledging a notification.
    import app.notifications as notifications
    monkeypatch.setattr(notifications, 'DB_PATH', actions.db.DB_PATH)
    notifications.reconcile_notification_events('owner', [decision_item()])
    notifications.acknowledge_notification_events('owner', ['captain-question-task-1'])
    with sqlite3.connect(actions.db.DB_PATH) as conn:
        assert conn.execute('SELECT COUNT(*) FROM attention_action_outcomes').fetchone()[0] == 0
