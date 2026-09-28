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
import re
import sqlite3
from app.persistence import connect
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.db import DB_PATH
from app.push_receipts import PushDeliveryStore, install_schema as install_push_receipts

NOTIFICATION_MODES = ("restricted", "moderate", "full")
DEFAULT_NOTIFICATION_MODE = "moderate"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
MAX_PUSH_ATTEMPTS = 3

# These are deliberately policy categories, not execution permissions.  A
# mode can change alert volume only; it never authorizes a command.
ACCOUNT_ATTENTION_KINDS = frozenset({"budget", "credit", "repository_disconnected", "payment_issue"})
RESTRICTED_KINDS = frozenset({"captain_question", "blocker", "consequential_decision"}) | ACCOUNT_ATTENTION_KINDS
MODERATE_KINDS = RESTRICTED_KINDS | frozenset({"pr_ready", "milestone", "stall", "failure", "completion"})
FULL_KINDS = frozenset({"captain_question", "blocker", "stall", "failure", "completion", "consequential_decision"}) | ACCOUNT_ATTENTION_KINDS
# A terminal outcome can be useful without demanding a captain action. It may
# be pushed once, but the client will not turn it into an unread Attention dot.
INFORMATIONAL_KINDS = frozenset({"completion", "failure"})
KNOWN_PLATFORMS = frozenset({"ios", "android", "native"})


def init_notification_db() -> None:
    conn = connect(DB_PATH)
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
            viewed INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, item_id)
        )
        """)
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(notification_state)")}
        if "viewed" not in columns:
            cursor.execute("ALTER TABLE notification_state ADD COLUMN viewed INTEGER NOT NULL DEFAULT 0")
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
        install_push_receipts(conn)
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
    with connect(DB_PATH) as conn:
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
    with connect(DB_PATH) as conn:
        if push_token:
            conn.execute("UPDATE push_tokens SET revoked_at=? WHERE user_id=? AND push_token=?", (int(time.time()), user_id, push_token.strip()))
        else:
            conn.execute("UPDATE push_tokens SET revoked_at=? WHERE user_id=?", (int(time.time()), user_id))
    return {"status": "revoked"}


def get_registered_push_token(user_id: str) -> Optional[Dict[str, Any]]:
    init_notification_db()
    enabled_value = os.getenv("MAGISTRATE_FRIEND_BETA_ENABLED", "false").strip().lower()
    friend_enabled = enabled_value in {"1", "true", "yes", "on"}
    now = int(time.time())
    with connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT push_token, platform, timezone_offset_minutes FROM push_tokens WHERE user_id=? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()
        known_grant = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='friend_beta_access_grants'",
        ).fetchone() and conn.execute(
            "SELECT 1 FROM friend_beta_access_grants WHERE user_id = ? LIMIT 1", (user_id,),
        ).fetchone()
        active_grant = known_grant and friend_enabled and conn.execute(
            """SELECT 1 FROM friend_beta_access_grants
               WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ? LIMIT 1""",
            (user_id, now),
        ).fetchone()
    if known_grant and not active_grant:
        return None
    return {"push_token": row[0], "platform": row[1], "timezone_offset_minutes": row[2]} if row else None


def list_registered_push_users() -> List[str]:
    """Return users eligible for background delivery, excluding closed grants."""
    init_notification_db()
    enabled_value = os.getenv("MAGISTRATE_FRIEND_BETA_ENABLED", "false").strip().lower()
    friend_enabled = enabled_value in {"1", "true", "yes", "on"}
    now = int(time.time())
    with connect(DB_PATH) as conn:
        has_grants = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='friend_beta_access_grants'",
        ).fetchone()
        if not has_grants:
            rows = conn.execute(
                "SELECT user_id FROM push_tokens WHERE revoked_at IS NULL AND push_token != ''",
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.user_id FROM push_tokens AS p
                   WHERE p.revoked_at IS NULL AND p.push_token != ''
                     AND (
                       NOT EXISTS (
                         SELECT 1 FROM friend_beta_access_grants AS known
                         WHERE known.user_id = p.user_id
                       )
                       OR (? = 1 AND EXISTS (
                         SELECT 1 FROM friend_beta_access_grants AS active
                         WHERE active.user_id = p.user_id
                           AND active.revoked_at IS NULL AND active.expires_at > ?
                       ))
                     )""",
                (1 if friend_enabled else 0, now),
            ).fetchall()
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
    *, expected_token: Optional[str] = None,
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
    if expected_token is not None and expected_token != token:
        return {"status": "skipped", "detail": "Push registration changed"}
    payload = {"to": token, "sound": "default", "title": title, "body": body, "data": data or {}}
    last_error = "Push provider unavailable"
    for attempt in range(MAX_PUSH_ATTEMPTS):
        try:
            timeout = httpx.Timeout(10.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
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
                ticket_id = ticket.get("id")
                if isinstance(ticket_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", ticket_id):
                    return {"status": "accepted", "attempts": attempt + 1, "ticket_id": ticket_id}
                return {"status": "error", "detail": "Push ticket identity missing"}
            last_error = "Push provider rejected notification"
            error_type = ticket.get("details", {}).get("error") if isinstance(ticket, dict) and isinstance(ticket.get("details"), dict) else None
            if error_type == "DeviceNotRegistered":
                revoke_push_token(user_id, token)
                return {"status": "revoked", "detail": last_error}
            if response.status_code < 500 and response.status_code != 429:
                return {"status": "error", "attempts": attempt + 1, "detail": last_error}
        except Exception:
            last_error = "Push provider unavailable"
        if attempt + 1 < MAX_PUSH_ATTEMPTS:
            # A short bounded backoff keeps request latency predictable while
            # allowing a transient provider/network failure to recover.
            import asyncio
            await asyncio.sleep(0.25 * (2 ** attempt))
    return {"status": "error", "attempts": MAX_PUSH_ATTEMPTS, "detail": last_error, "retryable": True}


async def reconcile_push_receipts() -> None:
    """Bounded notification-only write-side reconciliation; never a runtime read.

    An Expo receipt confirms handoff to APNs/FCM, not device arrival or viewing.
    Missing/malformed responses stay pending with bounded backoff until expiry.
    """
    init_notification_db()
    store = PushDeliveryStore(DB_PATH)
    deliveries = store.claim_receipts()
    if not deliveries:
        return
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", os.getenv("MAGISTRATE_EXPO_PUSH_RECEIPTS_URL", EXPO_RECEIPTS_URL),
                                     json={"ids": [row["ticket_id"] for row in deliveries]}) as response:
                if not response.is_success:
                    return
                body = bytearray()
                async for part in response.aiter_bytes():
                    if len(body) + len(part) > 128 * 1024:
                        return
                    body.extend(part)
        payload = json.loads(body)
        receipts = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(receipts, dict):
            return
        for delivery in deliveries:
            receipt = receipts.get(delivery["ticket_id"])
            if not isinstance(receipt, dict) or receipt.get("status") not in {"ok", "error"}:
                continue
            details = receipt.get("details")
            invalid = isinstance(details, dict) and details.get("error") == "DeviceNotRegistered"
            store.receipt(delivery, delivered=receipt["status"] == "ok", invalid_token=invalid)
    except Exception:
        # No provider error body, token, identity or exception text is observable.
        return


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
    with connect(DB_PATH) as conn:
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
        if (item.get("requires_action") is True or item.get("notification_kind") in INFORMATIONAL_KINDS)
        and _mode_for_kind(mode, item.get("notification_kind"), bool(item.get("consequential")))
    }
    now = int(time.time())
    with connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = {row["item_id"]: row for row in conn.execute("SELECT * FROM notification_state WHERE user_id=?", (user_id,)).fetchall()}
        for item_id, row in existing.items():
            if item_id not in actionable and row["active"]:
                conn.execute("UPDATE notification_state SET active=0, delivered=1, viewed=1, updated_at=? WHERE user_id=? AND item_id=?", (now, user_id, item_id))
        pending: List[Dict[str, Any]] = []
        for item_id, item in actionable.items():
            fingerprint = _fingerprint(item)
            row = existing.get(item_id)
            changed = row is None or row["fingerprint"] != fingerprint or not row["active"]
            if changed:
                conn.execute("""
                    INSERT INTO notification_state(user_id,item_id,fingerprint,active,delivered,viewed,updated_at)
                    VALUES(?,?,?,?,0,0,?)
                    ON CONFLICT(user_id,item_id) DO UPDATE SET
                      fingerprint=excluded.fingerprint, active=1, delivered=0, viewed=0, updated_at=excluded.updated_at
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


def _unread_events(user_id: str, attention_items: List[Dict[str, Any]], preferences: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return active, captain-relevant items not yet viewed by the captain.

    Delivery and viewing are intentionally separate: a remote push can be
    accepted while the item remains visible as unread in the app.
    """
    if not preferences["enabled"]:
        return []
    items_by_id = {str(item["id"]): item for item in attention_items}
    init_notification_db()
    with connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT item_id FROM notification_state WHERE user_id=? AND active=1 AND viewed=0",
            (user_id,),
        ).fetchall()
    return [items_by_id[row[0]] for row in rows if row[0] in items_by_id]


def mark_notification_events_delivered(user_id: str, item_ids: List[str]) -> None:
    """Record provider/browser delivery without clearing the unread indicator."""
    init_notification_db()
    with connect(DB_PATH) as conn:
        conn.executemany(
            "UPDATE notification_state SET delivered=1 WHERE user_id=? AND item_id=? AND active=1",
            [(user_id, item_id) for item_id in item_ids],
        )


def _safe_deep_link(event: Dict[str, Any]) -> str:
    """Keep notification navigation inside the public app route vocabulary."""
    route = str(event.get("deep_link") or event.get("url") or "")
    parsed = urlparse(route)
    query = parse_qs(parsed.query)
    if parsed.scheme or parsed.netloc:
        return "/attention?overview=true"
    item = query.get("item", [""])[0]
    if parsed.path == "/attention" and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", item):
        return "/attention?" + urlencode({"item": item})
    if parsed.path == "/attention" and query.get("overview") == ["true"]:
        return "/attention?overview=true"
    agent_id = query.get("agentId", [""])[0]
    if parsed.path == "/chat" and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", agent_id):
        return "/chat?" + urlencode({"agentId": agent_id})
    if parsed.path == "/chat" and query.get("shortcut") == ["running"]:
        return "/chat?shortcut=running"
    number = query.get("number", [""])[0]
    if parsed.path == "/pr-detail" and re.fullmatch(r"[1-9][0-9]{0,8}", number):
        return "/pr-detail?" + urlencode({"number": number})
    return "/attention?overview=true"


def _safe_push_copy(kind: Optional[str]) -> tuple[str, str]:
    """Visible push copy never exposes repository, task, provider, or billing identifiers."""
    if kind in {"captain_question", "consequential_decision", "blocker"}:
        return "Your answer is needed", "Open Magistrate to review an item that needs your attention."
    if kind == "completion":
        return "Work completed", "Open Magistrate to review the result."
    if kind in {"failure", "stall"}:
        return "Work needs attention", "Open Magistrate to review what happened."
    if kind in {"budget", "credit"}:
        return "Usage needs attention", "Open Magistrate to review account usage."
    if kind == "repository_disconnected":
        return "A repository needs reconnecting", "Open Magistrate to review the connection."
    if kind == "payment_issue":
        return "Billing needs attention", "Open Magistrate to review billing."
    if kind == "pr_ready":
        return "A pull request is ready", "Open Magistrate to review it."
    return "Magistrate attention", "Open Magistrate to review the update."


def _push_intent_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return a versioned, app-owned target alongside the legacy URL field."""
    route = _safe_deep_link(event)
    parsed = urlparse(route)
    query = parse_qs(parsed.query)
    target_type = "attention"
    target_id = str(event.get("id") or "")
    if parsed.path == "/chat" and query.get("agentId"):
        target_type, target_id = "agent", query["agentId"][0]
    elif parsed.path == "/pr-detail" and query.get("number"):
        target_type, target_id = "pull-request", query["number"][0]
    elif parsed.path == "/attention" and query.get("item"):
        target_id = query["item"][0]
    payload = {
        "intent_version": 1,
        "target_type": target_type,
        "target_id": target_id,
        "route": route,
    }
    action = event.get("action") if isinstance(event.get("action"), dict) else None
    if action and isinstance(action.get("action_key"), str):
        # The key is an opaque server-issued handle, not authority by itself;
        # the Gateway still revalidates the live item and owner confirmation.
        payload["action_key"] = action["action_key"]
    return payload


async def dispatch_notification_events(
    user_id: str,
    attention_items: List[Dict[str, Any]],
    local_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """Deliver each newly reconciled event remotely, once per fingerprint."""
    result = reconcile_notification_events(user_id, attention_items, foreground=False, local_hour=local_hour)
    events = list(result["events"])
    registered = get_registered_push_token(user_id)
    unread = _unread_events(user_id, attention_items, get_notification_preferences(user_id))
    if not registered or not events:
        return {**result, "unread": unread, "delivery": "web-or-in-app" if events else "none"}
    accepted: List[str] = []
    failures: List[Dict[str, Any]] = []
    store = PushDeliveryStore(DB_PATH)
    for event in events:
        claim = store.claim_send(user_id, str(event["id"]), _fingerprint(event), registered["push_token"])
        if claim is None:
            continue  # Pending, terminal or leased: never send again on a read.
        kind = event.get("notification_kind")
        title, body = _safe_push_copy(kind)
        safe_route = _safe_deep_link(event)
        outcome = await send_push_notification(
            user_id,
            title,
            body,
            {**_push_intent_data(event), "url": safe_route, "item_id": event.get("id"), "notification_kind": kind},
            expected_token=registered["push_token"],
        )
        if outcome.get("status") == "accepted":
            try:
                store.accepted(claim, outcome["ticket_id"])
                accepted.append(str(event["id"]))
            except sqlite3.IntegrityError:
                store.send_failed(claim, retryable=False)
                failures.append({"id": event.get("id"), "status": "error", "detail": "Conflicting push ticket"})
        else:
            store.send_failed(claim, retryable=outcome.get("retryable") is True)
            failures.append({"id": event.get("id"), "status": outcome.get("status"), "detail": outcome.get("detail")})
    # Ticket acceptance is not receipt delivery; keep in-app fallback/unread.
    # Native clients must never synthesize local notifications from this feed.
    unread = _unread_events(user_id, attention_items, get_notification_preferences(user_id))
    return {**result, "unread": unread, "delivery": "accepted" if accepted and not failures else "pending" if not failures else "partial", "failures": failures}


def acknowledge_notification_events(user_id: str, item_ids: List[str]) -> None:
    """Acknowledge items as viewed; this is the unread-dot clear operation."""
    init_notification_db()
    with connect(DB_PATH) as conn:
        conn.executemany("UPDATE notification_state SET delivered=1, viewed=1 WHERE user_id=? AND item_id=? AND active=1", [(user_id, item_id) for item_id in item_ids])


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
    with connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO notification_preferences(user_id,enabled,quiet_start,quiet_end,mode,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, quiet_start=excluded.quiet_start,
              quiet_end=excluded.quiet_end, mode=excluded.mode, updated_at=excluded.updated_at
        """, (user_id, int(enabled), quiet_start, quiet_end, mode, now))
    return {"enabled": enabled, "quiet_start": quiet_start, "quiet_end": quiet_end, "mode": mode}


init_notification_db()
