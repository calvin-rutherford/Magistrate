import os
import sqlite3
import json
import base64
import time
from typing import Optional, Dict, Any, List
from cryptography.fernet import Fernet

DB_PATH = os.getenv('MAGISTRATE_DB_PATH', '/home/spectre/Magistrate/gateway/magistrate.db')
SECRET_KEY = os.getenv('MAGISTRATE_SECRET_KEY', 'magistrate_super_secret_fernet_key_32bytes_len=')

def _get_fernet() -> Fernet:
    key_bytes = SECRET_KEY.encode('utf-8')
    key_b64 = base64.urlsafe_b64encode(key_bytes.ljust(32)[:32])
    return Fernet(key_b64)

def encrypt_token(plain_token: str) -> str:
    if not plain_token:
        return ''
    f = _get_fernet()
    return f.encrypt(plain_token.encode('utf-8')).decode('utf-8')

def decrypt_token(cipher_token: str) -> str:
    if not cipher_token:
        return ''
    try:
        f = _get_fernet()
        return f.decrypt(cipher_token.encode('utf-8')).decode('utf-8')
    except Exception:
        return cipher_token

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        avatar_url TEXT,
        bio TEXT,
        active_theme TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS connected_accounts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_user_id TEXT,
        provider_username TEXT,
        status TEXT NOT NULL,
        scopes TEXT,
        created_at INTEGER,
        updated_at INTEGER,
        FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS oauth_credentials (
        id TEXT PRIMARY KEY,
        connected_account_id TEXT NOT NULL,
        access_token_enc TEXT NOT NULL,
        refresh_token_enc TEXT,
        expires_at INTEGER,
        FOREIGN KEY(connected_account_id) REFERENCES connected_accounts(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS provider_capabilities (
        id TEXT PRIMARY KEY,
        connected_account_id TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        FOREIGN KEY(connected_account_id) REFERENCES connected_accounts(id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS voice_audit_events (
        event_id TEXT PRIMARY KEY,
        actor_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        move_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL,
        status TEXT NOT NULL,
        utterance_digest TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    ''')

    cursor.execute("SELECT user_id FROM user_profiles WHERE user_id = 'default_user'")
    if not cursor.fetchone():
        now = int(time.time())
        cursor.execute('''
        INSERT INTO user_profiles (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
        VALUES ('default_user', 'Spectre Operator', 'spectre@magistrate.io', '/uploads/avatars/default_avatar.png', 'Firstmate Master Operator', 'dusk-mountain', ?, ?)
        ''', (now, now))

    conn.commit()
    conn.close()

def get_profile(user_id: str = 'default_user') -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at FROM user_profiles WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'user_id': row[0],
            'name': row[1] or '',
            'email': row[2] or '',
            'avatar_url': row[3] or '/uploads/avatars/default_avatar.png',
            'bio': row[4] or '',
            'active_theme': row[5] or 'dusk-mountain',
            'created_at': row[6],
            'updated_at': row[7]
        }
    return {
        'user_id': user_id,
        'name': '',
        'email': '',
        'avatar_url': '/uploads/avatars/default_avatar.png',
        'bio': '',
        'active_theme': 'dusk-mountain',
        'created_at': int(time.time()),
        'updated_at': int(time.time())
    }

def update_profile(user_id: str = 'default_user', name: Optional[str] = None, email: Optional[str] = None, avatar_url: Optional[str] = None, bio: Optional[str] = None, active_theme: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time())

    curr = get_profile(user_id)
    new_name = name if name is not None else curr['name']
    new_email = email if email is not None else curr['email']
    new_avatar = avatar_url if avatar_url is not None else curr['avatar_url']
    new_bio = bio if bio is not None else curr['bio']
    new_theme = active_theme if active_theme is not None else curr.get('active_theme', 'dusk-mountain')

    cursor.execute('''
    INSERT INTO user_profiles (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        name=excluded.name,
        email=excluded.email,
        avatar_url=excluded.avatar_url,
        bio=excluded.bio,
        active_theme=excluded.active_theme,
        updated_at=excluded.updated_at
    ''', (user_id, new_name, new_email, new_avatar, new_bio, new_theme, now, now))

    conn.commit()
    conn.close()

    return get_profile(user_id)

def get_connected_accounts(user_id: str = 'default_user') -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT id, provider, provider_user_id, provider_username, status, scopes, updated_at
    FROM connected_accounts WHERE user_id = ?
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            'id': r[0],
            'provider': r[1],
            'provider_user_id': r[2],
            'provider_username': r[3],
            'status': r[4],
            'scopes': r[5].split(',') if r[5] else [],
            'updated_at': r[6]
        })
    return result

def upsert_connected_account(user_id: str, provider: str, provider_username: str, status: str = 'connected', scopes: List[str] = [], access_token: str = '') -> Dict[str, Any]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = int(time.time())

    account_id = f'{user_id}_{provider}'
    scopes_str = ','.join(scopes)

    cursor.execute('''
    INSERT INTO connected_accounts (id, user_id, provider, provider_user_id, provider_username, status, scopes, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        provider_username=excluded.provider_username,
        status=excluded.status,
        scopes=excluded.scopes,
        updated_at=excluded.updated_at
    ''', (account_id, user_id, provider, provider_username, provider_username, status, scopes_str, now, now))

    if access_token:
        cred_id = f'cred_{account_id}'
        enc_access = encrypt_token(access_token)
        cursor.execute('''
        INSERT INTO oauth_credentials (id, connected_account_id, access_token_enc, refresh_token_enc, expires_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token_enc=excluded.access_token_enc
        ''', (cred_id, account_id, enc_access, '', now + 86400 * 30))

    conn.commit()
    conn.close()

    return {
        'id': account_id,
        'provider': provider,
        'provider_username': provider_username,
        'status': status,
        'scopes': scopes
    }

def disconnect_account(user_id: str, provider: str) -> bool:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    account_id = f'{user_id}_{provider}'
    now = int(time.time())

    cursor.execute("UPDATE connected_accounts SET status = 'disconnected', updated_at = ? WHERE id = ?", (now, account_id))
    conn.commit()
    conn.close()
    return True


def record_voice_audit(
    *, event_id: str, actor_id: str, session_id: str, move_id: str,
    action: str, target: str, status: str, utterance_digest: str,
) -> None:
    """Persist a minimal Voice audit record; raw audio is never stored."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT OR REPLACE INTO voice_audit_events
        (event_id, actor_id, session_id, move_id, action, target, status, utterance_digest, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (event_id, actor_id, session_id, move_id, action, target, status, utterance_digest, int(time.time())))
    conn.commit()
    conn.close()


def get_voice_audit_events(actor_id: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    if session_id:
        rows = conn.execute('''
            SELECT event_id, move_id, action, target, status, utterance_digest, created_at
            FROM voice_audit_events WHERE actor_id = ? AND session_id = ? ORDER BY created_at
        ''', (actor_id, session_id)).fetchall()
    else:
        rows = conn.execute('''
            SELECT event_id, move_id, action, target, status, utterance_digest, created_at
            FROM voice_audit_events WHERE actor_id = ? ORDER BY created_at
        ''', (actor_id,)).fetchall()
    conn.close()
    return [
        {'event_id': row[0], 'move_id': row[1], 'action': row[2], 'target': row[3],
         'status': row[4], 'utterance_digest': row[5], 'created_at': row[6]}
        for row in rows
    ]

init_db()
print('Database initialized successfully at:', DB_PATH)
