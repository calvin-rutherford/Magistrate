"""Private, owner-scoped object storage for chat attachments and product artifacts.

Client filenames are display metadata only. Objects are addressed by random keys,
content-sniffed before admission, optionally passed through an operator malware
scanner, and never exposed as filesystem paths. Database rows are the authority
for ownership, retention, and message association.
"""
from __future__ import annotations

import hashlib
import hmac
from io import BytesIO
import json
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from app import db
from app.persistence import connect

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 10
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
DEFAULT_UNATTACHED_TTL_SECONDS = 24 * 60 * 60
DEFAULT_ATTACHED_TTL_SECONDS = 30 * 24 * 60 * 60
SIGNED_ACCESS_TTL_SECONDS = 5 * 60
_SAFE_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SAFE_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SAFE_OBJECT_KEY = re.compile(r"^[a-f0-9]{2}/[a-f0-9]{2}/[A-Za-z0-9_-]{24,64}$")
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
_ALLOWED_ARCHIVE_TYPES = {
    "application/pdf", "application/zip", "application/gzip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
}
_ALLOWED_TEXT_TYPES = {
    "application/json", "application/xml", "application/javascript",
    "text/csv", "text/plain", "text/markdown", "text/html", "text/css",
    "text/javascript", "text/xml", "text/x-python", "text/x-shellscript",
    "text/x-c", "text/x-c++", "text/x-java-source", "text/x-rust", "text/x-go",
}
_ALLOWED_EXACT_TYPES = _ALLOWED_ARCHIVE_TYPES | _ALLOWED_TEXT_TYPES
_SUFFIX_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".json": "application/json", ".xml": "application/xml", ".html": "text/html",
    ".htm": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".mjs": "text/javascript", ".ts": "text/plain", ".tsx": "text/plain",
    ".jsx": "text/javascript", ".py": "text/x-python", ".sh": "text/x-shellscript",
    ".c": "text/x-c", ".h": "text/x-c", ".cpp": "text/x-c++",
    ".java": "text/x-java-source", ".rs": "text/x-rust", ".go": "text/x-go",
    ".yaml": "text/plain", ".yml": "text/plain", ".toml": "text/plain",
    ".sql": "text/plain", ".log": "text/plain", ".pdf": "application/pdf",
    ".zip": "application/zip", ".gz": "application/gzip",
    ".doc": "application/msword", ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_PROCESS_SIGNING_KEY = secrets.token_bytes(32)


def _root() -> Path:
    configured = os.getenv("MAGISTRATE_OBJECT_STORAGE_DIR", "").strip() or os.getenv("MAGISTRATE_CHAT_UPLOAD_DIR", "").strip()
    root = Path(configured) if configured else Path(db.DB_PATH).parent / "private_objects"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root.resolve()


def _ttl(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, 300), 365 * 24 * 60 * 60)


def init_upload_db() -> None:
    # The ordered application migration is the sole schema authority.
    db.init_db()


def _safe_name(filename: str) -> str:
    name = (filename or "upload").replace("\\", "/")
    name = Path(name).name
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:160]
    return name.strip(".") or "upload"


def _normalized_type(media_type: Optional[str], filename: str) -> str:
    declared = (media_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    suffix_type = _SUFFIX_TYPES.get(Path(filename).suffix.lower())
    if declared == "application/octet-stream":
        if not suffix_type:
            raise ValueError("This file type is not supported.")
        return suffix_type
    if declared not in _ALLOWED_IMAGE_TYPES and declared not in _ALLOWED_EXACT_TYPES:
        raise ValueError("This file type is not supported.")
    return declared


def _zip_kind(content: bytes) -> str:
    """Classify a bounded, traversal-safe OOXML/ZIP without extracting it."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000:
                raise ValueError("The ZIP document contains too many entries.")
            names: set[str] = set()
            expanded = 0
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if (
                    not name or name.startswith("/") or ".." in path.parts
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)
                    or entry.flag_bits & 0x1
                ):
                    raise ValueError("The ZIP document contains an unsafe entry.")
                expanded += entry.file_size
                if expanded > 250 * 1024 * 1024:
                    raise ValueError("The ZIP document expands beyond the safe limit.")
                names.add(name)
            if any(name.startswith("word/") for name in names):
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if any(name.startswith("xl/") for name in names):
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if any(name.startswith("ppt/") for name in names):
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    except ValueError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError):
        raise ValueError("The file content is not a valid ZIP document.") from None
    return "application/zip"


def _content_kind(content: bytes) -> Optional[str]:
    if content.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")): return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP": return "image/webp"
    if content.startswith(b"BM"): return "image/bmp"
    if content.startswith(b"%PDF-"): return "application/pdf"
    if content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")): return _zip_kind(content)
    if content.startswith(b"\x1f\x8b"): return "application/gzip"
    if content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")): return "application/x-ole-storage"
    return None


def validate_content(media_type: Optional[str], filename: str, content: bytes) -> str:
    """Sniff bytes; extensions and client MIME declarations are never authority."""
    safe_name = _safe_name(filename)
    kind = _normalized_type(media_type, safe_name)
    detected = _content_kind(content)
    if kind in _ALLOWED_IMAGE_TYPES:
        if detected != kind: raise ValueError("The file content does not match its image type.")
        return kind
    if kind in {"application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"}:
        if detected != "application/x-ole-storage":
            raise ValueError("The file content does not match its document type.")
        return kind
    if kind in _ALLOWED_ARCHIVE_TYPES:
        if detected != kind:
            raise ValueError("The file content does not match its declared type.")
        return kind
    if detected:
        raise ValueError("The file content does not match its declared type.")
    if kind in _ALLOWED_TEXT_TYPES:
        if b"\x00" in content:
            raise ValueError("The file content is not valid text.")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("The file content is not valid UTF-8 text.") from exc
        if kind == "application/json":
            try: json.loads(decoded)
            except json.JSONDecodeError as exc: raise ValueError("The file content is not valid JSON.") from exc
        return kind
    raise ValueError("This file type is not supported.")


def validate_media_type(media_type: Optional[str], filename: str) -> str:
    return _normalized_type(media_type, _safe_name(filename))


def validate_upload_metadata(upload: dict[str, Any], filename: str, media_type: str, size: int) -> None:
    if (_safe_name(filename) != upload["filename"]
            or media_type.lower().split(";", 1)[0].strip() != upload["media_type"]
            or size != upload["size"]):
        raise ValueError("Attachment metadata does not match the uploaded file.")


def _object_path(object_key: str) -> Path:
    if not _SAFE_OBJECT_KEY.fullmatch(object_key):
        raise ValueError("Invalid private object key.")
    root = _root()
    candidate = (root / object_key).resolve()
    if root not in candidate.parents:
        raise ValueError("Invalid private object key.")
    return candidate


def _scan(path: Path) -> str:
    """Invoke an optional fail-closed malware scanner without a shell.

    The configured command receives the private object path as its final argv.
    Exit 0 means clean, exit 1 means rejected, and any other outcome means the
    scanner is unavailable. Output is intentionally discarded.
    """
    configured = os.getenv("MAGISTRATE_UPLOAD_SCAN_COMMAND", "").strip()
    if not configured:
        return "not-configured"
    argv = shlex.split(configured)
    if not argv or len(argv) > 16 or not Path(argv[0]).is_absolute():
        raise ValueError("The upload scanner is unavailable.")
    try:
        result = subprocess.run([*argv, str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("The upload scanner is unavailable.") from exc
    if result.returncode == 0: return "clean"
    if result.returncode == 1: raise ValueError("The file was rejected by content scanning.")
    raise ValueError("The upload scanner is unavailable.")


def cleanup_expired_uploads(now: Optional[int] = None) -> int:
    """Tombstone and remove expired objects; safe to call repeatedly."""
    init_upload_db()
    current = int(time.time()) if now is None else int(now)
    with connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT upload_id,object_key,path FROM chat_uploads WHERE deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at<=?", (current,)).fetchall()
        for row in rows:
            try:
                path = _object_path(row["object_key"]) if row["object_key"] else Path(row["path"])
                if path.is_file() and (_root() == path.resolve().parent or _root() in path.resolve().parents):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            conn.execute("UPDATE chat_uploads SET deleted_at=? WHERE upload_id=?", (current, row["upload_id"]))
    return len(rows)


def save_upload(user_id: str, filename: str, media_type: Optional[str], content: bytes) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Files must be smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    init_upload_db()
    cleanup_expired_uploads()
    safe_name = _safe_name(filename)
    kind = validate_content(media_type, safe_name, content)
    upload_id = secrets.token_urlsafe(18)
    leaf = secrets.token_urlsafe(24)
    digest = hashlib.sha256(content).hexdigest()
    object_key = f"{digest[:2]}/{digest[2:4]}/{leaf}"
    destination = _object_path(object_key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(destination, 0o600)
    try:
        scan_status = _scan(destination)
        now = int(time.time())
        expires_at = now + _ttl("MAGISTRATE_UNATTACHED_UPLOAD_TTL_SECONDS", DEFAULT_UNATTACHED_TTL_SECONDS)
        with connect(db.DB_PATH) as conn:
            conn.execute("""INSERT INTO chat_uploads
                (upload_id,user_id,filename,media_type,size,path,created_at,object_key,sha256,scan_status,expires_at,deleted_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (upload_id, user_id, safe_name, kind, len(content), "", now, object_key, digest, scan_status, expires_at))
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"upload_id": upload_id, "filename": safe_name, "media_type": kind,
            "size": len(content), "status": "stored", "scan_status": scan_status,
            "expires_at": expires_at}


def associate_uploads(user_id: str, message_id: str, upload_ids: list[str]) -> None:
    if not _SAFE_MESSAGE_ID.fullmatch(message_id): raise ValueError("Invalid chat message id.")
    if len(upload_ids) > MAX_UPLOAD_COUNT or len(set(upload_ids)) != len(upload_ids) or any(not _SAFE_UPLOAD_ID.fullmatch(item) for item in upload_ids):
        raise ValueError("Invalid attachment reference.")
    init_upload_db()
    now = int(time.time())
    expires_at = now + _ttl("MAGISTRATE_ATTACHED_UPLOAD_TTL_SECONDS", DEFAULT_ATTACHED_TTL_SECONDS)
    with connect(db.DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = []
        for upload_id in upload_ids:
            row = conn.execute("SELECT * FROM chat_uploads WHERE upload_id=? AND user_id=? AND deleted_at IS NULL AND (expires_at IS NULL OR expires_at>?)", (upload_id, user_id, now)).fetchone()
            if not row: raise ValueError("One or more attached files are unavailable.")
            rows.append(upload_id)
        for upload_id in rows:
            conn.execute("INSERT OR IGNORE INTO chat_message_attachments(message_id,user_id,upload_id,created_at) VALUES(?,?,?,?)", (message_id, user_id, upload_id, now))
            conn.execute("""UPDATE chat_uploads
                SET expires_at=CASE WHEN COALESCE(expires_at,0)>? THEN expires_at ELSE ? END
                WHERE upload_id=? AND user_id=?""",
                (expires_at, expires_at, upload_id, user_id))


def retain_upload_until(
    user_id: str, upload_id: str, expires_at: int, *, connection: Any = None,
) -> bool:
    """Extend one live owner-scoped object's retention without shortening it."""
    if not _SAFE_UPLOAD_ID.fullmatch(upload_id) or type(expires_at) is not int:
        return False
    if connection is None:
        init_upload_db()
    now = int(time.time())
    maximum = now + 365 * 24 * 60 * 60
    if expires_at <= now or expires_at > maximum:
        return False

    def update(conn: Any) -> bool:
        cursor = conn.execute("""UPDATE chat_uploads
            SET expires_at=CASE WHEN COALESCE(expires_at,0)>? THEN expires_at ELSE ? END
            WHERE upload_id=? AND user_id=? AND deleted_at IS NULL
              AND (expires_at IS NULL OR expires_at>?)""",
            (expires_at, expires_at, upload_id, user_id, now))
        return cursor.rowcount == 1

    if connection is not None:
        return update(connection)
    with connect(db.DB_PATH) as conn:
        return update(conn)


def get_upload(user_id: str, upload_id: str) -> Optional[dict[str, Any]]:
    if not _SAFE_UPLOAD_ID.fullmatch(upload_id): return None
    init_upload_db()
    now = int(time.time())
    with connect(db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM chat_uploads WHERE upload_id=? AND user_id=? AND deleted_at IS NULL AND (expires_at IS NULL OR expires_at>?)", (upload_id, user_id, now)).fetchone()
    if not row: return None
    record = {key: row[key] for key in row.keys()}
    try:
        path = _object_path(record["object_key"]) if record.get("object_key") else Path(record["path"])
    except ValueError:
        return None
    if not path.is_file(): return None
    record["path"] = str(path)  # internal only; public serializers select fields explicitly
    return record


def read_upload_content(user_id: str, upload_id: str) -> Optional[bytes]:
    upload = get_upload(user_id, upload_id)
    if not upload: return None
    try:
        content = Path(upload["path"]).read_bytes()
    except OSError:
        return None
    if len(content) != upload["size"] or (upload.get("sha256") and not hmac.compare_digest(hashlib.sha256(content).hexdigest(), upload["sha256"])):
        return None
    try:
        if validate_content(upload["media_type"], upload["filename"], content) != upload["media_type"]:
            return None
    except ValueError:
        return None
    return content


def delete_upload(user_id: str, upload_id: str) -> bool:
    upload = get_upload(user_id, upload_id)
    if not upload: return False
    with connect(db.DB_PATH) as conn:
        attached = conn.execute("SELECT 1 FROM chat_message_attachments WHERE upload_id=? AND user_id=? LIMIT 1", (upload_id, user_id)).fetchone()
        if attached: raise ValueError("An attached file follows message retention and cannot be removed separately.")
        conn.execute("UPDATE chat_uploads SET deleted_at=? WHERE upload_id=? AND user_id=?", (int(time.time()), upload_id, user_id))
    Path(upload["path"]).unlink(missing_ok=True)
    return True


def _signing_key() -> bytes:
    configured = os.getenv("MAGISTRATE_UPLOAD_SIGNING_KEY", "").strip()
    if len(configured) >= 32:
        return configured.encode("utf-8")
    # The Gateway's required persistent secret keeps signatures valid across
    # workers/restarts when a dedicated rotation key is not configured.
    gateway_secret = os.getenv("MAGISTRATE_SECRET_KEY", "").strip()
    return gateway_secret.encode("utf-8") if len(gateway_secret) >= 32 else _PROCESS_SIGNING_KEY


def signed_access_token(user_id: str, upload_id: str, expires_at: int) -> str:
    payload = f"upload.v1\0{user_id}\0{upload_id}\0{expires_at}".encode("utf-8")
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def verify_signed_access(user_id: str, upload_id: str, expires_at: int, signature: str) -> bool:
    now = int(time.time())
    if expires_at < now or expires_at > now + SIGNED_ACCESS_TTL_SECONDS + 30 or not re.fullmatch(r"[a-f0-9]{64}", signature or ""):
        return False
    return hmac.compare_digest(signature, signed_access_token(user_id, upload_id, expires_at))
