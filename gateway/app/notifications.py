"""Authenticated native push delivery and attention transition policy.

The gateway is the source of truth for native delivery.  The Expo client only
registers a real Expo token and never turns an attention poll into a pretend
background notification.  Web clients continue to consume the transition
feed and use the browser Notification API while an eligible tab is open.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.db import DB_PATH

NOTIFICATION_MODES = ("restricted", "moderate", "full")
DEFAULT_NOTIFICATION_MODE = "moderate"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
MAX_PUSH_ATTEMPTS = 3

# These are deliberately policy categories, not execution permissions.  A
# mode can change alert volume only; it never authorizes a command.
RESTRICTED_KINDS = frozenset({"captain_question", "blocker"})
MODERATE_KINDS = RESTRICTED_KINDS | frozenset({"pr_ready", "milestone", "stall", "failure"})
FULL_KINDS = frozenset({"captain_question", "blocker", "stall", "failure", "completion", "consequential_decision"})
KNOWN_PLATFORMS = frozenset({"ios", "android", "native"})


def init_notification_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            user_id TEXT PRIMARY KEY,
            push_token TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'ios',
            updated_at INTEGER NOT NULL,
            revoked_at INTEGER,
            timezone_offset_minutes INTEGER
        )
        """)
        # Existing beta databases predate revoked_at.  Keep upgrades additive
        # and idempotent so a deployment does not lose a registered device.
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(push_tokens)")}
        if "revoked_at" not in columns:
            cursor.execute("ALTER TABLE push_tokens ADD COLUMN revoked_at INTEGER")
        if "timezone_offset_minutes" not in columns:
            cursor.execute("ALTER TABLE push_tokens ADD COLUMN timezone_offset_minutes INTEGER")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_state (
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            delivered INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, item_id)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            user_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            quiet_start INTEGER,
            quiet_end INTEGER,
            mode TEXT NOT NULL DEFAULT 'moderate',
            updated_at INTEGER
        )
        """)
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(notification_preferences)")}
        if "mode" not in columns:
            cursor.execute("ALTER TABLE notification_preferences ADD COLUMN mode TEXT NOT NULL DEFAULT 'moderate'")
        if "updated_at" not in columns:
            cursor.execute("ALTER TABLE notification_preferences ADD COLUMN updated_at INTEGER")
        conn.commit()
    finally:
        conn.close()


def _validate_push_token(push_token: str) -> str:
    if not isinstance(push_token, str) or not push_token.strip():
        raise ValueError("A push token is required.")
    token = push_token.strip()
    # Gateway delivery uses Expo's authenticated push service.  Do not accept
    # arbitrary text or a local notification identifier as a server token.
    if not (token.startswith("ExponentPushToken[") and token.endswith("]") and len(token) > 19):
        raise ValueError("Expected a real Expo push token.")
    return token


def register_push_token(user_id: str, push_token: str, platform: str = "ios", timezone_offset_minutes: Optional[int] = None) -> Dict[str, Any]:
    token = _validate_push_token(push_token)
    platform = (platform or "ios").lower().strip()
    if platform not in KNOWN_PLATFORMS:
        raise ValueError("Unsupported native push platform.")
    if timezone_offset_minutes is not None and not -840 <= timezone_offset_minutes <= 840:
        raise ValueError("timezone_offset_minutes must be between -840 and 840.")
    init_notification_db()
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO push_tokens (user_id, push_token, platform, updated_at, revoked_at, timezone_offset_minutes)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              push_token=excluded.push_token, platform=excluded.platform,
              updated_at=excluded.updated_at, revoked_at=NULL,
              timezone_offset_minutes=excluded.timezone_offset_minutes
        """, (user_id, token, platform, now, timezone_offset_minutes))
    return {"status": "registered", "platform": platform}


def revoke_push_token(user_id: str, push_token: Optional[str] = None) -> Dict[str, Any]:
    init_notification_db()
    with sqlite3.connect(DB_PATH) as conn:
        if push_token:
            conn.execute("UPDATE push_tokens SET revoked_at=? WHERE user_id=? AND push_token=?", (int(time.time()), user_id, push_token.strip()))
        else:
            conn.execute("UPDATE push_tokens SET revoked_at=? WHERE user_id=?", (int(time.time()), user_id))
    return {"status": "revoked"}


def get_registered_push_token(user_id: str) -> Optional[Dict[str, Any]]:
    init_notification_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT push_token, platform, timezone_offset_minutes FROM push_tokens WHERE user_id=? AND revoked_at IS NULL", (user_id,)).fetchone()
    return {"push_token": row[0], "platform": row[1], "timezone_offset_minutes": row[2]} if row else None


def list_registered_push_users() -> List[str]:
    """Return users eligible for the gateway's background reconciler."""
    init_notification_db()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT user_id FROM push_tokens WHERE revoked_at IS NULL AND push_token != ''").fetchall()
    return [str(row[0]) for row in rows]


def registered_local_hour(user_id: str) -> int:
    registered = get_registered_push_token(user_id)
    offset = registered.get("timezone_offset_minutes") if registered else None
    # JS Date#getTimezoneOffset is UTC minus local time, hence subtraction.
    local = datetime.now(timezone.utc) - timedelta(minutes=int(offset or 0))
    return local.hour


async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send one remote push, retrying transient Expo failures.

    A successful HTTP response is not enough: Expo's JSON ticket must also be
    ``ok``. Invalid-token responses revoke the token so a dead device cannot
    cause an endless retry loop.
    """
    registered = get_registered_push_token(user_id)
    if not registered:
        return {"status": "skipped", "reason": "No active native push token registered for user"}
    token = registered["push_token"]
    payload = {"to": token, "sound": "default", "title": title, "body": body, "data": data or {}}
    last_error = "Push provider unavailable"
    for attempt in range(MAX_PUSH_ATTEMPTS):
        try:
            timeout = httpx.Timeout(10.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(os.getenv("MAGISTRATE_EXPO_PUSH_URL", EXPO_PUSH_URL), json=payload)
            try:
                result = response.json()
            except Exception:
                result = {}
            ticket = result.get("data", result) if isinstance(result, dict) else {}
            if isinstance(ticket, list):
                ticket = ticket[0] if ticket else {}
            provider_status = ticket.get("status") if isinstance(ticket, dict) else None
            if response.is_success and provider_status == "ok":
                return {"status": "sent", "attempts": attempt + 1, "response": result}
            detail = ticket.get("message") if isinstance(ticket, dict) else None
            last_error = str(detail or f"Push provider returned HTTP {response.status_code}")
            error_type = ticket.get("details", {}).get("error") if isinstance(ticket, dict) and isinstance(ticket.get("details"), dict) else None
            if error_type in {"DeviceNotRegistered", "InvalidCredentials"}:
                revoke_push_token(user_id, token)
                return {"status": "revoked", "detail": last_error}
            if response.status_code < 500 and response.status_code != 429:
                return {"status": "error", "attempts": attempt + 1, "detail": last_error}
        except Exception as exc:
            last_error = str(exc)
        if attempt + 1 < MAX_PUSH_ATTEMPTS:
            # A short bounded backoff keeps request latency predictable while
            # allowing a transient provider/network failure to recover.
            import asyncio
            await asyncio.sleep(0.25 * (2 ** attempt))
    return {"status": "error", "attempts": MAX_PUSH_ATTEMPTS, "detail": last_error}


def _fingerprint(item: Dict[str, Any]) -> str:
    material = {
        "kind": item.get("notification_kind"),
        "revision": item.get("revision"),
        "title": item.get("title"),
        "subtitle": item.get("subtitle"),
        "status": item.get("status"),
        "url": item.get("url"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _mode_for_kind(mode: str, kind: Optional[str], consequential: bool = False) -> bool:
    if mode == "restricted":
        return kind in RESTRICTED_KINDS
    if mode == "full":
        # A review-ready PR is normally moderate-volume; an explicit merge
        # decision is consequential and remains visible in full mode.
        return kind in FULL_KINDS or (kind == "pr_ready" and consequential)
    return kind in MODERATE_KINDS


def _quiet(local_hour: Optional[int], quiet_start: Optional[int], quiet_end: Optional[int]) -> bool:
    if local_hour is None or quiet_start is None or quiet_end is None:
        return False
    return (quiet_start <= local_hour < quiet_end) if quiet_start < quiet_end else (local_hour >= quiet_start or local_hour < quiet_end)


def get_notification_preferences(user_id: str) -> Dict[str, Any]:
    init_notification_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT enabled, quiet_start, quiet_end, mode FROM notification_preferences WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"enabled": True, "quiet_start": None, "quiet_end": None, "mode": DEFAULT_NOTIFICATION_MODE}
    mode = row["mode"] if row["mode"] in NOTIFICATION_MODES else DEFAULT_NOTIFICATION_MODE
    return {"enabled": bool(row["enabled"]), "quiet_start": row["quiet_start"], "quiet_end": row["quiet_end"], "mode": mode}


def reconcile_notification_events(
    user_id: str,
    attention_items: List[Dict[str, Any]],
    foreground: bool = False,
    local_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """Reconcile actionable transitions without losing suppressed transitions.

    Filtering happens before state creation.  Consequently a mode change can
    surface an existing item exactly once, and quiet hours defer rather than
    discard it. ``foreground`` remains supported for the web fallback contract;
    server push callers always reconcile with ``foreground=False``.
    """
    init_notification_db()
    preferences = get_notification_preferences(user_id)
    mode = preferences["mode"]
    actionable = {
        str(item["id"]): item for item in attention_items
        if item.get("requires_action") is True and _mode_for_kind(mode, item.get("notification_kind"), bool(item.get("consequential")))
    }
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = {row["item_id"]: row for row in conn.execute("SELECT * FROM notification_state WHERE user_id=?", (user_id,)).fetchall()}
        for item_id, row in existing.items():
            if item_id not in actionable and row["active"]:
                conn.execute("UPDATE notification_state SET active=0, delivered=1, updated_at=? WHERE user_id=? AND item_id=?", (now, user_id, item_id))
        pending: List[Dict[str, Any]] = []
        for item_id, item in actionable.items():
            fingerprint = _fingerprint(item)
            row = existing.get(item_id)
            changed = row is None or row["fingerprint"] != fingerprint or not row["active"]
            if changed:
                conn.execute("""
                    INSERT INTO notification_state(user_id,item_id,fingerprint,active,delivered,updated_at)
                    VALUES(?,?,?,?,0,?)
                    ON CONFLICT(user_id,item_id) DO UPDATE SET
                      fingerprint=excluded.fingerprint, active=1, delivered=0, updated_at=excluded.updated_at
                """, (user_id, item_id, fingerprint, 1, now))
            delivered = False if changed else bool(row["delivered"])
            if not delivered:
                pending.append(item)
        quiet = _quiet(local_hour, preferences["quiet_start"], preferences["quiet_end"])
        if foreground and pending:
            conn.executemany("UPDATE notification_state SET delivered=1 WHERE user_id=? AND item_id=? AND active=1", [(user_id, str(item["id"])) for item in pending])
            pending = []
        conn.commit()
    return {
        "events": pending if preferences["enabled"] and not quiet else [],
        "enabled": preferences["enabled"],
        "mode": mode,
        "quiet": quiet,
        "suppressed_foreground": foreground,
    }


async def dispatch_notification_events(
    user_id: str,
    attention_items: List[Dict[str, Any]],
    local_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """Deliver each newly reconciled event remotely, once per fingerprint."""
    result = reconcile_notification_events(user_id, attention_items, foreground=False, local_hour=local_hour)
    events = list(result["events"])
    registered = get_registered_push_token(user_id)
    if not registered or not events:
        return {**result, "delivery": "web-or-in-app" if events else "none"}
    delivered: List[str] = []
    failures: List[Dict[str, Any]] = []
    for event in events:
        kind = event.get("notification_kind")
        title = "Your answer is needed" if kind in {"captain_question", "consequential_decision", "blocker"} else event.get("title", "Magistrate attention")
        outcome = await send_push_notification(
            user_id,
            title,
            event.get("subtitle") or "An item needs your attention.",
            {"url": event.get("deep_link") or event.get("url", "/attention"), "item_id": event.get("id"), "notification_kind": kind},
        )
        if outcome.get("status") == "sent":
            delivered.append(str(event["id"]))
        else:
            failures.append({"id": event.get("id"), "status": outcome.get("status"), "detail": outcome.get("detail")})
    acknowledge_notification_events(user_id, delivered)
    # Native clients must not manufacture a local notification from this feed.
    # Failed sends remain in the in-app fallback and are retried on the next
    # reconciliation; successful sends are already remote pushes.
    remaining = [event for event in events if str(event["id"]) not in delivered]
    return {**result, "events": remaining, "delivery": "sent" if not failures else "partial", "failures": failures}


def acknowledge_notification_events(user_id: str, item_ids: List[str]) -> None:
    init_notification_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("UPDATE notification_state SET delivered=1 WHERE user_id=? AND item_id=? AND active=1", [(user_id, item_id) for item_id in item_ids])


def update_notification_preferences(
    user_id: str,
    enabled: bool,
    quiet_start: Optional[int],
    quiet_end: Optional[int],
    mode: str = DEFAULT_NOTIFICATION_MODE,
) -> Dict[str, Any]:
    if mode not in NOTIFICATION_MODES:
        raise ValueError("mode must be restricted, moderate, or full")
    if (quiet_start is None) != (quiet_end is None):
        raise ValueError("quiet_start and quiet_end must both be set or both omitted")
    if any(hour is not None and not 0 <= hour <= 23 for hour in (quiet_start, quiet_end)):
        raise ValueError("quiet hours must be between 0 and 23")
    init_notification_db()
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO notification_preferences(user_id,enabled,quiet_start,quiet_end,mode,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, quiet_start=excluded.quiet_start,
              quiet_end=excluded.quiet_end, mode=excluded.mode, updated_at=excluded.updated_at
        """, (user_id, int(enabled), quiet_start, quiet_end, mode, now))
    return {"enabled": enabled, "quiet_start": quiet_start, "quiet_end": quiet_end, "mode": mode}


init_notification_db()
