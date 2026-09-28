import hashlib
import hmac
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.billing import (
    BillingCatalog, BillingError, BillingService, CreditLedger, InsufficientCredits,
    StripeWebhookProcessor, load_catalog,
)
from app.main import app
from conftest import TEST_HEADERS


def _ensure_user(user_id: str = 'captain') -> None:
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT OR IGNORE INTO user_profiles
               (user_id,name,email,created_at,updated_at) VALUES (?, '', '', 1, 1)""",
            (user_id,),
        )


def _signature(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b'.' + payload, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


def _event(event_id: str, event_type: str, obj: dict, *, created: int = 1_735_689_600) -> bytes:
    return json.dumps({
        'id': event_id, 'type': event_type, 'created': created,
        'data': {'object': obj},
    }, sort_keys=True, separators=(',', ':')).encode()


def test_credit_reservation_is_atomic_concurrent_and_measured(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'billing.sqlite3'))
    _ensure_user()
    ledger = CreditLedger()
    initial = ledger.summary('captain', now=1_735_689_600)
    assert initial['balance_microcredits'] == 500_000_000
    assert initial['low_credit_warning'] is False

    reservation = ledger.reserve_objective(
        'captain', 'objective-a', idempotency_key='objective:objective-a', now=1_735_689_601,
    )
    assert reservation['estimated_microcredits'] == 100_000_000
    replay = ledger.reserve_objective(
        'captain', 'objective-a', idempotency_key='objective:objective-a', now=1_735_689_602,
    )
    assert replay['reservation_id'] == reservation['reservation_id']
    with pytest.raises(BillingError) as concurrency:
        ledger.reserve_objective(
            'captain', 'objective-b', idempotency_key='objective:objective-b', now=1_735_689_603,
        )
    assert concurrency.value.code == 'concurrency_limit'

    settled = ledger.settle_objective('captain', 'objective-a', {
        'provider': 'openai', 'model': 'gpt-4o-mini',
        'input_tokens': 1_000_000, 'output_tokens': 0, 'compute_milliseconds': 0,
    }, event_id='event-complete-a', now=1_735_689_604)
    assert settled['actual_microcredits'] == 18_750_000
    replay = ledger.settle_objective('captain', 'objective-a', {
        'provider': 'openai', 'model': 'gpt-4o-mini',
        'input_tokens': 1_000_000, 'output_tokens': 0, 'compute_milliseconds': 0,
    }, event_id='event-complete-a', now=1_735_689_604)
    assert replay['actual_microcredits'] == settled['actual_microcredits']
    with pytest.raises(BillingError) as settlement_conflict:
        ledger.settle_objective('captain', 'objective-a', {
            'provider': 'openai', 'model': 'gpt-4o-mini',
            'input_tokens': 1_000_000, 'output_tokens': 0, 'compute_milliseconds': 0,
        }, event_id='different-event', now=1_735_689_604)
    assert settlement_conflict.value.code == 'settlement_conflict'
    summary = ledger.summary('captain', now=1_735_689_605)
    assert summary['balance_microcredits'] == 481_250_000
    assert summary['reserved_microcredits'] == 0
    assert summary['period_spend_microcredits'] == 18_750_000
    assert summary['usage'] == [{
        'usage_id': summary['usage'][0]['usage_id'], 'event_id': 'event-complete-a',
        'objective_id': 'objective-a', 'provider': 'openai', 'model': 'gpt-4o-mini',
        'input_tokens': 1_000_000, 'output_tokens': 0, 'compute_milliseconds': 0,
        'cost_microcredits': 18_750_000, 'created_at': 1_735_689_604,
    }]
    assert [entry['type'] for entry in summary['ledger'][:3]] == [
        'settlement', 'reservation', 'included_grant',
    ]


def test_monthly_hard_spend_limit_blocks_before_reservation(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'spend-limit.sqlite3'))
    _ensure_user()
    payload = json.loads((__import__('pathlib').Path(__file__).parents[1] / 'billing_catalog.json').read_text())
    free = next(plan for plan in payload['plans'] if plan['id'] == 'free')
    free.update(concurrency_limit=2, objective_reserve_credits=60, monthly_spend_limit_credits=100)
    catalog = BillingCatalog(payload)
    ledger = CreditLedger(lambda: catalog)
    ledger.reserve_objective('captain', 'first', idempotency_key='objective:first', now=1_735_689_600)
    with pytest.raises(BillingError) as limited:
        ledger.reserve_objective('captain', 'second', idempotency_key='objective:second', now=1_735_689_601)
    assert limited.value.code == 'spend_limit'
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute("SELECT COUNT(*) FROM credit_reservations").fetchone()[0] == 1


def test_insufficient_credit_starts_no_reservation_and_release_refund_are_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'limits.sqlite3'))
    _ensure_user()
    ledger = CreditLedger()
    ledger.summary('captain', now=1_735_689_600)
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute("UPDATE billing_accounts SET available_microcredits = 1 WHERE owner_user_id = 'captain'")
    with pytest.raises(InsufficientCredits):
        ledger.reserve_objective('captain', 'no-funds', idempotency_key='objective:no-funds', now=1_735_689_601)
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute("SELECT COUNT(*) FROM credit_reservations").fetchone()[0] == 0

    ledger.add_credits('captain', 200, idempotency_key='manual:test', source='test', now=1_735_689_602)
    assert not ledger.add_credits('captain', 200, idempotency_key='manual:test', source='test', now=1_735_689_602)
    with pytest.raises(BillingError) as grant_conflict:
        ledger.add_credits('captain', 201, idempotency_key='manual:test', source='test', now=1_735_689_602)
    assert grant_conflict.value.code == 'idempotency_conflict'
    ledger.reserve_objective('captain', 'released', idempotency_key='objective:released', now=1_735_689_603)
    assert ledger.release_objective('captain', 'released', reason='dispatch-failed', now=1_735_689_604)
    assert not ledger.release_objective('captain', 'released', reason='replay', now=1_735_689_605)

    ledger.reserve_objective('captain', 'settled', idempotency_key='objective:settled', now=1_735_689_606)
    ledger.settle_objective('captain', 'settled', {
        'provider': 'openai', 'model': 'gpt-4o-mini',
        'input_tokens': 100_000, 'output_tokens': 0, 'compute_milliseconds': 0,
    }, event_id='settled-event', now=1_735_689_607)
    assert ledger.refund_objective('captain', 'settled', 1_000_000, idempotency_key='refund:one', reason='provider adjustment', now=1_735_689_608)
    assert not ledger.refund_objective('captain', 'settled', 1_000_000, idempotency_key='refund:one', reason='replay', now=1_735_689_609)
    with pytest.raises(BillingError) as refund_conflict:
        ledger.refund_objective('captain', 'settled', 1_500_000, idempotency_key='refund:one', reason='changed', now=1_735_689_609)
    assert refund_conflict.value.code == 'idempotency_conflict'


def test_signed_credit_pack_webhook_is_authoritative_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'webhook.sqlite3'))
    _ensure_user()
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test_secret')
    ledger = CreditLedger()
    ledger.summary('captain', now=1_735_689_600)
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO billing_checkout_sessions
               (stripe_session_id, owner_user_id, catalog_id, kind, idempotency_key, created_at)
               VALUES ('cs_pack', 'captain', 'credits-10k', 'credit_pack', 'checkout-one', ?)""",
            (1_735_689_600,),
        )
    processor = StripeWebhookProcessor(ledger=ledger)
    payload = _event('evt_pack', 'checkout.session.completed', {
        'id': 'cs_pack', 'mode': 'payment', 'payment_status': 'paid', 'customer': 'cus_captain',
    })
    signature = _signature(payload, 'whsec_test_secret', 1_735_689_610)
    assert processor.process(payload, signature, now=1_735_689_610)['status'] == 'processed'
    assert processor.process(payload, signature, now=1_735_689_610)['status'] == 'duplicate'
    assert ledger.summary('captain', now=1_735_689_611)['balance_microcredits'] == 10_500_000_000

    changed = _event('evt_pack', 'checkout.session.completed', {
        'id': 'cs_pack', 'mode': 'payment', 'payment_status': 'unpaid',
    })
    with pytest.raises(BillingError) as conflict:
        processor.process(changed, _signature(changed, 'whsec_test_secret', 1_735_689_610), now=1_735_689_610)
    assert conflict.value.code == 'webhook_conflict'
    with pytest.raises(BillingError) as unsigned:
        processor.process(payload, 't=1735689610,v1=wrong', now=1_735_689_610)
    assert unsigned.value.code == 'webhook_signature_invalid'


def _activated_catalog() -> BillingCatalog:
    payload = json.loads((__import__('pathlib').Path(__file__).parents[1] / 'billing_catalog.json').read_text())
    for plan in payload['plans']:
        if plan['id'] == 'individual':
            plan['stripe_price_id'] = 'price_individual_real'
    return BillingCatalog(payload)


def test_subscription_renewal_failure_grace_and_cancel(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'subscription.sqlite3'))
    _ensure_user()
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_test_secret')
    catalog = _activated_catalog()
    ledger = CreditLedger(lambda: catalog)
    ledger.summary('captain', now=1_735_689_600)
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            "UPDATE billing_accounts SET external_customer_ref = 'cus_captain' WHERE owner_user_id = 'captain'"
        )
    processor = StripeWebhookProcessor(lambda: catalog, ledger)

    subscription = {
        'id': 'sub_captain', 'customer': 'cus_captain', 'status': 'active',
        'current_period_start': 1_735_689_600, 'current_period_end': 1_738_368_000,
        'cancel_at_period_end': False,
        'items': {'data': [{'price': {'id': 'price_individual_real'}}]},
        'metadata': {'magistrate_owner': 'captain', 'catalog_id': 'individual'},
    }
    created = _event(
        'evt_sub', 'customer.subscription.created', subscription, created=1_735_689_610,
    )
    processor.process(created, _signature(created, 'whsec_test_secret', 1_735_689_610), now=1_735_689_610)
    invoice = _event('evt_invoice', 'invoice.paid', {
        'id': 'in_paid', 'subscription': 'sub_captain',
        'period_start': 1_735_689_600, 'period_end': 1_738_368_000,
    }, created=1_735_689_611)
    processor.process(invoice, _signature(invoice, 'whsec_test_secret', 1_735_689_611), now=1_735_689_611)
    active = ledger.summary('captain', now=1_735_689_612)
    assert active['catalog_id'] == 'individual'
    assert active['balance_microcredits'] == 10_500_000_000
    proration = _event('evt_proration', 'invoice.paid', {
        'id': 'in_proration', 'subscription': 'sub_captain',
        'period_start': 1_735_689_600, 'period_end': 1_738_368_000,
    }, created=1_735_689_612)
    processor.process(proration, _signature(proration, 'whsec_test_secret', 1_735_689_612), now=1_735_689_612)
    assert ledger.summary('captain', now=1_735_689_612)['balance_microcredits'] == 10_500_000_000

    failed = _event(
        'evt_failed', 'invoice.payment_failed',
        {'id': 'in_failed', 'subscription': 'sub_captain'}, created=1_735_689_613,
    )
    processor.process(failed, _signature(failed, 'whsec_test_secret', 1_735_689_613), now=1_735_689_613)
    grace = ledger.summary('captain', now=1_735_689_614)
    assert grace['subscription_status'] == 'past_due'
    assert grace['entitlements']['execution'] is True
    retry = _event(
        'evt_failed_retry', 'invoice.payment_failed',
        {'id': 'in_failed_retry', 'subscription': 'sub_captain'}, created=1_735_689_614,
    )
    processor.process(retry, _signature(retry, 'whsec_test_secret', 1_735_700_000), now=1_735_700_000)
    assert ledger.summary('captain', now=1_735_700_001)['grace_ends_at'] == grace['grace_ends_at']
    assert ledger.summary('captain', now=grace['grace_ends_at'] + 1)['entitlements']['execution'] is False

    deleted = _event(
        'evt_deleted', 'customer.subscription.deleted', subscription, created=1_735_689_615,
    )
    processor.process(deleted, _signature(deleted, 'whsec_test_secret', 1_735_689_615), now=1_735_689_615)
    cancelled = ledger.summary('captain', now=1_735_689_616)
    assert cancelled['catalog_id'] == 'free'
    assert cancelled['subscription_status'] == 'active'
    stale = _event(
        'evt_stale_subscription', 'customer.subscription.updated', subscription,
        created=1_735_689_614,
    )
    processor.process(stale, _signature(stale, 'whsec_test_secret', 1_735_689_616), now=1_735_689_616)
    assert ledger.summary('captain', now=1_735_689_617)['catalog_id'] == 'free'


@pytest.mark.asyncio
async def test_customer_checkout_and_portal_are_server_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'checkout.sqlite3'))
    _ensure_user()
    monkeypatch.setenv('MAGISTRATE_BILLING_RETURN_ORIGINS', 'https://app.example.invalid,magistrate://chat')
    catalog = _activated_catalog()
    calls = []

    class FakeStripe:
        async def post(self, path, data, *, idempotency_key):
            calls.append((path, dict(data), idempotency_key))
            if path == '/customers':
                return {'id': 'cus_server_bound'}
            if path == '/checkout/sessions':
                return {'id': 'cs_server_bound', 'url': 'https://checkout.stripe.test/session'}
            if path == '/billing_portal/sessions':
                return {'url': 'https://billing.stripe.test/portal'}
            raise AssertionError(path)

    service = BillingService(
        stripe_factory=lambda: FakeStripe(), catalog_loader=lambda: catalog,
        ledger=CreditLedger(lambda: catalog),
    )
    checkout = await service.checkout(
        'captain', 'individual', 'https://app.example.invalid/chat', 'checkout-owner-one',
    )
    assert checkout['session_id'] == 'cs_server_bound'
    checkout_call = next(call for call in calls if call[0] == '/checkout/sessions')
    assert checkout_call[1]['customer'] == 'cus_server_bound'
    assert checkout_call[1]['metadata[magistrate_owner]'] == 'captain'
    assert checkout_call[1]['line_items[0][price]'] == 'price_individual_real'
    portal = await service.portal(
        'captain', 'magistrate://chat', 'portal-owner-one',
    )
    assert portal['portal_url'].startswith('https://billing.stripe.test/')
    assert [path for path, _, _ in calls].count('/customers') == 1
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute(
            "SELECT external_customer_ref FROM billing_accounts WHERE owner_user_id = 'captain'"
        ).fetchone()[0] == 'cus_server_bound'
    with pytest.raises(BillingError) as unsafe:
        await service.portal('captain', 'https://evil.example/chat', 'portal-owner-two')
    assert unsafe.value.code == 'return_url_invalid'


def test_billing_routes_are_owner_authorized_and_truthful_without_prices():
    client = TestClient(app)
    assert client.get('/api/v1/billing/account').status_code == 401
    assert client.post('/api/v1/billing/checkout', json={}).status_code == 401
    assert client.post('/api/v1/billing/portal', json={}).status_code == 401
    account = client.get('/api/v1/billing/account', headers=TEST_HEADERS)
    assert account.status_code == 200
    assert account.json()['schema_version'] == 'magistrate.billing-account.v1'
    catalog = client.get('/api/v1/billing/catalog', headers=TEST_HEADERS).json()
    assert catalog['activation'] == 'blocked_external'
    assert all(not item['checkout_available'] for item in catalog['plans'] if item['kind'] == 'subscription')
    blocked = client.post('/api/v1/billing/checkout', headers=TEST_HEADERS, json={
        'catalog_id': 'individual', 'return_url': 'https://app.example.invalid/chat',
        'idempotency_key': 'checkout-test-one',
    })
    assert blocked.status_code == 503
    assert blocked.json()['detail']['code'] == 'price_not_activated'
