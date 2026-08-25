import os
import sqlite3
import time
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

init_notification_db()
