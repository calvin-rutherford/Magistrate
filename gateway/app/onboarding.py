"""Durable, truth-derived first-run state for provider-backed accounts."""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from fastapi import HTTPException

from app import db
from app.billing import billing_available
from app.persistence import connect
from app.providers.github import GitHubProviderAdapter


def ensure_provider_onboarding(connection: sqlite3.Connection, user_id: str, now: int) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO account_onboarding
           (user_id, welcome_completed_at, created_at, updated_at)
           VALUES(?, NULL, ?, ?)""",
        (user_id, now, now),
    )


def acknowledge_welcome(user_id: str) -> None:
    db.init_db()
    now = int(time.time())
    with connect(db.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute(
            "SELECT 1 FROM account_onboarding WHERE user_id = ?", (user_id,),
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=409, detail="This account does not require provider onboarding.")
        connection.execute(
            """UPDATE account_onboarding
               SET welcome_completed_at = COALESCE(welcome_completed_at, ?), updated_at = ?
               WHERE user_id = ?""",
            (now, now, user_id),
        )


def _state(connection: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    progress = connection.execute(
        "SELECT welcome_completed_at FROM account_onboarding WHERE user_id = ?", (user_id,),
    ).fetchone()
    profile = connection.execute(
        "SELECT name FROM user_profiles WHERE user_id = ?", (user_id,),
    ).fetchone()
    github = connection.execute(
        """SELECT account.status, credential.expires_at
           FROM connected_accounts AS account
           JOIN oauth_credentials AS credential ON credential.connected_account_id = account.id
           WHERE account.user_id = ? AND account.provider = 'github'
             AND account.account_kind = 'oauth'
           ORDER BY account.updated_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    billing = connection.execute(
        "SELECT status, current_period_end, external_customer_ref FROM billing_accounts WHERE owner_user_id = ?",
        (user_id,),
    ).fetchone()
    now = int(time.time())
    provider_account = progress is not None
    welcome_complete = not provider_account or progress["welcome_completed_at"] is not None
    profile_complete = bool(profile and isinstance(profile["name"], str) and profile["name"].strip())
    github_adapter = GitHubProviderAdapter()
    github_available = bool(github_adapter.is_configured())
    github_complete = bool(
        github_available and github and github["status"] == "connected"
        and (github["expires_at"] is None or int(github["expires_at"]) > now)
    )
    billing_status = str(billing["status"]) if billing else "none"
    billing_complete = billing_status in {"active", "trialing"}
    required = (
        not profile_complete if not provider_account else
        not (welcome_complete and profile_complete and github_complete and billing_complete)
    )
    next_step = None
    if required:
        next_step = (
            "welcome" if provider_account and not welcome_complete else
            "profile" if not profile_complete else
            "github" if provider_account and not github_complete else
            "billing"
        )
    return {
        "schema_version": "account-onboarding.v1",
        "required": required,
        "next_step": next_step,
        "steps": {
            "welcome": {"complete": welcome_complete},
            "profile": {"complete": profile_complete},
            "github": {
                "complete": github_complete,
                "available": github_available,
                "unavailable_reason": None if github_available else github_adapter.unavailable_reason(),
            },
            "billing": {
                "complete": billing_complete,
                "available": billing_available(),
                "status": billing_status,
                "current_period_end": int(billing["current_period_end"]) if billing and billing["current_period_end"] is not None else None,
                "customer_portal_available": bool(billing and billing["external_customer_ref"]),
            },
        },
    }


def onboarding_state(user_id: str) -> dict[str, Any]:
    db.init_db()
    with connect(db.DB_PATH) as connection:
        return _state(connection, user_id)


def onboarding_required(user_id: str) -> bool:
    return bool(onboarding_state(user_id)["required"])


def onboarding_required_in_transaction(connection: sqlite3.Connection, user_id: str) -> bool:
    return bool(_state(connection, user_id)["required"])
