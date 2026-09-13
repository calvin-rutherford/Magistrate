"""Server-issued authentication for the Gateway and restricted Friend Beta.

Owner sessions are issued from the deployment bootstrap credential. Friend Beta
sessions are issued from independently revocable, operator-provisioned access
grants. Only SHA-256 digests of either bearer or grant codes are persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app import db as database

SESSION_TTL_SECONDS = 3600
SESSION_RETENTION_SECONDS = 30 * 24 * 3600
FRIEND_BETA_GRANT_TTL_SECONDS = 7 * 24 * 3600
FRIEND_BETA_GRANT_MAX_TTL_SECONDS = 30 * 24 * 3600
FRIEND_BETA_ACCESS_PREFIX = "mgb_"
KNOWN_SCOPES = frozenset({"read", "account", "providers", "notifications", "voice", "command", "response"})
FRIEND_BETA_REQUIRED_SCOPES = frozenset({"read", "account"})
FRIEND_BETA_DEFAULT_SCOPES = frozenset({"read", "account", "notifications"})
FRIEND_BETA_SHARED_RUNTIME_SCOPES = frozenset({"voice", "command"})
_FRIEND_BETA_ACCESS_PATTERN = re.compile(r"^mgb_[A-Za-z0-9_-]{32,64}$")
_FRIEND_BETA_USER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Principal:
    user_id: str
    scopes: frozenset[str]
    session_id: str
    expires_at: int
    access_grant_id: Optional[str] = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _valid_user_id(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 128
        and not any(
            unicodedata.category(character).startswith('C')
            or unicodedata.category(character) in {'Zl', 'Zp'}
            for character in value
        )
    )


def _session_db() -> None:
    # init_db owns the schema. Keeping this call here makes session issuance
    # and verification safe for callers that use the auth module directly.
    database.init_db()


def _revoke_push_delivery(connection: sqlite3.Connection, user_id: str, revoked_at: int) -> None:
    """Stop server-driven delivery when a Friend Beta identity loses access."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(push_tokens)")
    }
    if "user_id" in columns and "revoked_at" in columns:
        connection.execute(
            "UPDATE push_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (revoked_at, user_id),
        )


def _suspend_friend_beta_sessions_and_push(now: int) -> None:
    """Apply the default-off feature flag as an authorization/delivery kill switch."""
    _session_db()
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE gateway_sessions SET revoked_at = ?
               WHERE access_grant_id IS NOT NULL AND revoked_at IS NULL""",
            (now,),
        )
        users = connection.execute(
            "SELECT DISTINCT user_id FROM friend_beta_access_grants",
        ).fetchall()
        for row in users:
            _revoke_push_delivery(connection, row[0], now)


def _validated_scopes(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("Scopes must be a collection of scope names")
    scopes = frozenset(item.strip() for item in values if isinstance(item, str) and item.strip())
    if not scopes or not scopes.issubset(KNOWN_SCOPES):
        raise ValueError("Scopes contain an unknown or empty value")
    return scopes


def _configured_scopes() -> frozenset[str]:
    raw = os.getenv("MAGISTRATE_SESSION_SCOPES", "read,account,providers,notifications,voice,command")
    return _validated_scopes(raw.split(","))


def _configured_session_ttl(now: int, *, cap: Optional[int] = None) -> int:
    ttl_seconds = int(os.getenv("MAGISTRATE_SESSION_TTL_SECONDS", SESSION_TTL_SECONDS))
    expires_at = now + ttl_seconds
    if expires_at <= now or expires_at > now + 86400:
        raise ValueError("Session lifetime is not configured safely")
    return min(expires_at, cap) if cap is not None else expires_at


def _insert_session(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    scopes: frozenset[str],
    now: int,
    expires_at: int,
    access_grant_id: Optional[str] = None,
) -> dict[str, object]:
    token = secrets.token_urlsafe(32)
    session_id = secrets.token_urlsafe(16)
    connection.execute(
        """INSERT INTO gateway_sessions
           (session_id, token_hash, user_id, scopes, issued_at, expires_at, access_grant_id)
           VALUES(?,?,?,?,?,?,?)""",
        (
            session_id, _hash_token(token), user_id, ",".join(sorted(scopes)),
            now, expires_at, access_grant_id,
        ),
    )
    return {
        "session_token": token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "scopes": sorted(scopes),
        "user_id": user_id,
    }


def _friend_beta_enabled() -> bool:
    value = os.getenv("MAGISTRATE_FRIEND_BETA_ENABLED", "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError("MAGISTRATE_FRIEND_BETA_ENABLED must be a supported boolean literal")


def validate_friend_beta_configuration() -> bool:
    """Resolve strict Friend Beta settings during Gateway startup."""
    enabled = _friend_beta_enabled()
    now = int(time.time())
    if enabled:
        try:
            _configured_session_ttl(now)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Friend Beta session configuration is invalid") from exc
    else:
        _suspend_friend_beta_sessions_and_push(now)
    return enabled


def create_friend_beta_access_grant(
    user_id: str,
    *,
    scopes: Optional[Iterable[str]] = None,
    ttl_seconds: int = FRIEND_BETA_GRANT_TTL_SECONDS,
    allow_shared_runtime_access: bool = False,
    now: Optional[int] = None,
) -> dict[str, object]:
    """Create one independently revocable beta access code for an operator.

    The default grant is observer/account-only. Command or voice scope requires
    an explicit shared-runtime acknowledgement because those scopes reach the
    operator-owned execution boundary; ``response`` is producer-only and can
    never be granted by this interface.
    """
    user_id = user_id.strip() if isinstance(user_id, str) else user_id
    if not _valid_user_id(user_id) or not _FRIEND_BETA_USER_PATTERN.fullmatch(user_id):
        raise ValueError("Friend Beta user_id must be a 1-64 character URL-safe identifier")
    if user_id == os.getenv("MAGISTRATE_BOOTSTRAP_USER_ID", "default_user").strip():
        raise ValueError("Friend Beta user_id must differ from the owner bootstrap identity")
    selected = _validated_scopes(scopes if scopes is not None else FRIEND_BETA_DEFAULT_SCOPES)
    if not FRIEND_BETA_REQUIRED_SCOPES.issubset(selected):
        raise ValueError("Friend Beta grants require read and account scopes")
    if "response" in selected:
        raise ValueError("The response producer scope cannot be issued to Friend Beta users")
    elevated = selected.intersection(FRIEND_BETA_SHARED_RUNTIME_SCOPES)
    if elevated and not allow_shared_runtime_access:
        raise ValueError("Command or voice scope requires explicit shared-runtime risk acknowledgement")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise ValueError("Friend Beta grant lifetime must be an integer")
    if ttl_seconds < 3600 or ttl_seconds > FRIEND_BETA_GRANT_MAX_TTL_SECONDS:
        raise ValueError("Friend Beta grant lifetime must be between one hour and 30 days")

    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + ttl_seconds
    access_code = FRIEND_BETA_ACCESS_PREFIX + secrets.token_urlsafe(32)
    grant_id = "fbg_" + secrets.token_urlsafe(18)
    _session_db()
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """SELECT 1 FROM friend_beta_access_grants
               WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?""",
            (user_id, issued_at),
        ).fetchone()
        if existing:
            raise ValueError("Friend Beta principal already has an active grant; revoke it before reissuing")
        connection.execute(
            """INSERT INTO friend_beta_access_grants
               (grant_id, code_hash, user_id, scopes, created_at, expires_at)
               VALUES(?,?,?,?,?,?)""",
            (grant_id, _hash_token(access_code), user_id, ",".join(sorted(selected)), issued_at, expires_at),
        )
        # Establish a real principal-owned account row without inventing a name
        # or email. The client completes that profile before protected routes.
        connection.execute(
            """INSERT OR IGNORE INTO user_profiles
               (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
               VALUES(?, '', '', '', '', 'dusk-mountain', ?, ?)""",
            (user_id, issued_at, issued_at),
        )
        # A principal represents one beta installation. Never let an expired
        # device's old push destination reactivate when the principal is
        # deliberately reissued after expiry/revocation.
        _revoke_push_delivery(connection, user_id, issued_at)
    return {
        "schema_version": "friend-beta-access.v1",
        "grant_id": grant_id,
        "access_code": access_code,
        "user_id": user_id,
        "scopes": sorted(selected),
        "created_at": issued_at,
        "expires_at": expires_at,
    }


def list_friend_beta_access_grants(
    *, user_id: Optional[str] = None, now: Optional[int] = None,
) -> list[dict[str, object]]:
    """Return content-free grant metadata; hashes and access codes stay hidden."""
    if user_id is not None and (not _valid_user_id(user_id) or not _FRIEND_BETA_USER_PATTERN.fullmatch(user_id)):
        raise ValueError("Friend Beta user_id must be a 1-64 character URL-safe identifier")
    current = int(time.time() if now is None else now)
    _session_db()
    query = """SELECT grant_id, user_id, scopes, created_at, expires_at,
                      first_redeemed_at, last_redeemed_at, revoked_at
               FROM friend_beta_access_grants"""
    parameters: tuple[object, ...] = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        parameters = (user_id,)
    query += " ORDER BY created_at, grant_id"
    with sqlite3.connect(database.DB_PATH) as connection:
        rows = connection.execute(query, parameters).fetchall()
    grants = []
    for row in rows:
        state = "revoked" if row[7] is not None else "expired" if row[4] <= current else "active"
        grants.append({
            "schema_version": "friend-beta-access.v1",
            "grant_id": row[0],
            "user_id": row[1],
            "scopes": sorted(filter(None, row[2].split(","))),
            "created_at": row[3],
            "expires_at": row[4],
            "first_redeemed_at": row[5],
            "last_redeemed_at": row[6],
            "revoked_at": row[7],
            "state": state,
        })
    return grants


def revoke_friend_beta_access_grant(grant_id: str, *, now: Optional[int] = None) -> bool:
    """Revoke a grant and every bearer derived from it."""
    if not isinstance(grant_id, str) or not re.fullmatch(r"fbg_[A-Za-z0-9_-]{20,32}", grant_id):
        raise ValueError("Friend Beta grant_id is invalid")
    revoked_at = int(time.time() if now is None else now)
    _session_db()
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        grant = connection.execute(
            "SELECT user_id FROM friend_beta_access_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        result = connection.execute(
            """UPDATE friend_beta_access_grants SET revoked_at = ?
               WHERE grant_id = ? AND revoked_at IS NULL""",
            (revoked_at, grant_id),
        )
        connection.execute(
            """UPDATE gateway_sessions SET revoked_at = ?
               WHERE access_grant_id = ? AND revoked_at IS NULL""",
            (revoked_at, grant_id),
        )
        if grant:
            _revoke_push_delivery(connection, grant[0], revoked_at)
    return result.rowcount == 1


def issue_friend_beta_session(access_code: str) -> dict[str, object]:
    """Redeem or renew one per-person access grant into a short bearer."""
    try:
        enabled = _friend_beta_enabled()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Friend Beta access configuration is invalid") from exc
    if not enabled:
        raise HTTPException(status_code=503, detail="Friend Beta access is not enabled")
    if not isinstance(access_code, str) or not _FRIEND_BETA_ACCESS_PATTERN.fullmatch(access_code):
        raise HTTPException(status_code=401, detail="Invalid or expired Friend Beta access code")

    now = int(time.time())
    _session_db()
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT grant_id, user_id, scopes, expires_at, revoked_at
               FROM friend_beta_access_grants WHERE code_hash = ?""",
            (_hash_token(access_code),),
        ).fetchone()
        if not row or row[4] is not None or row[3] <= now or not _valid_user_id(row[1]):
            raise HTTPException(status_code=401, detail="Invalid or expired Friend Beta access code")
        try:
            scopes = _validated_scopes(row[2].split(","))
            if not FRIEND_BETA_REQUIRED_SCOPES.issubset(scopes) or "response" in scopes:
                raise ValueError
            expires_at = _configured_session_ttl(now, cap=row[3])
        except (TypeError, ValueError):
            raise HTTPException(status_code=503, detail="Friend Beta access configuration is invalid") from None

        cutoff = now - SESSION_RETENTION_SECONDS
        connection.execute(
            "DELETE FROM gateway_sessions WHERE (revoked_at IS NOT NULL AND revoked_at < ?) OR expires_at < ?",
            (cutoff, cutoff),
        )
        # A grant represents one beta installation. Reusing it safely replaces
        # the prior short bearer instead of multiplying live device sessions.
        connection.execute(
            """UPDATE gateway_sessions SET revoked_at = ?
               WHERE access_grant_id = ? AND revoked_at IS NULL""",
            (now, row[0]),
        )
        payload = _insert_session(
            connection, user_id=row[1], scopes=scopes, now=now,
            expires_at=expires_at, access_grant_id=row[0],
        )
        connection.execute(
            """UPDATE friend_beta_access_grants
               SET first_redeemed_at = COALESCE(first_redeemed_at, ?), last_redeemed_at = ?
               WHERE grant_id = ?""",
            (now, now, row[0]),
        )
        profile = connection.execute(
            "SELECT name FROM user_profiles WHERE user_id = ?", (row[1],),
        ).fetchone()
    return {
        **payload,
        "auth_method": "friend-beta-access",
        "renewable_until": row[3],
        "onboarding_required": not profile or not isinstance(profile[0], str) or not profile[0].strip(),
    }


def friend_beta_onboarding_required(principal: Principal) -> bool:
    """Tell the client whether this invited principal still needs a name."""
    if principal.access_grant_id is None or not principal.has("account"):
        return False
    _session_db()
    with sqlite3.connect(database.DB_PATH) as connection:
        row = connection.execute(
            "SELECT name FROM user_profiles WHERE user_id = ?", (principal.user_id,),
        ).fetchone()
    return not row or not isinstance(row[0], str) or not row[0].strip()


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
    if not _valid_user_id(user_id):
        raise HTTPException(status_code=503, detail="Session identity is not configured")
    try:
        scopes = _configured_scopes()
        now = int(time.time())
        expires_at = _configured_session_ttl(now)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Session configuration is invalid") from exc

    _session_db()
    cleanup_sessions(now=now)
    with sqlite3.connect(database.DB_PATH) as connection:
        return _insert_session(
            connection, user_id=user_id, scopes=scopes, now=now, expires_at=expires_at,
        )


def revoke_session(token: str) -> None:
    _session_db()
    try:
        token_hash = _hash_token(token)
    except (UnicodeEncodeError, TypeError):
        return
    now = int(time.time())
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT access_grant_id, user_id FROM gateway_sessions WHERE token_hash = ?", (token_hash,),
        ).fetchone()
        connection.execute(
            "UPDATE gateway_sessions SET revoked_at = ? WHERE token_hash = ?", (now, token_hash),
        )
        if row and row[0]:
            connection.execute(
                "UPDATE friend_beta_access_grants SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL",
                (now, row[0]),
            )
            connection.execute(
                "UPDATE gateway_sessions SET revoked_at = ? WHERE access_grant_id = ? AND revoked_at IS NULL",
                (now, row[0]),
            )
            _revoke_push_delivery(connection, row[1], now)


def _principal_from_token(token: str) -> Principal:
    if not isinstance(token, str) or not token or len(token) > 512:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    try:
        token_hash = _hash_token(token)
    except (UnicodeEncodeError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    _session_db()
    now = int(time.time())
    with sqlite3.connect(database.DB_PATH) as connection:
        row = connection.execute(
            """SELECT s.session_id, s.user_id, s.scopes, s.expires_at, s.revoked_at,
                      s.access_grant_id, g.expires_at, g.revoked_at
               FROM gateway_sessions AS s
               LEFT JOIN friend_beta_access_grants AS g ON g.grant_id = s.access_grant_id
               WHERE s.token_hash = ?""",
            (token_hash,),
        ).fetchone()
    scopes = frozenset(filter(None, row[2].split(","))) if row else frozenset()
    grant_invalid = bool(row and row[5] and (row[6] is None or row[6] <= now or row[7] is not None))
    if row and row[5]:
        try:
            if not _friend_beta_enabled():
                _suspend_friend_beta_sessions_and_push(now)
                grant_invalid = True
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Friend Beta access configuration is invalid") from exc
    if (
        not row or row[4] is not None or row[3] <= now or grant_invalid
        or not _valid_user_id(row[1]) or not scopes or not scopes.issubset(KNOWN_SCOPES)
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return Principal(row[1], scopes, row[0], row[3], row[5])


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


def require_any_scope(*scopes: str):
    """Authorize a least-privilege producer or the existing owner command role."""
    if not scopes or any(scope not in KNOWN_SCOPES for scope in scopes):
        raise ValueError('Unknown or empty gateway scope set')

    async def dependency(principal: Principal = Depends(verify_token)) -> Principal:
        if not any(principal.has(scope) for scope in scopes):
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: one of {', '.join(scopes)}",
            )
        return principal

    return dependency
