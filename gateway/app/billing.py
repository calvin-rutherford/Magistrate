"""Truthful Stripe subscription boundary for provider-backed customer accounts.

Checkout/portal URLs are created by Stripe and subscription access changes only
from signed Stripe webhooks. A browser redirect is never treated as payment
proof.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from app import db
from app.persistence import connect

STRIPE_API = "https://api.stripe.com/v1"
MAX_WEBHOOK_BYTES = 256 * 1024
_ACTIVE_STATUSES = frozenset({"active", "trialing"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9_]{4,255}$")


def _settings() -> dict[str, str]:
    names = {
        "secret_key": "MAGISTRATE_STRIPE_SECRET_KEY",
        "webhook_secret": "MAGISTRATE_STRIPE_WEBHOOK_SECRET",
        "price_id": "MAGISTRATE_STRIPE_PRICE_ID",
        "success_url": "MAGISTRATE_BILLING_SUCCESS_URL",
        "cancel_url": "MAGISTRATE_BILLING_CANCEL_URL",
        "portal_return_url": "MAGISTRATE_BILLING_PORTAL_RETURN_URL",
    }
    return {key: os.getenv(name, "").strip() for key, name in names.items()}


def _valid_return_url(value: str, *, production: bool) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    return (
        not parsed.username and not parsed.password and not parsed.fragment
        and bool(parsed.netloc)
        and (parsed.scheme == "https" or (not production and local and parsed.scheme == "http"))
    )


def validate_billing_configuration() -> None:
    """Reject partial or unsafe billing configuration during startup."""
    settings = _settings()
    supplied = [bool(value) for value in settings.values()]
    if not any(supplied):
        return
    if not all(supplied):
        raise RuntimeError("Stripe billing configuration is incomplete")
    production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {
        "dev", "development", "test", "testing",
    }
    key_prefix = "sk_live_" if production else "sk_"
    if (
        not settings["secret_key"].startswith(key_prefix)
        or not settings["webhook_secret"].startswith("whsec_")
        or not settings["price_id"].startswith("price_")
        or not all(_valid_return_url(settings[key], production=production) for key in (
            "success_url", "cancel_url", "portal_return_url",
        ))
    ):
        raise RuntimeError("Stripe billing configuration is invalid")


def billing_available() -> bool:
    settings = _settings()
    return all(settings.values())


def _billing_row(user_id: str) -> sqlite3.Row | None:
    db.init_db()
    with connect(db.DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM billing_accounts WHERE owner_user_id = ?", (user_id,),
        ).fetchone()


def subscription_active(user_id: str) -> bool:
    row = _billing_row(user_id)
    return bool(row and row["status"] in _ACTIVE_STATUSES)


def billing_status(user_id: str) -> dict[str, Any]:
    row = _billing_row(user_id)
    status = str(row["status"]) if row else "none"
    return {
        "schema_version": "billing-status.v1",
        "provider": "stripe" if billing_available() else None,
        "available": billing_available(),
        "status": status,
        "active": status in _ACTIVE_STATUSES,
        "current_period_end": int(row["current_period_end"]) if row and row["current_period_end"] is not None else None,
        "customer_portal_available": bool(billing_available() and row and row["external_customer_ref"]),
    }


async def _stripe_post(path: str, fields: list[tuple[str, str]]) -> dict[str, Any]:
    settings = _settings()
    if not billing_available():
        raise HTTPException(status_code=503, detail="Billing is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                f"{STRIPE_API}/{path}", data=fields,
                headers={
                    "Authorization": f"Bearer {settings['secret_key']}",
                    "Idempotency-Key": secrets.token_hex(24),
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Billing provider is unavailable.") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code < 200 or response.status_code >= 300 or not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail="Billing provider could not create this session.")
    return payload


async def create_checkout(user_id: str, email: str | None) -> dict[str, str]:
    settings = _settings()
    row = _billing_row(user_id)
    fields = [
        ("mode", "subscription"),
        ("line_items[0][price]", settings.get("price_id", "")),
        ("line_items[0][quantity]", "1"),
        ("success_url", settings.get("success_url", "")),
        ("cancel_url", settings.get("cancel_url", "")),
        ("client_reference_id", user_id),
        ("metadata[magistrate_user_id]", user_id),
        ("subscription_data[metadata][magistrate_user_id]", user_id),
        ("allow_promotion_codes", "true"),
    ]
    if row and row["external_customer_ref"]:
        fields.append(("customer", str(row["external_customer_ref"])))
    elif email and "@" in email and len(email) <= 320:
        fields.append(("customer_email", email))
    payload = await _stripe_post("checkout/sessions", fields)
    session_id, url = payload.get("id"), payload.get("url")
    if not isinstance(session_id, str) or not _SAFE_ID.fullmatch(session_id) or not isinstance(url, str):
        raise HTTPException(status_code=503, detail="Billing provider returned an invalid checkout session.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=503, detail="Billing provider returned an invalid checkout URL.")
    return {"schema_version": "billing-checkout.v1", "checkout_session_id": session_id, "url": url}


async def create_portal(user_id: str) -> dict[str, str]:
    settings = _settings()
    row = _billing_row(user_id)
    if not row or not row["external_customer_ref"]:
        raise HTTPException(status_code=409, detail="No billing customer exists for this account.")
    payload = await _stripe_post("billing_portal/sessions", [
        ("customer", str(row["external_customer_ref"])),
        ("return_url", settings.get("portal_return_url", "")),
    ])
    url = payload.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
        raise HTTPException(status_code=503, detail="Billing provider returned an invalid portal URL.")
    return {"schema_version": "billing-portal.v1", "url": url}


def _stripe_signature(raw: bytes, signature_header: str, now: int) -> None:
    settings = _settings()
    if not billing_available():
        raise HTTPException(status_code=503, detail="Billing is not configured.")
    values: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values.get("t", [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid billing webhook signature.") from exc
    if abs(now - timestamp) > 300:
        raise HTTPException(status_code=400, detail="Expired billing webhook signature.")
    expected = hmac.new(
        settings["webhook_secret"].encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in values.get("v1", [])):
        raise HTTPException(status_code=400, detail="Invalid billing webhook signature.")


def accept_webhook(raw: bytes, signature_header: str, *, now: int | None = None) -> dict[str, Any]:
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Billing webhook is too large.")
    current = int(time.time() if now is None else now)
    _stripe_signature(raw, signature_header, current)
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid billing webhook.") from exc
    event_id = event.get("id") if isinstance(event, dict) else None
    event_type = event.get("type") if isinstance(event, dict) else None
    event_created = event.get("created") if isinstance(event, dict) else None
    data_container = event.get("data") if isinstance(event, dict) else None
    data = data_container.get("object") if isinstance(data_container, dict) else None
    if not isinstance(event_id, str) or not _SAFE_ID.fullmatch(event_id) or not isinstance(event_type, str) or type(event_created) is not int or event_created <= 0 or not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid billing webhook.")

    db.init_db()
    with connect(db.DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM billing_webhook_events WHERE event_id = ?", (event_id,)).fetchone():
            return {"status": "accepted", "duplicate": True}

        user_id: str | None = None
        customer = data.get("customer")
        subscription = data.get("subscription")
        status: str | None = None
        period_end: int | None = None
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        candidate = metadata.get("magistrate_user_id") or data.get("client_reference_id")
        if isinstance(candidate, str):
            user_id = candidate
        if event_type.startswith("customer.subscription."):
            subscription = data.get("id")
            status = data.get("status") if isinstance(data.get("status"), str) else "none"
            period_end = data.get("current_period_end") if type(data.get("current_period_end")) is int else None
        elif event_type == "checkout.session.completed":
            # Checkout completion records identifiers only. Entitlement remains
            # pending until a signed subscription event reports active/trialing.
            status = "pending"
        if not user_id and isinstance(customer, str):
            row = connection.execute(
                "SELECT owner_user_id FROM billing_accounts WHERE external_customer_ref = ?", (customer,),
            ).fetchone()
            user_id = str(row[0]) if row else None
        if user_id and connection.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone():
            existing = connection.execute(
                "SELECT status, provider_event_created FROM billing_accounts WHERE owner_user_id = ?",
                (user_id,),
            ).fetchone()
            subscription_event = event_type.startswith("customer.subscription.")
            checkout_event = event_type == "checkout.session.completed"
            should_apply = (
                subscription_event and (
                    existing is None or existing["provider_event_created"] is None
                    or event_created >= int(existing["provider_event_created"])
                )
            ) or (
                checkout_event and (existing is None or existing["status"] in {"none", "pending"})
            )
            if should_apply:
                connection.execute(
                    """INSERT INTO billing_accounts
                       (owner_user_id, provider, external_customer_ref, subscription_id, status,
                        current_period_end, provider_event_created, created_at, updated_at)
                       VALUES(?,'stripe',?,?,?,?,?,?,?)
                       ON CONFLICT(owner_user_id) DO UPDATE SET
                         provider='stripe',
                         external_customer_ref=COALESCE(excluded.external_customer_ref, billing_accounts.external_customer_ref),
                         subscription_id=COALESCE(excluded.subscription_id, billing_accounts.subscription_id),
                         status=excluded.status,
                         current_period_end=excluded.current_period_end,
                         provider_event_created=CASE
                           WHEN COALESCE(billing_accounts.provider_event_created, 0) > excluded.provider_event_created
                           THEN billing_accounts.provider_event_created
                           ELSE excluded.provider_event_created
                         END,
                         updated_at=excluded.updated_at""",
                    (
                        user_id, customer if isinstance(customer, str) else None,
                        subscription if isinstance(subscription, str) else None,
                        status or "none", period_end, event_created, current, current,
                    ),
                )
        connection.execute(
            "INSERT INTO billing_webhook_events(event_id, event_type, received_at) VALUES(?,?,?)",
            (event_id, event_type, current),
        )
    return {"status": "accepted", "duplicate": False}
