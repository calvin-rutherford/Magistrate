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
