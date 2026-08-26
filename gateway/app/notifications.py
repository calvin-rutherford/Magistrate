import os
import sqlite3
import time
import hashlib
import json
import httpx
from typing import Dict, Any, List, Optional
from app.db import DB_PATH

def init_notification_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS push_tokens (
        user_id TEXT PRIMARY KEY,
        push_token TEXT NOT NULL,
        platform TEXT DEFAULT 'ios',
        updated_at INTEGER
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notification_state (
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        delivered INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, item_id)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notification_preferences (
        user_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        quiet_start INTEGER,
        quiet_end INTEGER
    )
    ''')
    conn.commit()
    conn.close()

def register_push_token(user_id: str, push_token: str, platform: str = 'ios') -> Dict[str, Any]:
    init_notification_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time())
    cursor.execute('''
    INSERT INTO push_tokens (user_id, push_token, platform, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET push_token=excluded.push_token, updated_at=excluded.updated_at
    ''', (user_id, push_token, platform, now))
    conn.commit()
    conn.close()
    return {'status': 'registered', 'user_id': user_id, 'push_token': push_token}

async def send_push_notification(user_id: str, title: str, body: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    init_notification_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT push_token FROM push_tokens WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        return {'status': 'skipped', 'reason': 'No push token registered for user'}

    token = row[0]
    payload = {
        'to': token,
        'sound': 'default',
        'title': title,
        'body': body,
        'data': data or {}
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post('https://exp.host/--/api/v2/push/send', json=payload)
            return {'status': 'sent', 'response': res.json()}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}

ACTIONABLE_KINDS = {'captain_question', 'pr_ready'}

def _fingerprint(item: Dict[str, Any]) -> str:
    material = {
        'kind': item.get('notification_kind'),
        'revision': item.get('revision'),
        'title': item.get('title'),
        'subtitle': item.get('subtitle'),
        'status': item.get('status'),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

def reconcile_notification_events(
    user_id: str,
    attention_items: List[Dict[str, Any]],
    foreground: bool = False,
    local_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """Return unresolved transitions once; callers acknowledge after handling them.

    Repeated identical snapshots are deduped. A material revision reopens the event.
    Missing items resolve immediately. Foreground items are acknowledged as suppressed,
    because the captain can already see the live app rather than needing a later repeat.
    """
    init_notification_db()
    actionable = {
        str(item['id']): item for item in attention_items
        if item.get('requires_action') is True and item.get('notification_kind') in ACTIONABLE_KINDS
    }
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT enabled, quiet_start, quiet_end FROM notification_preferences WHERE user_id=?', (user_id,))
    pref = cursor.fetchone()
    enabled = bool(pref['enabled']) if pref else True
    quiet_start = pref['quiet_start'] if pref else None
    quiet_end = pref['quiet_end'] if pref else None
    now = int(time.time())

    cursor.execute('SELECT * FROM notification_state WHERE user_id=?', (user_id,))
    existing = {row['item_id']: row for row in cursor.fetchall()}
    for item_id, row in existing.items():
        if item_id not in actionable and row['active']:
            cursor.execute(
                'UPDATE notification_state SET active=0, delivered=1, updated_at=? WHERE user_id=? AND item_id=?',
                (now, user_id, item_id),
            )

    pending = []
    for item_id, item in actionable.items():
        fingerprint = _fingerprint(item)
        row = existing.get(item_id)
        changed = row is None or row['fingerprint'] != fingerprint or not row['active']
        if changed:
            cursor.execute('''
                INSERT INTO notification_state(user_id,item_id,fingerprint,active,delivered,updated_at)
                VALUES(?,?,?,?,0,?)
                ON CONFLICT(user_id,item_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint, active=1, delivered=0, updated_at=excluded.updated_at
            ''', (user_id, item_id, fingerprint, 1, now))
        delivered = False if changed else bool(row['delivered'])
        if not delivered:
            pending.append(item)

    quiet = False
    if local_hour is not None and quiet_start is not None and quiet_end is not None:
        quiet = (quiet_start <= local_hour < quiet_end) if quiet_start < quiet_end else (local_hour >= quiet_start or local_hour < quiet_end)

    if foreground and pending:
        cursor.executemany(
            'UPDATE notification_state SET delivered=1 WHERE user_id=? AND item_id=? AND active=1',
            [(user_id, str(item['id'])) for item in pending],
        )
        pending = []
    conn.commit()
    conn.close()
    return {'events': pending if enabled and not quiet else [], 'enabled': enabled, 'quiet': quiet, 'suppressed_foreground': foreground}

def acknowledge_notification_events(user_id: str, item_ids: List[str]) -> None:
    init_notification_db()
    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        'UPDATE notification_state SET delivered=1 WHERE user_id=? AND item_id=? AND active=1',
        [(user_id, item_id) for item_id in item_ids],
    )
    conn.commit()
    conn.close()

def update_notification_preferences(user_id: str, enabled: bool, quiet_start: Optional[int], quiet_end: Optional[int]) -> Dict[str, Any]:
    if (quiet_start is None) != (quiet_end is None):
        raise ValueError('quiet_start and quiet_end must both be set or both omitted')
    if any(hour is not None and not 0 <= hour <= 23 for hour in (quiet_start, quiet_end)):
        raise ValueError('quiet hours must be between 0 and 23')
    init_notification_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO notification_preferences(user_id,enabled,quiet_start,quiet_end) VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end
    ''', (user_id, int(enabled), quiet_start, quiet_end))
    conn.commit()
    conn.close()
    return {'enabled': enabled, 'quiet_start': quiet_start, 'quiet_end': quiet_end}

init_notification_db()
