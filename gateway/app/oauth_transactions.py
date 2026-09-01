"""Provider-independent OAuth transaction integrity and redirect policy.

The OAuth callback is reached from a provider redirect, so it cannot rely on
the gateway's request authentication header.  Instead, connect records the
authenticated request's principal in this server-side transaction store.  The
callback can then recover only values that were stored by connect; it never
parses identity or redirect data from the provider-controlled state value.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from app import db as database


DEFAULT_REDIRECT_URI = "magistrate://account"
REDIRECT_URI_ENV = "MAGISTRATE_OAUTH_REDIRECT_URIS"
STATE_TTL_ENV = "MAGISTRATE_OAUTH_STATE_TTL_SECONDS"
DEFAULT_STATE_TTL_SECONDS = 600
MAX_STATE_TTL_SECONDS = 3600
STATE_BYTES = 32
STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
UNSAFE_REDIRECT_SCHEMES = frozenset({"data", "file", "javascript", "vbscript"})


class OAuthTransactionError(ValueError):
    """A safe, client-facing OAuth transaction validation failure."""


@dataclass(frozen=True)
class OAuthTransaction:
    principal_id: str
    provider: str
    redirect_uri: str
    expires_at: int


def _is_safe_redirect_uri(uri: str) -> bool:
    """Reject ambiguous, credential-bearing, or browser-executable targets."""

    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        return False

    try:
        parsed = urlsplit(uri)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    if parsed.scheme.lower() in UNSAFE_REDIRECT_SCHEMES:
        return False

    # HTTP is useful for a local Expo/browser callback only when it is loopback
    # and explicitly present in the environment allowlist.
    if parsed.scheme == "http":
        return parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https":
        return parsed.hostname is not None

    # Custom app schemes such as magistrate://account are accepted by exact
    # allowlist match below; no web origin is implied by a custom scheme.
    return bool(re.fullmatch(r"[a-z][a-z0-9+.-]*", parsed.scheme, re.IGNORECASE))


def allowed_redirect_uris() -> frozenset[str]:
    """Return the exact, safe application redirect allowlist.

    The default is the native app callback.  Setting the environment variable
    replaces (rather than expands) it, which makes local development explicit.
    Invalid entries are ignored and can never become redirect targets.
    """

    configured = os.getenv(REDIRECT_URI_ENV)
    entries = configured.split(",") if configured is not None else [DEFAULT_REDIRECT_URI]
    return frozenset(
        entry.strip()
        for entry in entries
        if entry.strip() and _is_safe_redirect_uri(entry.strip())
    )


def is_allowed_redirect_uri(uri: str) -> bool:
    return _is_safe_redirect_uri(uri) and uri in allowed_redirect_uris()


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def _validate_state_format(state: object) -> str:
    if not isinstance(state, str) or not STATE_PATTERN.fullmatch(state):
        raise OAuthTransactionError("Malformed OAuth state")
    return state


def _state_ttl_seconds() -> int:
    configured = os.getenv(STATE_TTL_ENV)
    if configured is None:
        return DEFAULT_STATE_TTL_SECONDS
    try:
        ttl = int(configured)
    except ValueError:
        return DEFAULT_STATE_TTL_SECONDS
    # Invalid configuration fails safe by using the short default rather than
    # creating transactions with an unexpectedly long lifetime.
    if ttl < 1 or ttl > MAX_STATE_TTL_SECONDS:
        return DEFAULT_STATE_TTL_SECONDS
    return ttl


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database.DB_PATH, timeout=5)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class OAuthTransactionStore:
    """Persist opaque OAuth transactions and consume each state atomically."""

    def initialize(self) -> None:
        connection = _connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_transactions (
                    state_hash TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def create(
        self,
        principal_id: str,
        provider: str,
        redirect_uri: str,
        *,
        now: Optional[int] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        if not isinstance(principal_id, str) or not principal_id:
            raise OAuthTransactionError("Invalid OAuth principal")
        if not isinstance(provider, str) or not provider:
            raise OAuthTransactionError("Invalid OAuth provider")
        if not is_allowed_redirect_uri(redirect_uri):
            raise OAuthTransactionError("Disallowed OAuth redirect")

        self.initialize()
        state = secrets.token_urlsafe(STATE_BYTES)
        created_at = int(time.time() if now is None else now)
        ttl = _state_ttl_seconds() if ttl_seconds is None else ttl_seconds
        if ttl < 1 or ttl > MAX_STATE_TTL_SECONDS:
            raise OAuthTransactionError("Invalid OAuth state lifetime")
        expires_at = created_at + ttl

        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO oauth_transactions
                    (state_hash, principal_id, provider, redirect_uri, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_state_hash(state), principal_id, provider, redirect_uri, expires_at),
            )
            connection.commit()
        finally:
            connection.close()
        return state

    def consume(
        self,
        state: object,
        provider: str,
        *,
        expected_principal: Optional[str] = None,
        now: Optional[int] = None,
    ) -> OAuthTransaction:
        valid_state = _validate_state_format(state)
        if not isinstance(provider, str) or not provider:
            raise OAuthTransactionError("Invalid OAuth provider")
        if expected_principal is not None and not expected_principal:
            raise OAuthTransactionError("Invalid OAuth principal")

        self.initialize()
        current_time = int(time.time() if now is None else now)
        connection = _connect()
        try:
            # BEGIN IMMEDIATE serializes competing callbacks so only one can
            # transition a matching transaction from unused to consumed.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT principal_id, provider, redirect_uri, expires_at, consumed_at
                FROM oauth_transactions
                WHERE state_hash = ?
                """,
                (_state_hash(valid_state),),
            ).fetchone()
            if row is None:
                raise OAuthTransactionError("Unknown OAuth state")
            if row[4] is not None:
                raise OAuthTransactionError("OAuth state already consumed")
            if row[3] <= current_time:
                raise OAuthTransactionError("Expired OAuth state")
            if row[1] != provider:
                raise OAuthTransactionError("OAuth state provider mismatch")
            if expected_principal is not None and row[0] != expected_principal:
                raise OAuthTransactionError("OAuth state principal mismatch")

            consumed_at = current_time
            updated = connection.execute(
                """
                UPDATE oauth_transactions
                SET consumed_at = ?
                WHERE state_hash = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (consumed_at, _state_hash(valid_state), current_time),
            ).rowcount
            if updated != 1:
                raise OAuthTransactionError("OAuth state already consumed")
            connection.commit()
            return OAuthTransaction(
                principal_id=row[0],
                provider=row[1],
                redirect_uri=row[2],
                expires_at=row[3],
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
