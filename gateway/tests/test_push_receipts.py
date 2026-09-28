"""Expo transport fixtures; never APNs/FCM/device delivery evidence."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from app import db, notifications
from app.account_lifecycle import delete_account
from app.push_receipts import PushDeliveryStore, RECEIPT_DELAY, RECEIPT_TTL


def event(revision='1'):
    return {'id': 'decision-1', 'revision': revision, 'title': 'private title',
            'subtitle': 'private content', 'requires_action': True,
            'notification_kind': 'captain_question', 'url': '/attention?item=decision-1'}


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    path = str(tmp_path / 'receipts.sqlite3')
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(notifications, 'DB_PATH', path)
    db.init_db()
    clock = [1_800_000_000]
    monkeypatch.setattr('app.push_receipts.time.time', lambda: clock[0])
    notifications.register_push_token('owner-a', 'ExponentPushToken[fixture-a]')
    requests = []
    replies = []

    async def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        status, payload = replies.pop(0)
        return httpx.Response(status, json=payload)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(notifications.httpx, 'AsyncClient',
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    return path, clock, requests, replies


def state(path):
    with sqlite3.connect(path) as connection:
        return connection.execute('SELECT delivered,viewed FROM notification_state WHERE user_id=?', ('owner-a',)).fetchone()


@pytest.mark.asyncio
async def test_ticket_is_not_delivery_restart_and_receipt_do_not_mark_viewed(delivery):
    path, clock, requests, replies = delivery
    replies.append((200, {'data': {'status': 'ok', 'id': 'ticket-one'}}))
    result = await notifications.dispatch_notification_events('owner-a', [event()])
    assert result['delivery'] == 'accepted' and result['events'] and result['unread']
    assert state(path) == (0, 0)
    assert PushDeliveryStore(path).summary('owner-a') == {'pending': 1}
    await notifications.dispatch_notification_events('owner-a', [event()])
    await notifications.reconcile_push_receipts()
    assert len(requests) == 1  # Durable dedupe and delayed receipt polling.
    clock[0] += RECEIPT_DELAY
    replies.append((200, {'data': {'ticket-one': {'status': 'ok'}}}))
    await notifications.reconcile_push_receipts()
    assert PushDeliveryStore(path).summary('owner-a') == {'delivered': 1}
    assert state(path) == (1, 0)
    assert requests[-1] == ('/--/api/v2/push/getReceipts', {'ids': ['ticket-one']})
    await notifications.reconcile_push_receipts()
    assert len(requests) == 2
    assert (await notifications.dispatch_notification_events('owner-a', [event()]))['unread']
    notifications.acknowledge_notification_events('owner-a', ['decision-1'])
    assert state(path) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('replace_token', [False, True])
async def test_invalid_receipt_retires_only_the_exact_registered_token(delivery, replace_token):
    path, clock, requests, replies = delivery
    replies.append((200, {'data': {'status': 'ok', 'id': 'ticket-invalid'}}))
    await notifications.dispatch_notification_events('owner-a', [event()])
    if replace_token:
        notifications.register_push_token('owner-a', 'ExponentPushToken[replacement]')
    clock[0] += RECEIPT_DELAY
    replies.append((200, {'data': {'ticket-invalid': {'status': 'error', 'message': 'private-provider-error',
                                                    'details': {'error': 'DeviceNotRegistered'}}}}))
    await notifications.reconcile_push_receipts()
    assert bool(notifications.get_registered_push_token('owner-a')) is replace_token
    assert state(path) == (0, 0)
    assert PushDeliveryStore(path).summary('owner-a') == {'failed': 1}
    assert 'private-provider-error' not in str(PushDeliveryStore(path).summary('owner-a'))


@pytest.mark.asyncio
async def test_late_receipt_cannot_ack_a_new_fingerprint_or_another_owner(delivery):
    path, clock, requests, replies = delivery
    replies.append((200, {'data': {'status': 'ok', 'id': 'ticket-old'}}))
    await notifications.dispatch_notification_events('owner-a', [event()])
    notifications.reconcile_notification_events('owner-a', [event('2')])
    notifications.reconcile_notification_events('owner-b', [event()])
    clock[0] += RECEIPT_DELAY
    replies.append((200, {'data': {'ticket-old': {'status': 'ok'}, 'unrequested': {'status': 'ok'}}}))
    await notifications.reconcile_push_receipts()
    assert state(path) == (0, 0)
    assert PushDeliveryStore(path).summary('owner-b') == {}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT delivered FROM notification_state WHERE user_id='owner-b'").fetchone() == (0,)


@pytest.mark.asyncio
async def test_outage_missing_and_malformed_receipts_back_off_then_expire_without_resend(delivery):
    path, clock, requests, replies = delivery
    replies.append((200, {'data': {'status': 'ok', 'id': 'ticket-outage'}}))
    await notifications.dispatch_notification_events('owner-a', [event()])
    clock[0] += RECEIPT_DELAY
    for response in ((503, {}), (200, {'data': {}}), (200, {'data': []}),
                     (200, {'data': {'ticket-outage': {'status': []}}})):
        replies.append(response)
        await notifications.reconcile_push_receipts()
        count = len(requests)
        await notifications.reconcile_push_receipts()
        assert len(requests) == count
        assert PushDeliveryStore(path).summary('owner-a') == {'pending': 1}
        clock[0] += 3600
    clock[0] += RECEIPT_TTL
    await notifications.reconcile_push_receipts()
    assert PushDeliveryStore(path).summary('owner-a') == {'expired': 1}
    assert (await notifications.dispatch_notification_events('owner-a', [event()]))['events']
    assert state(path) == (0, 0) and not replies


@pytest.mark.asyncio
async def test_missing_ticket_and_concurrent_dispatch_never_claim_delivery(delivery):
    path, clock, requests, replies = delivery
    replies.append((200, {'data': {'status': 'ok'}}))
    await asyncio.gather(*(notifications.dispatch_notification_events('owner-a', [event()]) for _ in range(2)))
    assert len(requests) == 1
    assert state(path) == (0, 0)
    assert PushDeliveryStore(path).summary('owner-a') == {'failed': 1}


def test_send_lease_recovery_is_bounded_and_receipt_leases_are_exclusive(delivery):
    path, clock, requests, replies = delivery
    store = PushDeliveryStore(path)
    claim = store.claim_send('owner-a', 'decision-1', 'fingerprint', 'token')
    assert claim and not store.claim_send('owner-a', 'decision-1', 'fingerprint', 'token')
    for _ in range(2):
        clock[0] += 121
        claim = PushDeliveryStore(path).claim_send('owner-a', 'decision-1', 'fingerprint', 'token')
        assert claim
    clock[0] += 121
    assert not store.claim_send('owner-a', 'decision-1', 'fingerprint', 'token')
    assert store.summary('owner-a') == {'failed': 1}
    claim = store.claim_send('owner-a', 'decision-2', 'fingerprint', 'token')
    store.accepted(claim, 'ticket-leased')
    clock[0] += RECEIPT_DELAY
    assert len(store.claim_receipts()) == 1
    assert PushDeliveryStore(path).claim_receipts() == []


def test_account_erasure_removes_only_owner_delivery_authority(delivery):
    path, clock, requests, replies = delivery
    store = PushDeliveryStore(path)
    for owner in ('owner-a', 'owner-b'):
        db.update_profile(owner, name=owner)
        store.claim_send(owner, 'decision', 'fingerprint', 'token')
    delete_account('owner-a', confirmation='DELETE owner-a')
    assert store.summary('owner-a') == {}
    assert store.summary('owner-b') == {'sending': 1}
