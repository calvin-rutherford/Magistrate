import hashlib
import hmac
import json
import sqlite3
import time

import pytest

from app import db
from app.billing import accept_webhook, billing_status, validate_billing_configuration
from app.onboarding import acknowledge_welcome, ensure_provider_onboarding, onboarding_state


def _configure(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_STRIPE_SECRET_KEY", "sk_test_repository")
    monkeypatch.setenv("MAGISTRATE_STRIPE_WEBHOOK_SECRET", "whsec_repository_test")
    monkeypatch.setenv("MAGISTRATE_STRIPE_PRICE_ID", "price_repository")
    monkeypatch.setenv("MAGISTRATE_BILLING_SUCCESS_URL", "https://app.example.test/?billing=success")
    monkeypatch.setenv("MAGISTRATE_BILLING_CANCEL_URL", "https://app.example.test/?billing=cancel")
    monkeypatch.setenv("MAGISTRATE_BILLING_PORTAL_RETURN_URL", "https://app.example.test/account")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "repository-client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "repository-secret")
    monkeypatch.setenv("MAGISTRATE_OAUTH_CALLBACK_BASE_URL", "https://gateway.example.test")


def _sign(raw: bytes, timestamp: int) -> str:
    signature = hmac.new(
        b"whsec_repository_test", str(timestamp).encode() + b"." + raw, hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def test_billing_migration_extends_tenant_schema_and_backfills_login(tmp_path):
    connection = sqlite3.connect(tmp_path / "provider-onboarding-migration.sqlite3")
    connection.execute("CREATE TABLE user_profiles(user_id TEXT PRIMARY KEY)")
    connection.execute(
        """CREATE TABLE connected_accounts(
             id TEXT PRIMARY KEY, user_id TEXT NOT NULL, account_kind TEXT NOT NULL,
             status TEXT NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE billing_accounts(
             owner_user_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
             external_customer_ref TEXT, status TEXT NOT NULL,
             created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
           )"""
    )
    connection.execute("INSERT INTO user_profiles(user_id) VALUES('existing-provider-owner')")
    connection.execute(
        """INSERT INTO connected_accounts(id,user_id,account_kind,status)
           VALUES('existing-login','existing-provider-owner','login','connected')"""
    )

    db.apply_schema_migrations(connection, ((
        3, "provider-onboarding-and-billing", db._migration_provider_onboarding_and_billing,
    ),))

    assert connection.execute(
        "SELECT welcome_completed_at FROM account_onboarding WHERE user_id='existing-provider-owner'"
    ).fetchone() == (None,)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(billing_accounts)")}
    assert {"subscription_id", "current_period_end", "provider_event_created"} <= columns
    assert connection.execute("SELECT version FROM schema_migrations WHERE version=3").fetchone() == (3,)
    connection.close()


def test_credit_billing_migration_evolves_the_canonical_account_and_webhook_tables(tmp_path):
    connection = sqlite3.connect(tmp_path / "credit-billing-migration.sqlite3")
    connection.execute(
        """CREATE TABLE billing_accounts(
             owner_user_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
             external_customer_ref TEXT, status TEXT NOT NULL,
             created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
             subscription_id TEXT, current_period_end INTEGER, provider_event_created INTEGER
           )"""
    )
    connection.execute(
        """CREATE TABLE billing_webhook_events(
             event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, received_at INTEGER NOT NULL
           )"""
    )
    connection.execute(
        """INSERT INTO billing_accounts
           (owner_user_id,provider,status,created_at,updated_at)
           VALUES('existing-owner','stripe','active',1,1)"""
    )

    db.apply_schema_migrations(connection, ((
        5, "credit-billing-ledgers", db._migration_credit_billing,
    ),))

    account = connection.execute(
        "SELECT catalog_id, available_microcredits, reserved_microcredits FROM billing_accounts"
    ).fetchone()
    assert account == ("free", 0, 0)
    webhook_columns = {row[1] for row in connection.execute("PRAGMA table_info(billing_webhook_events)")}
    assert {"payload_sha256", "processed_at"} <= webhook_columns
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"credit_ledger", "credit_reservations", "execution_usage_ledger", "billing_checkout_sessions"} <= tables
    assert "billing_customers" not in tables
    connection.close()


def test_partial_billing_configuration_refuses_startup(monkeypatch):
    for name in (
        "MAGISTRATE_STRIPE_SECRET_KEY", "MAGISTRATE_STRIPE_WEBHOOK_SECRET",
        "MAGISTRATE_STRIPE_PRICE_ID", "MAGISTRATE_BILLING_SUCCESS_URL",
        "MAGISTRATE_BILLING_CANCEL_URL", "MAGISTRATE_BILLING_PORTAL_RETURN_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGISTRATE_STRIPE_PRICE_ID", "price_incomplete")
    with pytest.raises(RuntimeError, match="incomplete"):
        validate_billing_configuration()


def test_provider_onboarding_resumes_from_truthful_github_and_billing_state(monkeypatch):
    _configure(monkeypatch)
    validate_billing_configuration()
    now = int(time.time())
    user_id = "billing-onboarding-user"
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """INSERT INTO user_profiles
               (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
               VALUES(?, '', 'person@example.test', '', '', 'dusk-mountain', ?, ?)""",
            (user_id, now, now),
        )
        ensure_provider_onboarding(connection, user_id, now)

    state = onboarding_state(user_id)
    assert state["next_step"] == "welcome"
    acknowledge_welcome(user_id)
    assert onboarding_state(user_id)["next_step"] == "profile"

    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute("UPDATE user_profiles SET name = 'Ada' WHERE user_id = ?", (user_id,))
    assert onboarding_state(user_id)["next_step"] == "github"

    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO connected_accounts
               (id, user_id, provider, provider_user_id, provider_username, status,
                scopes, created_at, updated_at, account_kind)
               VALUES('billing-github', ?, 'github', '42', 'ada', 'connected', 'repo', ?, ?, 'oauth')""",
            (user_id, now, now),
        )
        connection.execute(
            """INSERT INTO oauth_credentials
               (id, connected_account_id, access_token_enc, refresh_token_enc, expires_at)
               VALUES('billing-github-credential', 'billing-github', 'encrypted', NULL, NULL)""",
        )
    assert onboarding_state(user_id)["next_step"] == "billing"

    event = {
        "id": "evt_repository_active",
        "created": now,
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_repository", "customer": "cus_repository", "status": "active",
            "current_period_end": now + 3600,
            "metadata": {"magistrate_user_id": user_id},
        }},
    }
    raw = json.dumps(event, separators=(",", ":")).encode()
    assert accept_webhook(raw, _sign(raw, now), now=now) == {"status": "accepted", "duplicate": False}
    assert accept_webhook(raw, _sign(raw, now), now=now) == {"status": "accepted", "duplicate": True}
    assert onboarding_state(user_id)["required"] is False
    assert billing_status(user_id)["active"] is True

    # Stripe does not guarantee webhook delivery order. A late checkout event
    # must not regress the already-authoritative active subscription to pending.
    late_checkout = {
        "id": "evt_repository_late_checkout", "created": now + 1,
        "type": "checkout.session.completed", "data": {"object": {
            "id": "cs_repository_late", "customer": "cus_repository",
            "subscription": "sub_repository", "client_reference_id": user_id,
            "metadata": {"magistrate_user_id": user_id},
        }},
    }
    late_raw = json.dumps(late_checkout, separators=(",", ":")).encode()
    accept_webhook(late_raw, _sign(late_raw, now), now=now)
    assert billing_status(user_id)["status"] == "active"


def test_checkout_redirect_does_not_grant_subscription(monkeypatch):
    _configure(monkeypatch)
    now = int(time.time())
    user_id = "billing-pending-user"
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO user_profiles
               (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
               VALUES(?, 'Grace', '', '', '', 'dusk-mountain', ?, ?)""",
            (user_id, now, now),
        )
    event = {
        "id": "evt_repository_checkout",
        "created": now,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_repository", "customer": "cus_pending", "subscription": "sub_pending",
            "client_reference_id": user_id, "metadata": {"magistrate_user_id": user_id},
        }},
    }
    raw = json.dumps(event, separators=(",", ":")).encode()
    accept_webhook(raw, _sign(raw, now), now=now)
    assert billing_status(user_id)["status"] == "pending"
    assert billing_status(user_id)["active"] is False
