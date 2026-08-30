import pytest
import app.notifications as notifications


def item(item_id='question-1', revision='1', kind='captain_question'):
    return {
        'id': item_id,
        'title': 'Deployment choice',
        'subtitle': 'Choose the rollout window.',
        'status': 'needs-decision',
        'requires_action': True,
        'notification_kind': kind,
        'revision': revision,
        'url': f'/attention?item={item_id}',
    }


def setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications, 'DB_PATH', str(tmp_path / 'notifications.sqlite3'))


def test_transition_dedupe_material_update_and_resolution(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    first = notifications.reconcile_notification_events('captain', [item()])
    assert [event['id'] for event in first['events']] == ['question-1']
    notifications.acknowledge_notification_events('captain', ['question-1'])
    assert notifications.reconcile_notification_events('captain', [item()])['events'] == []

    changed = notifications.reconcile_notification_events('captain', [item(revision='2')])
    assert [event['revision'] for event in changed['events']] == ['2']
    notifications.acknowledge_notification_events('captain', ['question-1'])
    assert notifications.reconcile_notification_events('captain', [])['events'] == []
    assert [event['id'] for event in notifications.reconcile_notification_events('captain', [item(revision='2')])['events']] == ['question-1']


def test_batches_actionable_items_and_ignores_infrastructure_block(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    infrastructure = item('infra')
    infrastructure['notification_kind'] = 'infrastructure_failure'
    events = notifications.reconcile_notification_events(
        'captain', [item(), item('pr-8', kind='pr_ready'), infrastructure]
    )['events']
    assert [event['id'] for event in events] == ['question-1', 'pr-8']
    assert events[1]['url'] == '/attention?item=pr-8'


def test_foreground_suppression_dedupes_later_background_poll(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    foreground = notifications.reconcile_notification_events('captain', [item()], foreground=True)
    assert foreground['events'] == []
    assert foreground['suppressed_foreground'] is True
    assert notifications.reconcile_notification_events('captain', [item()], foreground=False)['events'] == []


def test_quiet_hours_defer_until_quiet_period_ends(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    notifications.update_notification_preferences('captain', True, 22, 7)
    quiet = notifications.reconcile_notification_events('captain', [item()], local_hour=23)
    assert quiet['quiet'] is True
    assert quiet['events'] == []
    awake = notifications.reconcile_notification_events('captain', [item()], local_hour=8)
    assert [event['id'] for event in awake['events']] == ['question-1']


def test_disabled_preferences_suppress_without_losing_transition(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    notifications.update_notification_preferences('captain', False, None, None)
    assert notifications.reconcile_notification_events('captain', [item()])['events'] == []
    notifications.update_notification_preferences('captain', True, None, None)
    assert [event['id'] for event in notifications.reconcile_notification_events('captain', [item()])['events']] == ['question-1']


def test_permission_modes_filter_without_losing_future_transition(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    pr = item('pr-1', kind='pr_ready')
    notifications.update_notification_preferences('captain', True, None, None, 'restricted')
    assert notifications.reconcile_notification_events('captain', [pr])['events'] == []
    notifications.update_notification_preferences('captain', True, None, None, 'moderate')
    assert [event['id'] for event in notifications.reconcile_notification_events('captain', [pr])['events']] == ['pr-1']
    notifications.acknowledge_notification_events('captain', ['pr-1'])
    notifications.update_notification_preferences('captain', True, None, None, 'full')
    assert notifications.reconcile_notification_events('captain', [pr])['events'] == []


@pytest.mark.asyncio
async def test_remote_push_retries_and_deduplicates_by_fingerprint(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    notifications.register_push_token('captain', 'ExponentPushToken[real-device]', 'ios')
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
        @property
        def is_success(self):
            return 200 <= self.status_code < 300
        def json(self):
            return self._payload

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs):
            calls.append(kwargs['json'])
            return Response(503, {}) if len(calls) == 1 else Response(200, {'data': {'status': 'ok'}})

    monkeypatch.setattr(notifications.httpx, 'AsyncClient', Client)
    result = await notifications.dispatch_notification_events('captain', [item()])
    assert result['delivery'] == 'sent'
    assert len(calls) == 2
    assert calls[0]['data']['url'] == '/attention?item=question-1'
    assert (await notifications.dispatch_notification_events('captain', [item()]))['events'] == []
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_quiet_hours_defer_and_revocation_stops_remote_delivery(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    notifications.register_push_token('captain', 'ExponentPushToken[real-device]', 'ios')
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(args)
        return {'status': 'sent'}

    monkeypatch.setattr(notifications, 'send_push_notification', fake_send)
    notifications.update_notification_preferences('captain', True, 22, 7, 'moderate')
    quiet = await notifications.dispatch_notification_events('captain', [item()], local_hour=23)
    assert quiet['events'] == [] and sent == []
    awake = await notifications.dispatch_notification_events('captain', [item()], local_hour=8)
    assert awake['delivery'] == 'sent' and len(sent) == 1
    notifications.revoke_push_token('captain')
    assert notifications.get_registered_push_token('captain') is None
