"""Server-issued session authentication for the gateway.

The mobile/web client never contains a gateway credential.  A short-lived
opaque session is issued only after a server-configured bootstrap secret (or an
explicit development auto-session) is presented.  Only a SHA-256 digest is
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

# Kept as a test-only compatibility value for the pre-session regression suite.
# It is never accepted outside MAGISTRATE_ENV=test and is not shipped by the
# frontend.  Production has no default credential.
MAGISTRATE_TOKEN = os.getenv("MAGISTRATE_TOKEN") or (
    "magistrate-device-token-12345" if os.getenv("MAGISTRATE_ENV", "").lower() == "test" else ""
)
SESSION_TTL_SECONDS = 3600
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
    database.init_db()
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS gateway_sessions (
                session_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                scopes TEXT NOT NULL,
                issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            )"""
        )


def _configured_scopes() -> frozenset[str]:
    raw = os.getenv("MAGISTRATE_SESSION_SCOPES", "read,account,providers,notifications,voice,command")
    scopes = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if not scopes or not scopes.issubset(KNOWN_SCOPES):
        raise RuntimeError("MAGISTRATE_SESSION_SCOPES contains an unknown or empty scope")
    return scopes


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
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO gateway_sessions(session_id, token_hash, user_id, scopes, issued_at, expires_at) VALUES(?,?,?,?,?,?)",
            (session_id, _hash_token(token), user_id, ",".join(sorted(scopes)), now, expires_at),
        )
    return {"session_token": token, "token_type": "Bearer", "expires_at": expires_at, "scopes": sorted(scopes)}


def revoke_session(token: str) -> None:
    _session_db()
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE gateway_sessions SET revoked_at=? WHERE token_hash=?", (int(time.time()), _hash_token(token)))


def _principal_from_token(token: str) -> Principal:
    if not isinstance(token, str) or not token or len(token) > 512:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    try:
        token_hash = _hash_token(token)
    except UnicodeEncodeError:
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


def authenticate_request(request: Request, authorization: Optional[str], legacy_header: Optional[str]) -> Principal:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value or len(value) > 512:
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        return _principal_from_token(value)
    # Existing private tests and explicitly local development can continue to
    # exercise the demo with the old header, but this branch is unavailable in
    # production and does not accept query-string credentials.
    if legacy_header and MAGISTRATE_TOKEN and os.getenv("MAGISTRATE_ENV", "").lower() == "test" and hmac.compare_digest(legacy_header, MAGISTRATE_TOKEN):
        return Principal("default_user", frozenset(KNOWN_SCOPES), "legacy-test", 2**31)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def verify_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_magistrate_token: Optional[str] = Header(None, alias="X-Magistrate-Token"),
) -> Principal:
    return authenticate_request(request, authorization, x_magistrate_token)


def require_scope(scope: str):
    if scope not in KNOWN_SCOPES:
        raise ValueError(f"Unknown gateway scope: {scope}")

    async def dependency(principal: Principal = Depends(verify_token)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
        return principal

    return dependency
