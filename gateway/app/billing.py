"""Stripe-backed plans and an integer, auditable Magistrate credit ledger.

Stripe is the payment authority; this module is the execution authority.  A
checkout redirect never grants service. Only verified, idempotently consumed
webhooks change subscriptions or add purchased credits.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app import db
from app.persistence import connect

CATALOG_SCHEMA = "magistrate.billing-catalog.v1"
BILLING_SCHEMA = "magistrate.billing-account.v1"
MICROCREDITS_PER_CREDIT = 1_000_000
MAX_WEBHOOK_BYTES = 256 * 1024
_STRIPE_ID = re.compile(r"^(?:cus|price|sub|cs)_[A-Za-z0-9_]+$")


class BillingError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class InsufficientCredits(BillingError):
    def __init__(self, message: str = "Insufficient Magistrate credits for this execution."):
        super().__init__("insufficient_credits", message, status_code=402)


class BillingCatalog:
    def __init__(self, payload: dict[str, Any]):
        if payload.get("schema_version") != CATALOG_SCHEMA or payload.get("currency") != "usd":
            raise BillingError("catalog_invalid", "Billing catalog schema or currency is invalid.", status_code=503)
        unit = payload.get("credit_unit")
        if not isinstance(unit, dict) or unit.get("microcredits_per_credit") != MICROCREDITS_PER_CREDIT:
            raise BillingError("catalog_invalid", "Billing credit precision is invalid.", status_code=503)
        usd = unit.get("usd_micros_per_credit")
        if type(usd) is not int or usd <= 0:
            raise BillingError("catalog_invalid", "Billing credit value is invalid.", status_code=503)
        self.payload = payload
        self.usd_micros_per_credit = usd
        self.plans = self._indexed("plans", "plan")
        self.packs = self._indexed("credit_packs", "credit pack")
        default = payload.get("default_plan")
        if default not in self.plans or self.plans[default].get("kind") not in {"free", "demo"}:
            raise BillingError("catalog_invalid", "Billing default plan is invalid.", status_code=503)
        self.default_plan = default
        for plan in self.plans.values():
            self._validate_plan(plan)
        for pack in self.packs.values():
            self._validate_pack(pack)
        rates = payload.get("rates")
        if not isinstance(rates, list):
            raise BillingError("catalog_invalid", "Billing rates are invalid.", status_code=503)
        self.rates: dict[tuple[str, str], dict[str, Any]] = {}
        for rate in rates:
            if not isinstance(rate, dict) or not isinstance(rate.get("provider"), str) or not isinstance(rate.get("model"), str):
                raise BillingError("catalog_invalid", "A billing rate is invalid.", status_code=503)
            key = (rate["provider"], rate["model"])
            if key in self.rates:
                raise BillingError("catalog_invalid", "Billing rates are duplicated.", status_code=503)
            for field in ("input_usd_micros_per_million", "output_usd_micros_per_million", "compute_usd_micros_per_hour", "margin_bps"):
                if type(rate.get(field)) is not int or rate[field] < 0:
                    raise BillingError("catalog_invalid", f"Billing rate {field} is invalid.", status_code=503)
            self.rates[key] = rate

    def _indexed(self, field: str, label: str) -> dict[str, dict[str, Any]]:
        values = self.payload.get(field)
        if not isinstance(values, list):
            raise BillingError("catalog_invalid", f"Billing {field} are invalid.", status_code=503)
        indexed: dict[str, dict[str, Any]] = {}
        for value in values:
            if not isinstance(value, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(value.get("id", ""))):
                raise BillingError("catalog_invalid", f"A billing {label} is invalid.", status_code=503)
            if value["id"] in indexed:
                raise BillingError("catalog_invalid", f"Billing {label} ids are duplicated.", status_code=503)
            indexed[value["id"]] = value
        return indexed

    @staticmethod
    def _price(value: Any) -> None:
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"price_[A-Za-z0-9_]+", value)):
            raise BillingError("catalog_invalid", "A Stripe price id is invalid.", status_code=503)

    def _validate_plan(self, plan: dict[str, Any]) -> None:
        if plan.get("kind") not in {"free", "demo", "subscription"} or not isinstance(plan.get("name"), str):
            raise BillingError("catalog_invalid", "A billing plan is invalid.", status_code=503)
        self._price(plan.get("stripe_price_id"))
        for field in ("monthly_included_credits", "objective_reserve_credits", "monthly_spend_limit_credits", "concurrency_limit", "low_credit_warning_credits", "grace_days"):
            if type(plan.get(field)) is not int or plan[field] < 0:
                raise BillingError("catalog_invalid", f"Plan {field} is invalid.", status_code=503)
        entitlements = plan.get("entitlements")
        if (
            plan["concurrency_limit"] < 1
            or plan["monthly_spend_limit_credits"] < plan["objective_reserve_credits"]
            or not isinstance(entitlements, list)
            or any(not isinstance(item, str) or not item for item in entitlements)
            or len(set(entitlements)) != len(entitlements)
        ):
            raise BillingError("catalog_invalid", "Plan entitlements or limits are invalid.", status_code=503)
        if plan["kind"] != "subscription" and plan.get("stripe_price_id") is not None:
            raise BillingError("catalog_invalid", "Non-paid plans cannot have Stripe prices.", status_code=503)

    def _validate_pack(self, pack: dict[str, Any]) -> None:
        self._price(pack.get("stripe_price_id"))
        if not isinstance(pack.get("name"), str) or type(pack.get("credits")) is not int or pack["credits"] <= 0:
            raise BillingError("catalog_invalid", "A credit pack is invalid.", status_code=503)

    def price_item(self, price_id: str) -> tuple[str, dict[str, Any]] | None:
        for plan in self.plans.values():
            if plan.get("stripe_price_id") == price_id:
                return "subscription", plan
        for pack in self.packs.values():
            if pack.get("stripe_price_id") == price_id:
                return "credit_pack", pack
        return None

    def public(self) -> dict[str, Any]:
        def item(value: dict[str, Any]) -> dict[str, Any]:
            return {key: value[key] for key in value if key != "stripe_price_id"} | {
                "checkout_available": bool(value.get("stripe_price_id") and stripe_configured())
            }
        return {
            "schema_version": CATALOG_SCHEMA,
            "currency": self.payload.get("currency"),
            "credit_unit": self.payload["credit_unit"],
            "plans": [item(value) for value in self.plans.values()],
            "credit_packs": [item(value) for value in self.packs.values()],
            "activation": "active" if (
                stripe_configured()
                and all(plan.get("stripe_price_id") for plan in self.plans.values() if plan["kind"] == "subscription")
                and all(pack.get("stripe_price_id") for pack in self.packs.values())
            ) else "blocked_external",
        }


def load_catalog() -> BillingCatalog:
    configured = os.getenv("MAGISTRATE_BILLING_CATALOG_PATH", "").strip()
    path = Path(configured) if configured else Path(__file__).resolve().parents[1] / "billing_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BillingError("catalog_unavailable", "Billing catalog is unavailable.", status_code=503) from exc
    if not isinstance(payload, dict):
        raise BillingError("catalog_invalid", "Billing catalog must be an object.", status_code=503)
    return BillingCatalog(payload)


def stripe_configured() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY", "").strip() and os.getenv("STRIPE_WEBHOOK_SECRET", "").strip())


def _legacy_settings() -> dict[str, str]:
    names = (
        "MAGISTRATE_STRIPE_SECRET_KEY", "MAGISTRATE_STRIPE_WEBHOOK_SECRET",
        "MAGISTRATE_STRIPE_PRICE_ID", "MAGISTRATE_BILLING_SUCCESS_URL",
        "MAGISTRATE_BILLING_CANCEL_URL", "MAGISTRATE_BILLING_PORTAL_RETURN_URL",
    )
    return {name: os.getenv(name, "").strip() for name in names}


def billing_available() -> bool:
    legacy = _legacy_settings()
    return stripe_configured() or all(legacy.values())


def validate_billing_configuration() -> BillingCatalog:
    catalog = load_catalog()
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    webhook = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if bool(key) != bool(webhook):
        raise RuntimeError("STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be configured together")
    production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {
        "dev", "development", "test", "testing",
    }
    allowed_key_prefixes = ("sk_live_",) if production else ("sk_live_", "sk_test_")
    if key and not key.startswith(allowed_key_prefixes):
        raise RuntimeError("STRIPE_SECRET_KEY has an invalid prefix")
    if webhook and not webhook.startswith("whsec_"):
        raise RuntimeError("STRIPE_WEBHOOK_SECRET has an invalid prefix")
    if key and (
        any(not plan.get("stripe_price_id") for plan in catalog.plans.values() if plan["kind"] == "subscription")
        or any(not pack.get("stripe_price_id") for pack in catalog.packs.values())
    ):
        raise RuntimeError("Stripe activation requires real price ids for every paid catalog entry")
    if key:
        origins = [
            value.strip().rstrip("/")
            for value in os.getenv("MAGISTRATE_BILLING_RETURN_ORIGINS", "").split(",")
            if value.strip()
        ]
        if not origins:
            raise RuntimeError("Stripe activation requires MAGISTRATE_BILLING_RETURN_ORIGINS")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"https", "magistrate"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment
            ):
                raise RuntimeError("MAGISTRATE_BILLING_RETURN_ORIGINS contains an invalid origin")

    legacy = _legacy_settings()
    if any(legacy.values()) and not all(legacy.values()):
        raise RuntimeError("Legacy Stripe billing configuration is incomplete")
    if all(legacy.values()):
        if (
            not legacy["MAGISTRATE_STRIPE_SECRET_KEY"].startswith(allowed_key_prefixes)
            or not legacy["MAGISTRATE_STRIPE_WEBHOOK_SECRET"].startswith("whsec_")
            or not legacy["MAGISTRATE_STRIPE_PRICE_ID"].startswith("price_")
        ):
            raise RuntimeError("Legacy Stripe billing configuration is invalid")
        for name in (
            "MAGISTRATE_BILLING_SUCCESS_URL", "MAGISTRATE_BILLING_CANCEL_URL",
            "MAGISTRATE_BILLING_PORTAL_RETURN_URL",
        ):
            parsed = urlsplit(legacy[name])
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.username or parsed.password or parsed.fragment or not parsed.netloc or not (
                parsed.scheme == "https" or (not production and local and parsed.scheme == "http")
            ):
                raise RuntimeError("Legacy Stripe billing configuration is invalid")
    return catalog


def _connect() -> sqlite3.Connection:
    db.init_db()
    connection = connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def billing_status(user_id: str) -> dict[str, Any]:
    """Compatibility projection used by the accepted onboarding contract."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT status, current_period_end, external_customer_ref FROM billing_accounts WHERE owner_user_id = ?",
            (user_id,),
        ).fetchone()
    status = str(row["status"]) if row else "none"
    return {
        "schema_version": "billing-status.v1",
        "provider": "stripe" if billing_available() else None,
        "available": billing_available(),
        "status": status,
        "active": status in {"active", "trialing"},
        "current_period_end": int(row["current_period_end"]) if row and row["current_period_end"] is not None else None,
        "customer_portal_available": bool(row and row["external_customer_ref"] and billing_available()),
    }


def subscription_active(user_id: str) -> bool:
    return bool(billing_status(user_id)["active"])


def _now() -> int:
    return int(time.time())


def _period_key(now: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(now))


def _microcredits(credits: int) -> int:
    return credits * MICROCREDITS_PER_CREDIT


def _ledger_id(owner: str, key: str) -> str:
    return "cle_" + hashlib.sha256(f"{owner}\0{key}".encode()).hexdigest()[:32]


def _reservation_id(owner: str, objective: str) -> str:
    return "cr_" + hashlib.sha256(f"{owner}\0{objective}".encode()).hexdigest()[:32]


def _usage_id(owner: str, event_id: str) -> str:
    return "use_" + hashlib.sha256(f"{owner}\0{event_id}".encode()).hexdigest()[:32]


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _insert_ledger(connection: sqlite3.Connection, *, owner: str, key: str, entry_type: str,
                   amount: int, balance: int, source: str, now: int,
                   reservation_id: str | None = None, objective_id: str | None = None,
                   metadata: dict[str, Any] | None = None) -> bool:
    result = connection.execute(
        """INSERT OR IGNORE INTO credit_ledger
           (entry_id, owner_user_id, idempotency_key, entry_type, amount_microcredits,
            balance_after_microcredits, reservation_id, objective_id, source, metadata_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (_ledger_id(owner, key), owner, key, entry_type, amount, balance,
         reservation_id, objective_id, source,
         json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")), now),
    )
    return result.rowcount == 1


class CreditLedger:
    def __init__(self, catalog_loader: Callable[[], BillingCatalog] = load_catalog):
        self._catalog_loader = catalog_loader

    def _account(self, connection: sqlite3.Connection, owner: str, now: int) -> sqlite3.Row:
        catalog = self._catalog_loader()
        row = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
        if row is None:
            connection.execute(
                """INSERT INTO billing_accounts
                   (owner_user_id, provider, status, created_at, updated_at, catalog_id,
                    available_microcredits, reserved_microcredits,
                    period_spend_microcredits, period_key)
                   VALUES (?, 'stripe', 'active', ?, ?, ?, 0, 0, 0, NULL)""",
                (owner, now, now, catalog.default_plan),
            )
            row = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
        plan = catalog.plans.get(row["catalog_id"])
        if plan is None:
            raise BillingError("account_plan_invalid", "Billing account references an unknown plan.", status_code=503)
        # Free and operator-assigned demo plans have calendar-month grants.
        # Paid renewals are granted only by invoice.paid webhooks.
        period = _period_key(now)
        if plan["kind"] in {"free", "demo"} and row["period_key"] != period:
            grant = _microcredits(plan["monthly_included_credits"])
            balance = int(row["available_microcredits"]) + grant
            key = f"included:{plan['id']}:{period}"
            if _insert_ledger(connection, owner=owner, key=key, entry_type="included_grant",
                              amount=grant, balance=balance, source="catalog", now=now,
                              metadata={"catalog_id": plan["id"], "period": period}):
                connection.execute(
                    """UPDATE billing_accounts SET available_microcredits = ?,
                       period_spend_microcredits = 0, period_key = ?, updated_at = ?
                       WHERE owner_user_id = ?""", (balance, period, now, owner),
                )
            row = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
        return row

    def summary(self, owner: str, *, now: int | None = None, ledger_limit: int = 25) -> dict[str, Any]:
        current = _now() if now is None else now
        catalog = self._catalog_loader()
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._account(connection, owner, current)
            plan = catalog.plans[account["catalog_id"]]
            entries = connection.execute(
                """SELECT entry_id, entry_type, amount_microcredits, balance_after_microcredits,
                          reservation_id, objective_id, source, metadata_json, created_at
                   FROM credit_ledger WHERE owner_user_id = ?
                   ORDER BY created_at DESC, entry_id DESC LIMIT ?""", (owner, ledger_limit),
            ).fetchall()
            usage_rows = connection.execute(
                """SELECT usage_id, event_id, objective_id, provider, model, input_tokens,
                          output_tokens, compute_milliseconds, cost_microcredits, created_at
                   FROM execution_usage_ledger WHERE owner_user_id = ?
                   ORDER BY created_at DESC, usage_id DESC LIMIT ?""", (owner, ledger_limit),
            ).fetchall()
            connection.commit()
        balance = int(account["available_microcredits"])
        warning = balance <= _microcredits(plan["low_credit_warning_credits"])
        grace = account["status"] == "past_due" and account["grace_ends_at"] and int(account["grace_ends_at"]) > current
        execution_enabled = "execution" in plan["entitlements"] and (
            account["status"] in {"active", "trialing"} or bool(grace)
        )
        return {
            "schema_version": BILLING_SCHEMA,
            "catalog_id": plan["id"], "plan_name": plan["name"],
            "subscription_status": account["status"],
            "current_period_end": account["current_period_end"],
            "cancel_at_period_end": bool(account["cancel_at_period_end"]),
            "grace_ends_at": account["grace_ends_at"],
            "balance_microcredits": balance,
            "reserved_microcredits": int(account["reserved_microcredits"]),
            "period_spend_microcredits": int(account["period_spend_microcredits"]),
            "low_credit_warning": warning,
            "entitlements": {name: True for name in plan["entitlements"]} | {"execution": bool(execution_enabled)},
            "limits": {
                "concurrency": plan["concurrency_limit"],
                "monthly_spend_microcredits": _microcredits(plan["monthly_spend_limit_credits"]),
            },
            "ledger": [{
                "entry_id": row["entry_id"], "type": row["entry_type"],
                "amount_microcredits": row["amount_microcredits"],
                "balance_after_microcredits": row["balance_after_microcredits"],
                "reservation_id": row["reservation_id"], "objective_id": row["objective_id"],
                "source": row["source"], "created_at": row["created_at"],
            } for row in entries],
            "usage": [{key: row[key] for key in row.keys()} for row in usage_rows],
        }

    def reserve_objective(self, owner: str, objective_id: str, *, idempotency_key: str, now: int | None = None) -> dict[str, Any]:
        current = _now() if now is None else now
        catalog = self._catalog_loader()
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._account(connection, owner, current)
            plan = catalog.plans[account["catalog_id"]]
            existing = connection.execute(
                "SELECT * FROM credit_reservations WHERE owner_user_id = ? AND idempotency_key = ?",
                (owner, idempotency_key),
            ).fetchone()
            if existing:
                if existing["objective_id"] != objective_id:
                    raise BillingError("idempotency_conflict", "Reservation key is bound to another objective.", status_code=409)
                connection.commit()
                return _row_dict(existing)
            grace = account["status"] == "past_due" and account["grace_ends_at"] and int(account["grace_ends_at"]) > current
            if "execution" not in plan["entitlements"] or (account["status"] not in {"active", "trialing"} and not grace):
                raise BillingError("execution_not_entitled", "This account is not entitled to start execution.", status_code=403)
            active = connection.execute(
                "SELECT COUNT(*) FROM credit_reservations WHERE owner_user_id = ? AND status = 'reserved'", (owner,),
            ).fetchone()[0]
            if active >= plan["concurrency_limit"]:
                raise BillingError("concurrency_limit", "The plan concurrency limit is already in use.", status_code=409)
            estimate = _microcredits(plan["objective_reserve_credits"])
            if int(account["available_microcredits"]) < estimate:
                raise InsufficientCredits()
            spend_limit = _microcredits(plan["monthly_spend_limit_credits"])
            if int(account["period_spend_microcredits"]) + int(account["reserved_microcredits"]) + estimate > spend_limit:
                raise BillingError("spend_limit", "The monthly execution spend limit would be exceeded.", status_code=402)
            reservation_id = _reservation_id(owner, objective_id)
            balance = int(account["available_microcredits"]) - estimate
            connection.execute(
                """INSERT INTO credit_reservations
                   (reservation_id, owner_user_id, objective_id, idempotency_key,
                    estimated_microcredits, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,'reserved',?,?)""",
                (reservation_id, owner, objective_id, idempotency_key, estimate, current, current),
            )
            connection.execute(
                """UPDATE billing_accounts SET available_microcredits = ?,
                   reserved_microcredits = reserved_microcredits + ?, updated_at = ?
                   WHERE owner_user_id = ?""", (balance, estimate, current, owner),
            )
            _insert_ledger(connection, owner=owner, key=f"reserve:{idempotency_key}", entry_type="reservation",
                           amount=-estimate, balance=balance, source="execution", now=current,
                           reservation_id=reservation_id, objective_id=objective_id,
                           metadata={"estimated_microcredits": estimate})
            connection.commit()
            return _row_dict(connection.execute("SELECT * FROM credit_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone())

    def release_objective(self, owner: str, objective_id: str, *, reason: str, now: int | None = None) -> bool:
        current = _now() if now is None else now
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT * FROM credit_reservations WHERE owner_user_id = ? AND objective_id = ?", (owner, objective_id),
            ).fetchone()
            if reservation is None or reservation["status"] != "reserved":
                connection.commit()
                return False
            account = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
            amount = int(reservation["estimated_microcredits"])
            balance = int(account["available_microcredits"]) + amount
            connection.execute("UPDATE credit_reservations SET status = 'released', updated_at = ? WHERE reservation_id = ?", (current, reservation["reservation_id"]))
            connection.execute("UPDATE billing_accounts SET available_microcredits = ?, reserved_microcredits = reserved_microcredits - ?, updated_at = ? WHERE owner_user_id = ?", (balance, amount, current, owner))
            _insert_ledger(connection, owner=owner, key=f"release:{reservation['reservation_id']}", entry_type="release",
                           amount=amount, balance=balance, source="execution", now=current,
                           reservation_id=reservation["reservation_id"], objective_id=objective_id,
                           metadata={"reason": reason[:64]})
            connection.commit()
            return True

    def measured_cost(self, usage: dict[str, Any]) -> int:
        if not isinstance(usage, dict) or set(usage) != {
            "provider", "model", "input_tokens", "output_tokens", "compute_milliseconds",
        }:
            raise BillingError("usage_invalid", "Measured execution usage is invalid.", status_code=422)
        catalog = self._catalog_loader()
        provider, model = usage.get("provider"), usage.get("model")
        rate = catalog.rates.get((provider, model))
        if rate is None:
            raise BillingError("rate_not_configured", "Measured execution usage has no configured billing rate.", status_code=422)
        values: dict[str, int] = {}
        for field in ("input_tokens", "output_tokens", "compute_milliseconds"):
            value = usage.get(field, 0)
            if type(value) is not int or value < 0:
                raise BillingError("usage_invalid", "Measured execution usage is invalid.", status_code=422)
            values[field] = value
        numerator = (
            values["input_tokens"] * rate["input_usd_micros_per_million"]
            + values["output_tokens"] * rate["output_usd_micros_per_million"]
        )
        usd_micros = math.ceil(numerator / 1_000_000) + math.ceil(
            values["compute_milliseconds"] * rate["compute_usd_micros_per_hour"] / 3_600_000
        )
        billable_usd_micros = math.ceil(usd_micros * (10_000 + rate["margin_bps"]) / 10_000)
        return math.ceil(billable_usd_micros * MICROCREDITS_PER_CREDIT / catalog.usd_micros_per_credit)

    def settle_objective(self, owner: str, objective_id: str, usage: dict[str, Any], *, event_id: str, now: int | None = None) -> dict[str, Any]:
        current = _now() if now is None else now
        actual = self.measured_cost(usage)
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute("SELECT * FROM credit_reservations WHERE owner_user_id = ? AND objective_id = ?", (owner, objective_id)).fetchone()
            if reservation is None:
                raise BillingError("reservation_not_found", "Execution has no credit reservation.", status_code=409)
            if reservation["status"] == "settled":
                canonical_usage = json.dumps(usage, sort_keys=True, separators=(",", ":"))
                prior_usage = connection.execute(
                    "SELECT event_id FROM execution_usage_ledger WHERE owner_user_id = ? AND objective_id = ?",
                    (owner, objective_id),
                ).fetchone()
                if (
                    int(reservation["actual_microcredits"]) != actual
                    or reservation["usage_json"] != canonical_usage
                    or prior_usage is None or prior_usage["event_id"] != event_id
                ):
                    raise BillingError("settlement_conflict", "Execution was already settled with different usage.", status_code=409)
                connection.commit()
                return _row_dict(reservation)
            if reservation["status"] != "reserved":
                raise BillingError("reservation_released", "A released execution reservation cannot settle.", status_code=409)
            account = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
            estimate = int(reservation["estimated_microcredits"])
            adjustment = estimate - actual
            balance = int(account["available_microcredits"]) + adjustment
            connection.execute("UPDATE credit_reservations SET status = 'settled', actual_microcredits = ?, usage_json = ?, updated_at = ? WHERE reservation_id = ?", (actual, json.dumps(usage, sort_keys=True, separators=(",", ":")), current, reservation["reservation_id"]))
            connection.execute(
                """INSERT INTO execution_usage_ledger
                   (usage_id, owner_user_id, event_id, objective_id, reservation_id,
                    provider, model, input_tokens, output_tokens, compute_milliseconds,
                    cost_microcredits, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_usage_id(owner, event_id), owner, event_id, objective_id,
                 reservation["reservation_id"], usage["provider"], usage["model"],
                 usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                 usage.get("compute_milliseconds", 0), actual, current),
            )
            connection.execute(
                """UPDATE billing_accounts SET available_microcredits = ?,
                   reserved_microcredits = reserved_microcredits - ?,
                   period_spend_microcredits = period_spend_microcredits + ?, updated_at = ?
                   WHERE owner_user_id = ?""", (balance, estimate, actual, current, owner),
            )
            _insert_ledger(connection, owner=owner, key=f"settle:{event_id}", entry_type="settlement",
                           amount=adjustment, balance=balance, source="execution", now=current,
                           reservation_id=reservation["reservation_id"], objective_id=objective_id,
                           metadata={"actual_microcredits": actual, "estimated_microcredits": estimate, "usage": usage})
            connection.commit()
            return _row_dict(connection.execute("SELECT * FROM credit_reservations WHERE reservation_id = ?", (reservation["reservation_id"],)).fetchone())

    def refund_objective(self, owner: str, objective_id: str, microcredits: int, *, idempotency_key: str, reason: str, now: int | None = None) -> bool:
        """Credit a bounded execution correction exactly once.

        This is an internal service seam for reviewed provider adjustments; no
        end-user endpoint can mint refunds.
        """
        if type(microcredits) is not int or microcredits <= 0:
            raise BillingError("refund_invalid", "Refund amount is invalid.")
        current = _now() if now is None else now
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT * FROM credit_reservations WHERE owner_user_id = ? AND objective_id = ? AND status = 'settled'",
                (owner, objective_id),
            ).fetchone()
            if reservation is None or microcredits > int(reservation["actual_microcredits"]):
                raise BillingError("refund_invalid", "Refund exceeds settled execution cost.", status_code=409)
            account = connection.execute("SELECT * FROM billing_accounts WHERE owner_user_id = ?", (owner,)).fetchone()
            existing = connection.execute(
                """SELECT entry_type, amount_microcredits, objective_id FROM credit_ledger
                   WHERE owner_user_id = ? AND idempotency_key = ?""", (owner, idempotency_key),
            ).fetchone()
            if existing:
                if existing["entry_type"] != "refund" or int(existing["amount_microcredits"]) != microcredits or existing["objective_id"] != objective_id:
                    raise BillingError("idempotency_conflict", "Refund key is bound to different facts.", status_code=409)
                connection.commit()
                return False
            refunded = connection.execute(
                """SELECT COALESCE(SUM(amount_microcredits), 0) FROM credit_ledger
                   WHERE owner_user_id = ? AND reservation_id = ? AND entry_type = 'refund'""",
                (owner, reservation["reservation_id"]),
            ).fetchone()[0]
            if int(refunded) + microcredits > int(reservation["actual_microcredits"]):
                raise BillingError("refund_invalid", "Cumulative refunds exceed settled execution cost.", status_code=409)
            balance = int(account["available_microcredits"]) + microcredits
            _insert_ledger(connection, owner=owner, key=idempotency_key, entry_type="refund",
                           amount=microcredits, balance=balance, source="execution", now=current,
                           reservation_id=reservation["reservation_id"], objective_id=objective_id,
                           metadata={"reason": reason[:128]})
            connection.execute(
                """UPDATE billing_accounts SET available_microcredits = ?,
                   period_spend_microcredits = CASE
                     WHEN period_spend_microcredits > ? THEN period_spend_microcredits - ? ELSE 0 END,
                   updated_at = ? WHERE owner_user_id = ?""",
                (balance, microcredits, microcredits, current, owner),
            )
            connection.commit()
            return True

    def add_credits(self, owner: str, credits: int, *, idempotency_key: str, source: str, now: int | None = None) -> bool:
        if type(credits) is not int or credits <= 0:
            raise BillingError("credits_invalid", "Credit grant is invalid.")
        current = _now() if now is None else now
        amount = _microcredits(credits)
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._account(connection, owner, current)
            existing = connection.execute(
                """SELECT entry_type, amount_microcredits, source FROM credit_ledger
                   WHERE owner_user_id = ? AND idempotency_key = ?""", (owner, idempotency_key),
            ).fetchone()
            if existing:
                if existing["entry_type"] != "topup" or int(existing["amount_microcredits"]) != amount or existing["source"] != source:
                    raise BillingError("idempotency_conflict", "Credit grant key is bound to different facts.", status_code=409)
                connection.commit()
                return False
            balance = int(account["available_microcredits"]) + amount
            _insert_ledger(connection, owner=owner, key=idempotency_key, entry_type="topup", amount=amount,
                           balance=balance, source=source, now=current)
            connection.execute("UPDATE billing_accounts SET available_microcredits = ?, updated_at = ? WHERE owner_user_id = ?", (balance, current, owner))
            connection.commit()
            return True


async def _legacy_stripe_post(path: str, fields: list[tuple[str, str]]) -> dict[str, Any]:
    settings = _legacy_settings()
    if not all(settings.values()):
        raise BillingError("stripe_not_configured", "Legacy Stripe billing is not configured.", status_code=503)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                f"https://api.stripe.com/v1/{path}", data=fields,
                headers={
                    "Authorization": f"Bearer {settings['MAGISTRATE_STRIPE_SECRET_KEY']}",
                    "Idempotency-Key": secrets.token_hex(24),
                },
            )
    except httpx.HTTPError as exc:
        raise BillingError("stripe_unavailable", "Billing provider is unavailable.", status_code=503) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not 200 <= response.status_code < 300 or not isinstance(payload, dict):
        raise BillingError("stripe_request_failed", "Billing provider could not create this session.", status_code=503)
    return payload


async def create_checkout(user_id: str, email: str | None) -> dict[str, str]:
    """Create a session for the deployed pre-catalog subscription contract."""
    settings = _legacy_settings()
    with _connect() as connection:
        row = connection.execute(
            "SELECT external_customer_ref FROM billing_accounts WHERE owner_user_id = ?", (user_id,),
        ).fetchone()
    fields = [
        ("mode", "subscription"),
        ("line_items[0][price]", settings["MAGISTRATE_STRIPE_PRICE_ID"]),
        ("line_items[0][quantity]", "1"),
        ("success_url", settings["MAGISTRATE_BILLING_SUCCESS_URL"]),
        ("cancel_url", settings["MAGISTRATE_BILLING_CANCEL_URL"]),
        ("client_reference_id", user_id),
        ("metadata[magistrate_user_id]", user_id),
        ("subscription_data[metadata][magistrate_user_id]", user_id),
        ("allow_promotion_codes", "true"),
    ]
    if row and row["external_customer_ref"]:
        fields.append(("customer", str(row["external_customer_ref"])))
    elif isinstance(email, str) and "@" in email and len(email) <= 320:
        fields.append(("customer_email", email))
    payload = await _legacy_stripe_post("checkout/sessions", fields)
    session_id, url = payload.get("id"), payload.get("url")
    if not isinstance(session_id, str) or not session_id.startswith("cs_") or not isinstance(url, str):
        raise BillingError("stripe_response_invalid", "Billing provider returned an invalid checkout session.", status_code=503)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BillingError("stripe_response_invalid", "Billing provider returned an invalid checkout URL.", status_code=503)
    return {"schema_version": "billing-checkout.v1", "checkout_session_id": session_id, "url": url}


async def create_portal(user_id: str) -> dict[str, str]:
    settings = _legacy_settings()
    with _connect() as connection:
        row = connection.execute(
            "SELECT external_customer_ref FROM billing_accounts WHERE owner_user_id = ?", (user_id,),
        ).fetchone()
    if not row or not row["external_customer_ref"]:
        raise BillingError("customer_not_found", "No billing customer exists for this account.", status_code=409)
    payload = await _legacy_stripe_post("billing_portal/sessions", [
        ("customer", str(row["external_customer_ref"])),
        ("return_url", settings["MAGISTRATE_BILLING_PORTAL_RETURN_URL"]),
    ])
    url = payload.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
        raise BillingError("stripe_response_invalid", "Billing provider returned an invalid portal URL.", status_code=503)
    return {"schema_version": "billing-portal.v1", "url": url}


class StripeClient:
    def __init__(self, secret_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.secret_key = (secret_key or os.getenv("STRIPE_SECRET_KEY", "")).strip()
        self.transport = transport
        if not self.secret_key:
            raise BillingError("stripe_not_configured", "Stripe activation is blocked on external account configuration.", status_code=503)

    async def post(self, path: str, data: list[tuple[str, str]], *, idempotency_key: str) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key}
        async with httpx.AsyncClient(base_url="https://api.stripe.com/v1", auth=(self.secret_key, ""), transport=self.transport, timeout=15) as client:
            response = await client.post(path, data=data, headers=headers)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not isinstance(payload, dict):
            raise BillingError("stripe_request_failed", "Stripe could not complete the billing request.", status_code=502)
        return payload


class BillingService:
    def __init__(self, *, stripe_factory: Callable[[], StripeClient] = StripeClient,
                 catalog_loader: Callable[[], BillingCatalog] = load_catalog,
                 ledger: CreditLedger | None = None):
        self.stripe_factory = stripe_factory
        self.catalog_loader = catalog_loader
        self.ledger = ledger or CreditLedger(catalog_loader)

    async def _customer(self, owner: str) -> str:
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self.ledger._account(connection, owner, _now())
            if account["external_customer_ref"]:
                return str(account["external_customer_ref"])
            profile = connection.execute("SELECT email, name FROM user_profiles WHERE user_id = ?", (owner,)).fetchone()
        data = [("metadata[magistrate_owner]", owner)]
        if profile and profile["email"]:
            data.append(("email", str(profile["email"])))
        if profile and profile["name"]:
            data.append(("name", str(profile["name"])))
        payload = await self.stripe_factory().post("/customers", data, idempotency_key=f"magistrate-customer-{hashlib.sha256(owner.encode()).hexdigest()}")
        customer = payload.get("id")
        if not isinstance(customer, str) or not customer.startswith("cus_"):
            raise BillingError("stripe_response_invalid", "Stripe returned an invalid customer.", status_code=502)
        now = _now()
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.ledger._account(connection, owner, now)
            connection.execute(
                """UPDATE billing_accounts
                   SET external_customer_ref = COALESCE(external_customer_ref, ?), updated_at = ?
                   WHERE owner_user_id = ?""",
                (customer, now, owner),
            )
            row = connection.execute(
                "SELECT external_customer_ref FROM billing_accounts WHERE owner_user_id = ?", (owner,),
            ).fetchone()
        return str(row[0])

    @staticmethod
    def _return_url(value: str) -> str:
        if not isinstance(value, str) or len(value) > 2048:
            raise BillingError("return_url_invalid", "Billing return URL is invalid.")
        parsed = urlsplit(value)
        allowed = {item.strip().rstrip("/") for item in os.getenv("MAGISTRATE_BILLING_RETURN_ORIGINS", "").split(",") if item.strip()}
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if parsed.scheme not in {"https", "magistrate"} or origin not in allowed or parsed.username or parsed.password:
            raise BillingError("return_url_invalid", "Billing return URL is not allowlisted.")
        return value

    async def checkout(self, owner: str, catalog_id: str, return_url: str, idempotency_key: str) -> dict[str, str]:
        catalog = self.catalog_loader()
        kind: str
        item = catalog.plans.get(catalog_id)
        if item and item["kind"] == "subscription":
            kind = "subscription"
        else:
            item = catalog.packs.get(catalog_id)
            kind = "credit_pack"
        if item is None:
            raise BillingError("catalog_item_not_found", "Billing catalog item was not found.", status_code=404)
        price = item.get("stripe_price_id")
        if not price:
            raise BillingError("price_not_activated", "This catalog item is blocked on Stripe price activation.", status_code=503)
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key or ""):
            raise BillingError("idempotency_invalid", "A bounded idempotency key is required.")
        with _connect() as connection:
            prior = connection.execute(
                """SELECT catalog_id, kind FROM billing_checkout_sessions
                   WHERE owner_user_id = ? AND idempotency_key = ?""",
                (owner, idempotency_key),
            ).fetchone()
        if prior and (prior["catalog_id"] != catalog_id or prior["kind"] != kind):
            raise BillingError("idempotency_conflict", "Checkout key is bound to another purchase.", status_code=409)
        if kind == "credit_pack" and not self.ledger.summary(owner)["entitlements"].get("topups"):
            raise BillingError("topup_not_entitled", "This plan does not permit credit top-ups.", status_code=403)
        customer = await self._customer(owner)
        returned = self._return_url(return_url)
        mode = "subscription" if kind == "subscription" else "payment"
        data = [
            ("customer", customer), ("mode", mode), ("line_items[0][price]", price),
            ("line_items[0][quantity]", "1"), ("success_url", returned), ("cancel_url", returned),
            ("client_reference_id", owner), ("metadata[magistrate_owner]", owner),
            ("metadata[catalog_id]", catalog_id), ("metadata[kind]", kind),
        ]
        if kind == "subscription":
            data += [("subscription_data[metadata][magistrate_owner]", owner), ("subscription_data[metadata][catalog_id]", catalog_id)]
        owner_digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()
        payload = await self.stripe_factory().post("/checkout/sessions", data, idempotency_key=f"magistrate-checkout-{owner_digest}-{idempotency_key}")
        session_id, url = payload.get("id"), payload.get("url")
        if not isinstance(session_id, str) or not session_id.startswith("cs_") or not isinstance(url, str) or not url.startswith("https://"):
            raise BillingError("stripe_response_invalid", "Stripe returned an invalid checkout session.", status_code=502)
        with _connect() as connection:
            try:
                connection.execute("INSERT INTO billing_checkout_sessions (stripe_session_id, owner_user_id, catalog_id, kind, idempotency_key, created_at) VALUES (?,?,?,?,?,?)", (session_id, owner, catalog_id, kind, idempotency_key, _now()))
            except sqlite3.IntegrityError:
                row = connection.execute("SELECT * FROM billing_checkout_sessions WHERE owner_user_id = ? AND idempotency_key = ?", (owner, idempotency_key)).fetchone()
                if not row or row["catalog_id"] != catalog_id:
                    raise BillingError("idempotency_conflict", "Checkout key is bound to another purchase.", status_code=409) from None
        return {"checkout_url": url, "session_id": session_id}

    async def portal(self, owner: str, return_url: str, idempotency_key: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key or ""):
            raise BillingError("idempotency_invalid", "A bounded idempotency key is required.")
        customer = await self._customer(owner)
        owner_digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()
        payload = await self.stripe_factory().post("/billing_portal/sessions", [("customer", customer), ("return_url", self._return_url(return_url))], idempotency_key=f"magistrate-portal-{owner_digest}-{idempotency_key}")
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise BillingError("stripe_response_invalid", "Stripe returned an invalid portal session.", status_code=502)
        return {"portal_url": url}


class StripeWebhookProcessor:
    def __init__(self, catalog_loader: Callable[[], BillingCatalog] = load_catalog,
                 ledger: CreditLedger | None = None):
        self.catalog_loader = catalog_loader
        self.ledger = ledger or CreditLedger(catalog_loader)

    @staticmethod
    def verify(payload: bytes, signature: str, secret: str, *, now: int | None = None) -> None:
        if len(payload) > MAX_WEBHOOK_BYTES:
            raise BillingError("webhook_too_large", "Stripe webhook is too large.", status_code=413)
        fields: dict[str, list[str]] = {}
        for part in signature.split(","):
            key, separator, value = part.partition("=")
            if separator:
                fields.setdefault(key, []).append(value)
        try:
            timestamp = int(fields.get("t", [""])[0])
        except ValueError:
            timestamp = 0
        current = _now() if now is None else now
        if abs(current - timestamp) > 300:
            raise BillingError("webhook_signature_invalid", "Stripe webhook signature is invalid.", status_code=400)
        signed = str(timestamp).encode() + b"." + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in fields.get("v1", [])):
            raise BillingError("webhook_signature_invalid", "Stripe webhook signature is invalid.", status_code=400)

    @staticmethod
    def _metadata(obj: dict[str, Any]) -> tuple[str | None, str | None]:
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            return None, None
        owner, catalog_id = metadata.get("magistrate_owner"), metadata.get("catalog_id")
        return (owner if isinstance(owner, str) else None, catalog_id if isinstance(catalog_id, str) else None)

    def process(self, payload: bytes, signature: str, *, now: int | None = None) -> dict[str, Any]:
        current = _now() if now is None else now
        secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise BillingError("stripe_not_configured", "Stripe webhook handling is not activated.", status_code=503)
        self.verify(payload, signature, secret, now=current)
        try:
            event = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BillingError("webhook_invalid", "Stripe webhook JSON is invalid.") from exc
        if (
            not isinstance(event, dict) or not isinstance(event.get("id"), str)
            or not isinstance(event.get("type"), str) or type(event.get("created")) is not int
            or event["created"] <= 0
        ):
            raise BillingError("webhook_invalid", "Stripe webhook envelope is invalid.")
        event_id, event_type, event_created = event["id"], event["type"], event["created"]
        obj = event.get("data", {}).get("object") if isinstance(event.get("data"), dict) else None
        if not isinstance(obj, dict):
            raise BillingError("webhook_invalid", "Stripe webhook object is invalid.")
        digest = hashlib.sha256(payload).hexdigest()
        catalog = self.catalog_loader()
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT payload_sha256 FROM billing_webhook_events WHERE event_id = ?", (event_id,)).fetchone()
            if prior:
                if prior[0] is not None and prior[0] != digest:
                    raise BillingError("webhook_conflict", "Stripe event id was reused with different bytes.", status_code=409)
                connection.commit()
                return {"status": "duplicate", "event_id": event_id}
            # Reserve event identity inside the same transaction as every state change.
            connection.execute(
                """INSERT INTO billing_webhook_events
                   (event_id, event_type, received_at, payload_sha256, processed_at)
                   VALUES (?,?,?,?,?)""",
                (event_id, event_type, current, digest, current),
            )
            if event_type == "checkout.session.completed" and obj.get("mode") == "payment" and obj.get("payment_status") == "paid":
                session = connection.execute("SELECT * FROM billing_checkout_sessions WHERE stripe_session_id = ?", (obj.get("id"),)).fetchone()
                if session is None or session["kind"] != "credit_pack":
                    raise BillingError("checkout_unknown", "Paid checkout is not bound to a Magistrate credit pack.", status_code=409)
                pack = catalog.packs.get(session["catalog_id"])
                if pack is None:
                    raise BillingError("catalog_item_not_found", "Purchased credit pack no longer exists.", status_code=409)
                account = self.ledger._account(connection, session["owner_user_id"], current)
                amount = _microcredits(pack["credits"])
                balance = int(account["available_microcredits"]) + amount
                _insert_ledger(connection, owner=session["owner_user_id"], key=f"stripe:{event_id}", entry_type="topup", amount=amount, balance=balance, source="stripe", now=current, metadata={"catalog_id": pack["id"], "stripe_session_id": obj.get("id")})
                connection.execute("UPDATE billing_accounts SET available_microcredits = ?, updated_at = ? WHERE owner_user_id = ?", (balance, current, session["owner_user_id"]))
            elif event_type.startswith("customer.subscription."):
                self._subscription(connection, catalog, event_type, obj, current, event_created)
            elif event_type in {"invoice.paid", "invoice.payment_failed"}:
                self._invoice(connection, catalog, event_id, event_type, obj, current, event_created)
            connection.commit()
        return {"status": "processed", "event_id": event_id}

    def _subscription(
        self, connection: sqlite3.Connection, catalog: BillingCatalog,
        event_type: str, obj: dict[str, Any], now: int, event_created: int,
    ) -> None:
        owner, catalog_id = self._metadata(obj)
        subscription_id = obj.get("id")
        customer_id = obj.get("customer")
        customer_owner = None
        if isinstance(customer_id, str):
            row = connection.execute("SELECT owner_user_id FROM billing_accounts WHERE external_customer_ref = ?", (customer_id,)).fetchone()
            customer_owner = str(row[0]) if row else None
        if owner and customer_owner and owner != customer_owner:
            raise BillingError("subscription_owner_conflict", "Stripe subscription ownership is inconsistent.", status_code=409)
        owner = owner or customer_owner
        if not owner or customer_owner != owner or not isinstance(subscription_id, str):
            raise BillingError("subscription_unknown", "Stripe subscription is not bound to a Magistrate account.", status_code=409)
        items = obj.get("items", {}).get("data", []) if isinstance(obj.get("items"), dict) else []
        price = items[0].get("price", {}).get("id") if items and isinstance(items[0], dict) and isinstance(items[0].get("price"), dict) else None
        found = catalog.price_item(price) if isinstance(price, str) else None
        price_catalog_id = found[1]["id"] if found and found[0] == "subscription" else None
        if catalog_id and price_catalog_id and catalog_id != price_catalog_id:
            raise BillingError("subscription_price_conflict", "Stripe subscription metadata and price disagree.", status_code=409)
        catalog_id = price_catalog_id
        if catalog_id not in catalog.plans or catalog.plans[catalog_id]["kind"] != "subscription":
            raise BillingError("subscription_price_unknown", "Stripe subscription price is not in the active catalog.", status_code=409)
        account = self.ledger._account(connection, owner, now)
        if account["provider_event_created"] is not None and int(account["provider_event_created"]) > event_created:
            return
        status = obj.get("status") if isinstance(obj.get("status"), str) else "canceled"
        if event_type == "customer.subscription.deleted":
            status = "canceled"
        plan = catalog.plans[catalog_id]
        grace = None
        if status in {"past_due", "unpaid"}:
            grace = (
                int(account["grace_ends_at"]) if account["grace_ends_at"] is not None
                else now + plan["grace_days"] * 86400
            )
        selected = catalog.default_plan if status == "canceled" else catalog_id
        connection.execute(
            """UPDATE billing_accounts SET catalog_id = ?, status = ?,
               subscription_id = ?, current_period_start = ?, current_period_end = ?,
               cancel_at_period_end = ?, grace_ends_at = ?, provider_event_created = ?,
               updated_at = ? WHERE owner_user_id = ?""",
            (selected, status if status != "canceled" else "active", None if status == "canceled" else subscription_id,
             obj.get("current_period_start"), obj.get("current_period_end"),
             1 if obj.get("cancel_at_period_end") is True else 0, grace, event_created, now, owner),
        )

    def _invoice(
        self, connection: sqlite3.Connection, catalog: BillingCatalog, event_id: str,
        event_type: str, obj: dict[str, Any], now: int, event_created: int,
    ) -> None:
        subscription_id = obj.get("subscription")
        if isinstance(subscription_id, dict):
            subscription_id = subscription_id.get("id")
        if not isinstance(subscription_id, str):
            # One-off top-up invoices are intentionally not subscription authority.
            return
        account = connection.execute("SELECT * FROM billing_accounts WHERE subscription_id = ?", (subscription_id,)).fetchone()
        if account is None:
            raise BillingError("subscription_unknown", "Stripe invoice subscription is unknown.", status_code=409)
        if account["provider_event_created"] is not None and int(account["provider_event_created"]) > event_created:
            return
        plan = catalog.plans.get(account["catalog_id"])
        if plan is None or plan["kind"] != "subscription":
            raise BillingError("subscription_price_unknown", "Stripe invoice plan is unknown.", status_code=409)
        if event_type == "invoice.payment_failed":
            grace = (
                int(account["grace_ends_at"]) if account["grace_ends_at"] is not None
                else now + plan["grace_days"] * 86400
            )
            connection.execute(
                """UPDATE billing_accounts SET status = 'past_due', grace_ends_at = ?,
                   provider_event_created = ?, updated_at = ? WHERE owner_user_id = ?""",
                (grace, event_created, now, account["owner_user_id"]),
            )
            return
        period_start = obj.get("period_start") if type(obj.get("period_start")) is int else now
        period_end = obj.get("period_end") if type(obj.get("period_end")) is int else account["current_period_end"]
        grant = _microcredits(plan["monthly_included_credits"])
        balance = int(account["available_microcredits"]) + grant
        # Several paid invoices (for example a proration and the renewal) can
        # exist in one period. Included credits are monthly, not per invoice.
        grant_key = f"included:{subscription_id}:{period_start}"
        inserted = _insert_ledger(connection, owner=account["owner_user_id"], key=grant_key, entry_type="included_grant", amount=grant, balance=balance, source="stripe", now=now, metadata={"catalog_id": plan["id"], "invoice_id": obj.get("id"), "event_id": event_id})
        connection.execute(
            """UPDATE billing_accounts SET status = 'active', grace_ends_at = NULL,
               current_period_start = ?, current_period_end = ?, period_key = ?,
               period_spend_microcredits = 0,
               available_microcredits = available_microcredits + ?,
               provider_event_created = ?, updated_at = ? WHERE owner_user_id = ?""",
            (period_start, period_end, str(period_start), grant if inserted else 0,
             event_created, now, account["owner_user_id"]),
        )


def accept_webhook(raw: bytes, signature_header: str, *, now: int | None = None) -> dict[str, Any]:
    """Consume the pre-catalog subscription webhook contract without granting credits.

    This compatibility seam preserves deployed provider onboarding while all new
    Checkout and credit-pack traffic uses ``StripeWebhookProcessor``.
    """
    current = _now() if now is None else now
    settings = _legacy_settings()
    secret = settings["MAGISTRATE_STRIPE_WEBHOOK_SECRET"]
    if not secret:
        raise BillingError("stripe_not_configured", "Legacy Stripe billing is not configured.", status_code=503)
    StripeWebhookProcessor.verify(raw, signature_header, secret, now=current)
    try:
        event = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BillingError("webhook_invalid", "Stripe webhook JSON is invalid.") from exc
    if not isinstance(event, dict):
        raise BillingError("webhook_invalid", "Stripe webhook envelope is invalid.")
    event_id, event_type, created = event.get("id"), event.get("type"), event.get("created")
    obj = event.get("data", {}).get("object") if isinstance(event.get("data"), dict) else None
    if (
        not isinstance(event_id, str) or not isinstance(event_type, str)
        or type(created) is not int or created <= 0 or not isinstance(obj, dict)
    ):
        raise BillingError("webhook_invalid", "Stripe webhook envelope is invalid.")
    digest = hashlib.sha256(raw).hexdigest()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT payload_sha256 FROM billing_webhook_events WHERE event_id = ?", (event_id,),
        ).fetchone()
        if prior:
            if prior[0] is not None and prior[0] != digest:
                raise BillingError("webhook_conflict", "Stripe event id was reused with different bytes.", status_code=409)
            connection.commit()
            return {"status": "accepted", "duplicate": True}

        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        owner = metadata.get("magistrate_user_id") or obj.get("client_reference_id")
        customer = obj.get("customer")
        if not isinstance(owner, str) and isinstance(customer, str):
            account = connection.execute(
                "SELECT owner_user_id FROM billing_accounts WHERE external_customer_ref = ?", (customer,),
            ).fetchone()
            owner = str(account[0]) if account else None
        status: str | None = None
        subscription_id = obj.get("subscription")
        period_end = None
        if event_type.startswith("customer.subscription."):
            subscription_id = obj.get("id")
            status = obj.get("status") if isinstance(obj.get("status"), str) else "none"
            period_end = obj.get("current_period_end") if type(obj.get("current_period_end")) is int else None
        elif event_type == "checkout.session.completed":
            status = "pending"
        if isinstance(owner, str) and connection.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ?", (owner,),
        ).fetchone():
            existing = connection.execute(
                "SELECT status, provider_event_created FROM billing_accounts WHERE owner_user_id = ?", (owner,),
            ).fetchone()
            subscription_event = event_type.startswith("customer.subscription.")
            should_apply = (
                subscription_event and (
                    existing is None or existing["provider_event_created"] is None
                    or created >= int(existing["provider_event_created"])
                )
            ) or (event_type == "checkout.session.completed" and (
                existing is None or existing["status"] in {"none", "pending"}
            ))
            if should_apply:
                connection.execute(
                    """INSERT INTO billing_accounts
                       (owner_user_id, provider, external_customer_ref, subscription_id, status,
                        current_period_end, provider_event_created, created_at, updated_at, catalog_id)
                       VALUES(?,'stripe',?,?,?,?,?,?,?,'free')
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
                        owner, customer if isinstance(customer, str) else None,
                        subscription_id if isinstance(subscription_id, str) else None,
                        status or "none", period_end, created, current, current,
                    ),
                )
        connection.execute(
            """INSERT INTO billing_webhook_events
               (event_id, event_type, received_at, payload_sha256, processed_at)
               VALUES(?,?,?,?,?)""",
            (event_id, event_type, current, digest, current),
        )
        connection.commit()
    return {"status": "accepted", "duplicate": False}
