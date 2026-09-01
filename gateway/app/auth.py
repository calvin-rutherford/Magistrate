"""Server-issued session authentication for the gateway.

The mobile/web client never contains a gateway credential. A short-lived
opaque session is issued only after a server-configured bootstrap secret (or
an explicit development auto-session) is presented. Only a SHA-256 digest is
stored, so sessions can be revoked without retaining bearer credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app import db as database

SESSION_TTL_SECONDS = 3600
SESSION_RETENTION_SECONDS = 30 * 24 * 3600
KNOWN_SCOPES = frozenset({"read", "account", "providers", "notifications", "voice", "command"})


@dataclass(frozen=True)
class Principal:
    user_id: str
    scopes: frozenset[str]
    session_id: str
    expires_at: int

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _session_db() -> None:
    # init_db owns the schema. Keeping this call here makes session issuance
    # and verification safe for callers that use the auth module directly.
    database.init_db()


def _configured_scopes() -> frozenset[str]:
    raw = os.getenv("MAGISTRATE_SESSION_SCOPES", "read,account,providers,notifications,voice,command")
    scopes = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if not scopes or not scopes.issubset(KNOWN_SCOPES):
        raise RuntimeError("MAGISTRATE_SESSION_SCOPES contains an unknown or empty scope")
    return scopes


def cleanup_sessions(*, now: Optional[int] = None) -> int:
    """Delete old revoked/expired rows without touching active sessions."""
    _session_db()
    cutoff = int(time.time() if now is None else now) - SESSION_RETENTION_SECONDS
    with sqlite3.connect(database.DB_PATH) as conn:
        result = conn.execute(
            "DELETE FROM gateway_sessions WHERE (revoked_at IS NOT NULL AND revoked_at < ?) OR expires_at < ?",
            (cutoff, cutoff),
        )
    return result.rowcount


def issue_session(bootstrap_secret: Optional[str] = None) -> dict[str, object]:
    configured_secret = os.getenv("MAGISTRATE_BOOTSTRAP_SECRET", "")
    env = os.getenv("MAGISTRATE_ENV", "").lower()
    auto_dev = env in {"dev", "development", "test", "testing"} and _truthy("MAGISTRATE_DEV_AUTO_SESSION")
    if configured_secret:
        if not bootstrap_secret or not hmac.compare_digest(bootstrap_secret, configured_secret):
            raise HTTPException(status_code=401, detail="Invalid session bootstrap credential")
    elif not auto_dev:
        raise HTTPException(status_code=503, detail="Session issuance is not configured")

    user_id = os.getenv("MAGISTRATE_BOOTSTRAP_USER_ID", "default_user").strip()
    if not user_id or len(user_id) > 128:
        raise HTTPException(status_code=503, detail="Session identity is not configured")
    try:
        scopes = _configured_scopes()
        ttl_seconds = int(os.getenv("MAGISTRATE_SESSION_TTL_SECONDS", SESSION_TTL_SECONDS))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Session configuration is invalid") from exc
    now = int(time.time())
    expires_at = now + ttl_seconds
    if expires_at <= now or expires_at > now + 86400:
        raise HTTPException(status_code=503, detail="Session lifetime is not configured safely")

    token = secrets.token_urlsafe(32)
    session_id = secrets.token_urlsafe(16)
    _session_db()
    cleanup_sessions(now=now)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO gateway_sessions(session_id, token_hash, user_id, scopes, issued_at, expires_at) VALUES(?,?,?,?,?,?)",
            (session_id, _hash_token(token), user_id, ",".join(sorted(scopes)), now, expires_at),
        )
    return {
        "session_token": token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "scopes": sorted(scopes),
        "user_id": user_id,
    }


def revoke_session(token: str) -> None:
    _session_db()
    try:
        token_hash = _hash_token(token)
    except (UnicodeEncodeError, TypeError):
        return
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE gateway_sessions SET revoked_at=? WHERE token_hash=?", (int(time.time()), token_hash))


def _principal_from_token(token: str) -> Principal:
    if not isinstance(token, str) or not token or len(token) > 512:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    try:
        token_hash = _hash_token(token)
    except (UnicodeEncodeError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    _session_db()
    now = int(time.time())
    with sqlite3.connect(database.DB_PATH) as conn:
        row = conn.execute(
            "SELECT session_id,user_id,scopes,expires_at,revoked_at FROM gateway_sessions WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
    if not row or row[4] is not None or row[3] <= now:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return Principal(row[1], frozenset(filter(None, row[2].split(","))), row[0], row[3])


def authenticate_request(request: Request, authorization: Optional[str]) -> Principal:
    del request
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    scheme, separator, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not value or len(value) > 512:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return _principal_from_token(value)


def verify_token(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Principal:
    return authenticate_request(request, authorization)


def require_scope(scope: str):
    if scope not in KNOWN_SCOPES:
        raise ValueError(f"Unknown gateway scope: {scope}")

    async def dependency(principal: Principal = Depends(verify_token)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
        return principal

    return dependency
