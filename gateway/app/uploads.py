"""Private, authenticated chat upload storage.

Uploads are deliberately kept separate from the public avatar tree.  The chat
provider currently accepts a bounded attachment manifest alongside a prompt;
this module owns the bytes and the per-user ownership checks used by that
manifest.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from app import db

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 10
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
_SAFE_UPLOAD_ID = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
_SAFE_MESSAGE_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp'}
_ALLOWED_EXACT_TYPES = {
    'application/pdf', 'application/json', 'application/zip', 'application/gzip',
    'text/csv', 'text/plain', 'text/markdown',
}
_ALLOWED_OCTET_SUFFIXES = {'.txt', '.md', '.json', '.csv', '.pdf', '.zip', '.gz',
                           '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}


def _root() -> Path:
    configured = os.getenv('MAGISTRATE_CHAT_UPLOAD_DIR', '').strip()
    root = Path(configured) if configured else Path(db.DB_PATH).parent / 'chat_uploads'
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def init_upload_db() -> None:
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_uploads (
            upload_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, filename TEXT NOT NULL,
            media_type TEXT NOT NULL, size INTEGER NOT NULL, path TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_message_attachments (
            message_id TEXT NOT NULL, user_id TEXT NOT NULL, upload_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (message_id, upload_id),
            FOREIGN KEY(upload_id) REFERENCES chat_uploads(upload_id)
        )''')


def _safe_name(filename: str) -> str:
    # Path() only treats '/' as a separator on POSIX; normalize backslashes too
    # so a Windows client cannot smuggle a path component into the display name.
    name = (filename or 'upload').replace('\\', '/')
    name = Path(name).name
    name = re.sub(r'[\x00-\x1f\x7f]', '_', name)
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)[:160]
    name = name.strip('.') or 'upload'
    return name


def _normalized_type(media_type: Optional[str], filename: str) -> str:
    value = (media_type or 'application/octet-stream').lower().split(';', 1)[0].strip()
    suffix = Path(filename).suffix.lower()
    if value == 'application/octet-stream':
        if suffix not in _ALLOWED_OCTET_SUFFIXES:
            raise ValueError('This file type is not supported.')
        # Browsers/native pickers sometimes omit a MIME type. Resolve the
        # conservative extension allowlist to a real type before validating
        # content, rather than treating arbitrary bytes as octet-stream.
        value = {
            '.txt': 'text/plain', '.md': 'text/markdown', '.json': 'application/json',
            '.csv': 'text/csv', '.pdf': 'application/pdf', '.zip': 'application/zip',
            '.gz': 'application/gzip',
        }.get(suffix, 'application/octet-stream')
    if value not in _ALLOWED_IMAGE_TYPES and value not in _ALLOWED_EXACT_TYPES and value != 'application/octet-stream':
        raise ValueError('This file type is not supported.')
    return value


def _content_kind(content: bytes) -> Optional[str]:
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'image/webp'
    if content.startswith(b'BM'):
        return 'image/bmp'
    if content.startswith(b'%PDF-'):
        return 'application/pdf'
    if content.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
        return 'application/zip'
    if content.startswith(b'\x1f\x8b'):
        return 'application/gzip'
    return None


def validate_content(media_type: Optional[str], filename: str, content: bytes) -> str:
    """Validate the declared type against safe, bounded signatures/content.

    We do not execute or decompress uploads. Text is decoded only to reject
    binary payloads masquerading as text; known binary signatures must agree
    with the declared type. This is intentionally not an image understanding
    or virus-scanning feature.
    """
    safe_name = _safe_name(filename)
    kind = _normalized_type(media_type, safe_name)
    detected = _content_kind(content)
    if kind in _ALLOWED_IMAGE_TYPES:
        if detected != kind:
            raise ValueError('The file content does not match its image type.')
        return kind
    if detected and detected != kind and not (kind == 'application/octet-stream' and detected in {'application/zip', 'application/gzip'}):
        raise ValueError('The file content does not match its declared type.')
    if kind in {'application/pdf', 'application/zip', 'application/gzip'} and detected != kind:
        raise ValueError('The file content does not match its declared type.')
    if kind in {'text/plain', 'text/markdown', 'text/csv', 'application/json'}:
        if b'\x00' in content:
            raise ValueError('The file content is not valid text.')
        try:
            content.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('The file content is not valid UTF-8 text.') from exc
    if kind == 'application/json':
        try:
            json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('The file content is not valid JSON.') from exc
    return kind


def validate_media_type(media_type: Optional[str], filename: str) -> str:
    """Compatibility helper for callers that only need type validation."""
    return _normalized_type(media_type, _safe_name(filename))


def validate_upload_metadata(upload: dict[str, Any], filename: str, media_type: str, size: int) -> None:
    """Reject client metadata tampering before associating an upload."""
    if (_safe_name(filename) != upload['filename'] or media_type.lower().split(';', 1)[0].strip() != upload['media_type']
            or size != upload['size']):
        raise ValueError('Attachment metadata does not match the uploaded file.')


def save_upload(user_id: str, filename: str, media_type: Optional[str], content: bytes) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f'Files must be smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
    init_upload_db()
    safe_name = _safe_name(filename)
    kind = validate_content(media_type, safe_name, content)
    upload_id = secrets.token_urlsafe(18)
    destination = _root() / f'{upload_id}-{safe_name}'
    # Exclusive creation plus a random opaque id avoids symlink clobbering and
    # keeps user-controlled names out of the storage identity.
    with destination.open('xb') as handle:
        handle.write(content)
    os.chmod(destination, 0o600)
    try:
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute('INSERT INTO chat_uploads(upload_id,user_id,filename,media_type,size,path,created_at) VALUES(?,?,?,?,?,?,?)',
                         (upload_id, user_id, safe_name, kind, len(content), str(destination), int(time.time())))
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {'upload_id': upload_id, 'filename': safe_name, 'media_type': kind, 'size': len(content)}


def associate_uploads(user_id: str, message_id: str, upload_ids: list[str]) -> None:
    if not _SAFE_MESSAGE_ID.fullmatch(message_id):
        raise ValueError('Invalid chat message id.')
    if len(upload_ids) > MAX_UPLOAD_COUNT or any(not _SAFE_UPLOAD_ID.fullmatch(item) for item in upload_ids):
        raise ValueError('Invalid attachment reference.')
    init_upload_db()
    now = int(time.time())
    with sqlite3.connect(db.DB_PATH) as conn:
        for upload_id in upload_ids:
            row = conn.execute('SELECT 1 FROM chat_uploads WHERE upload_id=? AND user_id=?', (upload_id, user_id)).fetchone()
            if not row:
                raise ValueError('One or more attached files are unavailable.')
            conn.execute('INSERT OR IGNORE INTO chat_message_attachments(message_id,user_id,upload_id,created_at) VALUES(?,?,?,?)',
                         (message_id, user_id, upload_id, now))


def get_upload(user_id: str, upload_id: str) -> Optional[dict[str, Any]]:
    if not _SAFE_UPLOAD_ID.fullmatch(upload_id):
        return None
    init_upload_db()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM chat_uploads WHERE upload_id=? AND user_id=?', (upload_id, user_id)).fetchone()
    if not row or not Path(row['path']).is_file():
        return None
    return dict(row)
