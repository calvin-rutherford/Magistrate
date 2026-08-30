"""Private, authenticated chat upload storage."""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app import db

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_ALLOWED_EXACT_TYPES = {
    'application/pdf', 'application/json', 'application/zip', 'application/gzip',
    'text/csv', 'text/plain', 'text/markdown', 'application/octet-stream',
}


def _root() -> Path:
    configured = os.getenv('MAGISTRATE_CHAT_UPLOAD_DIR', '').strip()
    root = Path(configured) if configured else Path(db.DB_PATH).parent / 'chat_uploads'
    root.mkdir(parents=True, exist_ok=True)
    return root


def init_upload_db() -> None:
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_uploads (
            upload_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, filename TEXT NOT NULL,
            media_type TEXT NOT NULL, size INTEGER NOT NULL, path TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )''')


def _safe_name(filename: str) -> str:
    name = Path(filename or 'upload').name
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)[:160]
    return name or 'upload'


def validate_media_type(media_type: Optional[str], filename: str) -> str:
    value = (media_type or 'application/octet-stream').lower().split(';', 1)[0].strip()
    if value.startswith('image/'):
        return value
    if value == 'application/octet-stream' and Path(filename).suffix.lower() not in {'.txt', '.md', '.json', '.csv', '.pdf', '.zip', '.gz', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}:
        raise ValueError('This file type is not supported.')
    if value in _ALLOWED_EXACT_TYPES:
        return value
    raise ValueError('This file type is not supported.')


def save_upload(user_id: str, filename: str, media_type: Optional[str], content: bytes) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f'Files must be smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
    init_upload_db()
    upload_id = secrets.token_urlsafe(18)
    safe_name = _safe_name(filename)
    kind = validate_media_type(media_type, safe_name)
    destination = _root() / f'{upload_id}-{safe_name}'
    destination.write_bytes(content)
    import time
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('INSERT INTO chat_uploads(upload_id,user_id,filename,media_type,size,path,created_at) VALUES(?,?,?,?,?,?,?)',
                     (upload_id, user_id, safe_name, kind, len(content), str(destination), int(time.time())))
    return {'upload_id': upload_id, 'filename': safe_name, 'media_type': kind, 'size': len(content)}


def get_upload(user_id: str, upload_id: str) -> Optional[dict[str, Any]]:
    init_upload_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM chat_uploads WHERE upload_id=? AND user_id=?', (upload_id, user_id)).fetchone()
    if not row or not Path(row['path']).is_file():
        return None
    return dict(row)
