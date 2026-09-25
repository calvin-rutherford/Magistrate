"""Provider-backed account identity and durable session rotation.

Apple and Google assertions are verified against provider JWKS, bound to a
single-use server nonce, and then mapped onto the existing ``user_profiles`` /
``connected_accounts`` account model.  Browser refresh authority is delivered
only as an HttpOnly cookie; native refresh authority is returned for Keychain
storage.  Only digests of refresh tokens and provider assertions are retained.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import HTTPException

from app import db
from app.auth import (
    KNOWN_SCOPES,
    Principal,
    _configured_session_ttl,
    _hash_token,
    _insert_session,
    _revoke_push_delivery,
    _validated_scopes,
)

ProviderName = Literal["apple", "google"]
ChallengeAction = Literal["sign_in", "link"]
ClientPlatform = Literal["native", "web"]

PROVIDER_CHALLENGE_TTL_SECONDS = 10 * 60
PROVIDER_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60
PROVIDER_REFRESH_MAX_TTL_SECONDS = 90 * 24 * 60 * 60
PROVIDER_REFRESH_PREFIX = "mgr_"
PROVIDER_COOKIE_NAME = "magistrate_provider_refresh"
PROVIDER_COOKIE_PATH = "/api/v1/auth"
MAX_ID_TOKEN_BYTES = 24 * 1024
MAX_JWKS_BYTES = 256 * 1024
MAX_ACTIVE_CHALLENGES = 10_000
JWKS_UNKNOWN_KEY_REFRESH_SECONDS = 60
MAX_EXPIRED_SESSION_FAMILY_CLEANUP = 100
_JWT_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_SUBJECT = re.compile(r"^[^\x00-\x1f\x7f-\x9f]{1,255}$")
_SAFE_CHALLENGE = re.compile(r"^pac_[A-Za-z0-9_-]{20,40}$")
_SAFE_REFRESH = re.compile(r"^mgr_[A-Za-z0-9_-]{32,80}$")
_JWKS_URLS: dict[ProviderName, str] = {
    "apple": "https://appleid.apple.com/auth/keys",
    "google": "https://www.googleapis.com/oauth2/v3/certs",
}
_ISSUERS: dict[ProviderName, frozenset[str]] = {
    "apple": frozenset({"https://appleid.apple.com"}),
    "google": frozenset({"https://accounts.google.com", "accounts.google.com"}),
}
_jwks_cache: dict[ProviderName, tuple[float, list[dict[str, Any]]]] = {}
_jwks_refreshed_at: dict[ProviderName, float] = {}
_jwks_lock = asyncio.Lock()


@dataclass(frozen=True)
class ProviderClaims:
    provider: ProviderName
    subject: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str]
    assertion_hash: str
    audience: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid {label}.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=401, detail=f"Invalid {label}.")
    return value


def _decode_segment(value: str, *, maximum: int, label: str) -> bytes:
    if not value or len(value) > maximum * 2 or not _JWT_PART.fullmatch(value):
        raise HTTPException(status_code=401, detail=f"Invalid {label}.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid {label}.") from exc
    if len(decoded) > maximum:
        raise HTTPException(status_code=401, detail=f"Invalid {label}.")
    return decoded


def _validated_client_ids(setting: str) -> frozenset[str]:
    values = frozenset(item.strip() for item in os.getenv(setting, "").split(",") if item.strip())
    if any(len(value) > 255 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,254}", value) for value in values):
        raise HTTPException(status_code=503, detail="Provider sign-in configuration is invalid.")
    return values


def _configured_ids(provider: ProviderName) -> frozenset[str]:
    setting = "MAGISTRATE_APPLE_CLIENT_IDS" if provider == "apple" else "MAGISTRATE_GOOGLE_CLIENT_IDS"
    values = list(_validated_client_ids(setting))
    if provider == "apple":
        service_id = os.getenv("MAGISTRATE_APPLE_SERVICE_ID", "").strip()
        if service_id:
            values.append(service_id)
    else:
        values.extend(_validated_client_ids("MAGISTRATE_GOOGLE_WEB_CLIENT_IDS"))
        values.extend(_validated_client_ids("MAGISTRATE_GOOGLE_IOS_CLIENT_IDS"))
    accepted = frozenset(values)
    if any(len(value) > 255 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,254}", value) for value in accepted):
        raise HTTPException(status_code=503, detail=f"{provider.title()} sign-in configuration is invalid.")
    return accepted


def _platform_client_ids(provider: ProviderName, platform: ClientPlatform) -> frozenset[str]:
    if provider == "apple":
        if platform == "web":
            value = os.getenv("MAGISTRATE_APPLE_SERVICE_ID", "").strip()
            return frozenset({value}) if value else frozenset()
        return _validated_client_ids("MAGISTRATE_APPLE_CLIENT_IDS")
    setting = (
        "MAGISTRATE_GOOGLE_WEB_CLIENT_IDS"
        if platform == "web" else "MAGISTRATE_GOOGLE_IOS_CLIENT_IDS"
    )
    return _validated_client_ids(setting)


def _provider_scopes() -> frozenset[str]:
    raw = os.getenv(
        "MAGISTRATE_PROVIDER_SESSION_SCOPES",
        "read,account,providers,notifications,voice,command",
    )
    try:
        scopes = _validated_scopes(raw.split(","))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Provider session configuration is invalid.") from exc
    if "response" in scopes or not {"read", "account"}.issubset(scopes):
        raise HTTPException(status_code=503, detail="Provider session configuration is invalid.")
    return scopes


def _refresh_expiry(now: int) -> int:
    try:
        ttl = int(os.getenv("MAGISTRATE_PROVIDER_REFRESH_TTL_SECONDS", str(PROVIDER_REFRESH_TTL_SECONDS)))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Provider session configuration is invalid.") from exc
    if ttl < 3600 or ttl > PROVIDER_REFRESH_MAX_TTL_SECONDS:
        raise HTTPException(status_code=503, detail="Provider session configuration is invalid.")
    return now + ttl


def _cleanup_expired_session_families(connection: sqlite3.Connection, now: int) -> None:
    family_ids = [
        str(row[0]) for row in connection.execute(
            """SELECT family_id FROM provider_session_families
               WHERE expires_at <= ? ORDER BY expires_at LIMIT ?""",
            (now, MAX_EXPIRED_SESSION_FAMILY_CLEANUP),
        ).fetchall()
    ]
    if not family_ids:
        return
    placeholders = ",".join("?" for _ in family_ids)
    connection.execute(
        f"DELETE FROM gateway_sessions WHERE provider_session_id IN ({placeholders})",
        family_ids,
    )
    connection.execute(
        f"DELETE FROM provider_refresh_tokens WHERE family_id IN ({placeholders})",
        family_ids,
    )
    connection.execute(
        f"DELETE FROM provider_session_families WHERE family_id IN ({placeholders})",
        family_ids,
    )


def validate_provider_auth_configuration() -> None:
    """Fail startup only for partial configuration; an entirely absent provider stays unavailable."""
    for provider in ("apple", "google"):
        _configured_ids(provider)
    apple_secret_names = (
        "MAGISTRATE_APPLE_TEAM_ID",
        "MAGISTRATE_APPLE_KEY_ID",
        "MAGISTRATE_APPLE_PRIVATE_KEY",
        "MAGISTRATE_APPLE_SERVICE_ID",
    )
    supplied = [bool(os.getenv(name, "").strip()) for name in apple_secret_names]
    if any(supplied) and not all(supplied):
        raise RuntimeError("Apple web sign-in configuration is incomplete")
    production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {
        "dev", "development", "test", "testing",
    }
    if production:
        apple_any = bool(_configured_ids("apple"))
        if apple_any and (not _platform_client_ids("apple", "native") or not all(supplied)):
            raise RuntimeError("Apple iPhone/web sign-in configuration is incomplete")
        google_any = bool(_configured_ids("google"))
        if google_any and (
            not _platform_client_ids("google", "native")
            or not _platform_client_ids("google", "web")
        ):
            raise RuntimeError("Google iPhone/web sign-in configuration is incomplete")
        if apple_any or google_any:
            redirects = _allowed_redirects()
            try:
                for value in redirects:
                    _validate_redirect_uri(value, required=True)
                parsed_redirects = [(value, urlsplit(value)) for value in redirects]
            except (HTTPException, ValueError) as exc:
                raise RuntimeError("Provider sign-in redirect configuration is invalid") from exc
            web_redirects = {
                value for value, parsed in parsed_redirects if parsed.scheme == "https"
            }
            native_redirects = {
                value for value, parsed in parsed_redirects
                if parsed.scheme not in {"http", "https"}
                and bool(parsed.scheme) and not parsed.netloc
            }
            if not web_redirects or (google_any and not native_redirects):
                raise RuntimeError("Provider iPhone/web redirect configuration is incomplete")
        if apple_any:
            try:
                _apple_client_secret(int(time.time()))
            except HTTPException as exc:
                raise RuntimeError("Apple web sign-in configuration is invalid") from exc
    try:
        now = int(time.time())
        _provider_scopes()
        _refresh_expiry(now)
        _configured_session_ttl(now)
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc


def provider_availability() -> dict[str, bool]:
    apple_native = bool(_platform_client_ids("apple", "native"))
    apple_web = all(os.getenv(name, "").strip() for name in (
        "MAGISTRATE_APPLE_TEAM_ID", "MAGISTRATE_APPLE_KEY_ID",
        "MAGISTRATE_APPLE_PRIVATE_KEY", "MAGISTRATE_APPLE_SERVICE_ID",
    ))
    google_native = bool(_platform_client_ids("google", "native"))
    google_web = bool(_platform_client_ids("google", "web"))
    return {
        "apple": bool(_configured_ids("apple")),
        "apple_native": apple_native,
        "apple_web": apple_web,
        "google": bool(_configured_ids("google")),
        "google_native": google_native,
        "google_web": google_web,
    }


def _allowed_redirects() -> frozenset[str]:
    return frozenset(
        item.strip() for item in os.getenv("MAGISTRATE_AUTH_REDIRECT_URIS", "").split(",")
        if item.strip()
    )


def _validate_redirect_uri(value: Optional[str], *, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise HTTPException(status_code=422, detail="A registered sign-in redirect URI is required.")
        return None
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(char) < 32 for char in value):
        raise HTTPException(status_code=422, detail="The sign-in redirect URI is invalid.")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The sign-in redirect URI is invalid.") from exc
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    native = parsed.scheme not in {"http", "https"} and bool(parsed.scheme) and not parsed.netloc
    if parsed.fragment or parsed.username or parsed.password or not (
        (parsed.scheme == "https" and parsed.netloc) or (local and parsed.scheme == "http") or native
    ):
        raise HTTPException(status_code=422, detail="The sign-in redirect URI is invalid.")
    allowed = _allowed_redirects()
    production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {"dev", "development", "test", "testing"}
    if (allowed and value not in allowed) or (production and not allowed):
        raise HTTPException(status_code=422, detail="The sign-in redirect URI is not registered.")
    return value


def create_challenge(
    provider: ProviderName,
    action: ChallengeAction,
    client_platform: ClientPlatform,
    redirect_uri: Optional[str],
    principal: Optional[Principal],
) -> dict[str, Any]:
    if provider not in {"apple", "google"} or not _configured_ids(provider):
        raise HTTPException(status_code=503, detail=f"{str(provider).title()} sign-in is not configured.")
    if action == "link" and principal is None:
        raise HTTPException(status_code=401, detail="Authentication is required to link a provider.")
    if action == "sign_in" and principal is not None:
        raise HTTPException(status_code=409, detail="Sign out before starting another sign-in.")
    needs_redirect = client_platform == "web" or provider == "google"
    redirect_uri = _validate_redirect_uri(redirect_uri, required=needs_redirect)
    redirect_scheme = urlsplit(redirect_uri).scheme if redirect_uri else None
    if (
        (client_platform == "web" and redirect_scheme not in {"http", "https"})
        or (client_platform == "native" and provider == "google"
            and redirect_scheme in {"http", "https"})
        or (client_platform == "native" and provider == "apple" and redirect_uri is not None)
    ):
        raise HTTPException(status_code=422, detail="The sign-in redirect does not match the client platform.")
    platform_ids = _platform_client_ids(provider, client_platform)
    production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {
        "dev", "development", "test", "testing",
    }
    if production and not platform_ids:
        raise HTTPException(status_code=503, detail=f"{provider.title()} sign-in is not configured for this client.")
    availability = provider_availability()
    if provider == "apple" and client_platform == "web" and not availability["apple_web"]:
        raise HTTPException(status_code=503, detail="Apple web sign-in is not configured.")

    now = int(time.time())
    challenge_id = "pac_" + secrets.token_urlsafe(18)
    nonce = secrets.token_urlsafe(32)
    nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _cleanup_expired_session_families(connection, now)
        # Assertions cannot be replayed while their token can still satisfy the
        # one-day issued-at bound. Retire older digests before their challenge
        # rows so routine sign-in does not grow either ledger forever.
        connection.execute(
            "DELETE FROM provider_assertions WHERE accepted_at < ?",
            (now - 86400,),
        )
        connection.execute(
            """DELETE FROM provider_auth_challenges
               WHERE expires_at < ? AND challenge_id NOT IN
                 (SELECT challenge_id FROM provider_assertions)""",
            (now - 86400,),
        )
        active = connection.execute(
            "SELECT COUNT(*) FROM provider_auth_challenges WHERE consumed_at IS NULL AND expires_at > ?",
            (now,),
        ).fetchone()[0]
        if active >= MAX_ACTIVE_CHALLENGES:
            raise HTTPException(status_code=503, detail="Sign-in is temporarily busy. Try again shortly.")
        connection.execute(
            """INSERT INTO provider_auth_challenges
               (challenge_id, provider, action, nonce_hash, owner_user_id,
                client_platform, redirect_uri, created_at, expires_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                challenge_id, provider, action, nonce_hash,
                principal.user_id if principal else None, client_platform,
                redirect_uri, now, now + PROVIDER_CHALLENGE_TTL_SECONDS,
            ),
        )
    return {
        "schema_version": "provider-auth-challenge.v1",
        "challenge_id": challenge_id,
        "provider": provider,
        "action": action,
        "nonce": nonce,
        # Apple receives the SHA-256 nonce while Google receives the raw OIDC
        # nonce. The exchange still requires the raw nonce and verifies its
        # digest against the one-time challenge row.
        "authorization_nonce": nonce_hash if provider == "apple" else nonce,
        "redirect_uri": redirect_uri,
        "expires_at": now + PROVIDER_CHALLENGE_TTL_SECONDS,
    }


async def _download_jwks(provider: ProviderName) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(_JWKS_URLS[provider], headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="The identity provider could not be verified.") from exc
    if response.status_code != 200 or len(response.content) > MAX_JWKS_BYTES:
        raise HTTPException(status_code=503, detail="The identity provider could not be verified.")
    payload = _json_object(response.content, label="provider key set")
    keys = payload.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 20 or any(not isinstance(key, dict) for key in keys):
        raise HTTPException(status_code=503, detail="The identity provider returned an invalid key set.")
    return keys


async def _jwks(provider: ProviderName, *, force: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _jwks_cache.get(provider)
    if not force and cached and cached[0] > now:
        return cached[1]
    async with _jwks_lock:
        now = time.monotonic()
        cached = _jwks_cache.get(provider)
        if not force and cached and cached[0] > now:
            return cached[1]
        if _jwks_refreshed_at.get(provider, 0) + JWKS_UNKNOWN_KEY_REFRESH_SECONDS > now:
            if cached:
                return cached[1]
            raise HTTPException(status_code=503, detail="The identity provider could not be verified.")
        # Record the attempt before I/O so provider failures cannot turn
        # attacker-selected key IDs into one outbound request per assertion.
        _jwks_refreshed_at[provider] = now
        try:
            keys = await _download_jwks(provider)
        except HTTPException:
            if cached:
                return cached[1]
            raise
        refreshed_at = time.monotonic()
        _jwks_cache[provider] = (refreshed_at + 3600, keys)
        return keys


def _rsa_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    if (
        jwk.get("kty") != "RSA" or jwk.get("alg") not in {None, "RS256"}
        or jwk.get("use") not in {None, "sig"}
        or not isinstance(jwk.get("n"), str) or not isinstance(jwk.get("e"), str)
    ):
        raise HTTPException(status_code=401, detail="Invalid provider signing key.")
    modulus = int.from_bytes(_decode_segment(jwk["n"], maximum=1024, label="provider signing key"), "big")
    exponent = int.from_bytes(_decode_segment(jwk["e"], maximum=8, label="provider signing key"), "big")
    if modulus.bit_length() < 2048 or exponent not in {3, 65537}:
        raise HTTPException(status_code=401, detail="Invalid provider signing key.")
    try:
        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid provider signing key.") from exc


async def verify_identity_token(
    provider: ProviderName,
    identity_token: str,
    raw_nonce: str,
    *,
    keys: Optional[list[dict[str, Any]]] = None,
    now: Optional[int] = None,
) -> ProviderClaims:
    if (
        not isinstance(identity_token, str) or not identity_token
        or len(identity_token.encode("utf-8", errors="ignore")) > MAX_ID_TOKEN_BYTES
        or not isinstance(raw_nonce, str) or not 32 <= len(raw_nonce) <= 128
    ):
        raise HTTPException(status_code=401, detail="The provider assertion is invalid.")
    parts = identity_token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="The provider assertion is invalid.")
    header = _json_object(_decode_segment(parts[0], maximum=4096, label="provider assertion"), label="provider assertion")
    claims = _json_object(_decode_segment(parts[1], maximum=16 * 1024, label="provider assertion"), label="provider assertion")
    signature = _decode_segment(parts[2], maximum=1024, label="provider assertion")
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
        raise HTTPException(status_code=401, detail="The provider assertion is invalid.")

    candidates = keys if keys is not None else await _jwks(provider)
    jwk = next((item for item in candidates if item.get("kid") == kid), None)
    if jwk is None and keys is None:
        candidates = await _jwks(provider, force=True)
        jwk = next((item for item in candidates if item.get("kid") == kid), None)
    if jwk is None:
        raise HTTPException(status_code=401, detail="The provider assertion signing key is unknown.")
    try:
        _rsa_key(jwk).verify(
            signature,
            f"{parts[0]}.{parts[1]}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=401, detail="The provider assertion signature is invalid.") from exc

    current = int(time.time() if now is None else now)
    issuer = claims.get("iss")
    subject = claims.get("sub")
    audience_claim = claims.get("aud")
    expiration = claims.get("exp")
    issued_at = claims.get("iat")
    nonce_claim = claims.get("nonce")
    audiences = {audience_claim} if isinstance(audience_claim, str) else (
        set(audience_claim) if isinstance(audience_claim, list) and all(isinstance(item, str) for item in audience_claim) else set()
    )
    configured = _configured_ids(provider)
    expected_nonce = hashlib.sha256(raw_nonce.encode("ascii")).hexdigest() if provider == "apple" else raw_nonce
    authorized_party = claims.get("azp")
    if authorized_party is not None and (
        not isinstance(authorized_party, str)
        or authorized_party not in configured
        or authorized_party not in audiences
    ):
        raise HTTPException(status_code=401, detail="The provider assertion claims are invalid.")
    if (
        issuer not in _ISSUERS[provider]
        or not isinstance(subject, str) or not _SAFE_SUBJECT.fullmatch(subject)
        or not audiences.intersection(configured)
        or type(expiration) is not int or type(issued_at) is not int
        or expiration <= current - 30 or expiration <= issued_at or expiration > current + 86400
        or issued_at > current + 120 or issued_at < current - 86400
        or not isinstance(nonce_claim, str) or not secrets.compare_digest(nonce_claim, expected_nonce)
    ):
        raise HTTPException(status_code=401, detail="The provider assertion claims are invalid.")
    if len(audiences) > 1 and authorized_party is None:
        raise HTTPException(status_code=401, detail="The provider assertion claims are invalid.")
    audience = authorized_party or next(iter(audiences))

    verified_value = claims.get("email_verified")
    email_verified = verified_value is True or verified_value == "true"
    email = claims.get("email") if email_verified else None
    if not isinstance(email, str) or len(email) > 320 or "@" not in email or any(ord(char) < 32 for char in email):
        email = None
    name = claims.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or any(ord(char) < 32 for char in name):
        name = None
    return ProviderClaims(
        provider=provider,
        subject=subject,
        email=email,
        email_verified=email_verified and email is not None,
        name=name.strip() if name else None,
        assertion_hash=hashlib.sha256(identity_token.encode("ascii")).hexdigest(),
        audience=audience,
    )


def _apple_client_secret(now: int) -> tuple[str, str]:
    team_id = os.getenv("MAGISTRATE_APPLE_TEAM_ID", "").strip()
    key_id = os.getenv("MAGISTRATE_APPLE_KEY_ID", "").strip()
    private_key_raw = os.getenv("MAGISTRATE_APPLE_PRIVATE_KEY", "").strip().replace("\\n", "\n")
    client_id = os.getenv("MAGISTRATE_APPLE_SERVICE_ID", "").strip()
    if not all((team_id, key_id, private_key_raw, client_id)):
        raise HTTPException(status_code=503, detail="Apple web sign-in is not configured.")
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    claims = {"iss": team_id, "iat": now, "exp": now + 300, "aud": "https://appleid.apple.com", "sub": client_id}
    encode = lambda value: base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).rstrip(b"=")
    signing_input = encode(header) + b"." + encode(claims)
    try:
        key = serialization.load_pem_private_key(private_key_raw.encode("ascii"), password=None)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="Apple web sign-in configuration is invalid.") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise HTTPException(status_code=503, detail="Apple web sign-in configuration is invalid.")
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r_value, s_value = decode_dss_signature(der)
    signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii"), client_id


async def exchange_apple_code(code: str, redirect_uri: str) -> str:
    if not isinstance(code, str) or not 8 <= len(code) <= 4096 or any(ord(char) < 32 for char in code):
        raise HTTPException(status_code=401, detail="The Apple authorization code is invalid.")
    secret, client_id = _apple_client_secret(int(time.time()))
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            response = await client.post(
                "https://appleid.apple.com/auth/token",
                data={
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri, "client_id": client_id,
                    "client_secret": secret,
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Apple sign-in could not be completed.") from exc
    if response.status_code != 200 or len(response.content) > MAX_ID_TOKEN_BYTES * 2:
        raise HTTPException(status_code=401, detail="Apple rejected the authorization code.")
    payload = _json_object(response.content, label="Apple token response")
    token = payload.get("id_token")
    if not isinstance(token, str):
        raise HTTPException(status_code=401, detail="Apple returned an invalid sign-in response.")
    return token


def _display_name(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
        return None
    return normalized


def _new_refresh_token() -> str:
    return PROVIDER_REFRESH_PREFIX + secrets.token_urlsafe(40)


def _session_payload(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    account_id: str,
    provider: ProviderName,
    family_id: str,
    scopes: frozenset[str],
    now: int,
    refresh_expires_at: int,
    refresh_token: str,
    onboarding_required: bool,
) -> dict[str, Any]:
    payload = _insert_session(
        connection,
        user_id=user_id,
        scopes=scopes,
        now=now,
        expires_at=_configured_session_ttl(now, cap=refresh_expires_at),
        provider_session_id=family_id,
        auth_account_id=account_id,
    )
    return {
        **payload,
        "auth_method": provider,
        "refresh_expires_at": refresh_expires_at,
        "onboarding_required": onboarding_required,
        "_refresh_token": refresh_token,
    }


async def exchange_challenge(
    *,
    provider: ProviderName,
    challenge_id: str,
    raw_nonce: str,
    identity_token: Optional[str],
    authorization_code: Optional[str],
    redirect_uri: Optional[str],
    display_name: Optional[str],
    principal: Optional[Principal],
) -> dict[str, Any]:
    if provider not in {"apple", "google"} or not _SAFE_CHALLENGE.fullmatch(challenge_id or ""):
        raise HTTPException(status_code=401, detail="The sign-in challenge is invalid or expired.")
    if not isinstance(raw_nonce, str):
        raise HTTPException(status_code=401, detail="The sign-in challenge is invalid or expired.")
    db.init_db()
    now = int(time.time())
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        challenge = connection.execute(
            "SELECT * FROM provider_auth_challenges WHERE challenge_id = ?", (challenge_id,),
        ).fetchone()
    if (
        challenge is None or challenge["provider"] != provider
        or challenge["consumed_at"] is not None or int(challenge["expires_at"]) <= now
        or not secrets.compare_digest(
            str(challenge["nonce_hash"]), hashlib.sha256(raw_nonce.encode("utf-8")).hexdigest(),
        )
        or (challenge["action"] == "link" and (
            principal is None or principal.user_id != challenge["owner_user_id"]
        ))
        or (challenge["action"] == "sign_in" and principal is not None)
    ):
        raise HTTPException(status_code=401, detail="The sign-in challenge is invalid or expired.")
    expected_redirect = challenge["redirect_uri"]
    if redirect_uri != expected_redirect:
        raise HTTPException(status_code=401, detail="The sign-in redirect does not match its challenge.")
    if authorization_code:
        if provider != "apple" or identity_token:
            raise HTTPException(status_code=422, detail="The provider response is ambiguous.")
        identity_token = await exchange_apple_code(authorization_code, str(expected_redirect))
    if not identity_token or authorization_code and provider != "apple":
        raise HTTPException(status_code=422, detail="A provider identity assertion is required.")
    claims = await verify_identity_token(provider, identity_token, raw_nonce)
    platform_ids = _platform_client_ids(provider, str(challenge["client_platform"]))
    if platform_ids and claims.audience not in platform_ids:
        raise HTTPException(status_code=401, detail="The provider assertion does not match this client platform.")
    requested_name = _display_name(display_name) or claims.name
    scopes = _provider_scopes()

    connection = sqlite3.connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT * FROM provider_auth_challenges WHERE challenge_id = ?", (challenge_id,),
        ).fetchone()
        if (
            current is None or current["consumed_at"] is not None or int(current["expires_at"]) <= now
            or current["provider"] != provider
        ):
            raise HTTPException(status_code=409, detail="That sign-in response was already used.")
        replay = connection.execute(
            "SELECT 1 FROM provider_assertions WHERE assertion_hash = ?", (claims.assertion_hash,),
        ).fetchone()
        if replay:
            raise HTTPException(status_code=409, detail="That provider assertion was already used.")
        account = connection.execute(
            """SELECT * FROM connected_accounts
               WHERE provider = ? AND provider_user_id = ? AND account_kind = 'login'""",
            (provider, claims.subject),
        ).fetchone()
        action = str(current["action"])
        owner_user_id = principal.user_id if principal else None
        if account is not None and action == "link" and account["user_id"] != owner_user_id:
            raise HTTPException(status_code=409, detail="That provider identity belongs to another account.")
        if account is None:
            user_id = owner_user_id or ("usr_" + secrets.token_urlsafe(18))
            account_id = "login_" + secrets.token_urlsafe(18)
            connection.execute(
                """INSERT OR IGNORE INTO user_profiles
                   (user_id, name, email, avatar_url, bio, active_theme, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    user_id, requested_name or "", claims.email or "", "", "",
                    "dusk-mountain", now, now,
                ),
            )
            connection.execute(
                """INSERT INTO connected_accounts
                   (id, user_id, provider, provider_user_id, provider_username, status,
                    scopes, created_at, updated_at, account_kind, email_verified,
                    last_authenticated_at)
                   VALUES(?,?,?,?,?,'connected','openid,email,profile',?,?, 'login', ?, ?)""",
                (
                    account_id, user_id, provider, claims.subject, claims.email or "",
                    now, now, 1 if claims.email_verified else 0, now,
                ),
            )
        else:
            account_id = str(account["id"])
            user_id = str(account["user_id"])
            connection.execute(
                """UPDATE connected_accounts
                   SET status = 'connected', provider_username = CASE
                         WHEN provider_username = '' THEN ? ELSE provider_username END,
                       email_verified = MAX(email_verified, ?),
                       last_authenticated_at = ?, updated_at = ?
                   WHERE id = ? AND account_kind = 'login'""",
                (claims.email or "", 1 if claims.email_verified else 0, now, now, account_id),
            )
        profile = connection.execute(
            "SELECT name, email FROM user_profiles WHERE user_id = ?", (user_id,),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=503, detail="The account profile could not be established.")
        connection.execute(
            """UPDATE user_profiles
               SET name = CASE WHEN name = '' THEN ? ELSE name END,
                   email = CASE WHEN email = '' THEN ? ELSE email END,
                   updated_at = ? WHERE user_id = ?""",
            (requested_name or "", claims.email or "", now, user_id),
        )
        connection.execute(
            "UPDATE provider_auth_challenges SET consumed_at = ? WHERE challenge_id = ? AND consumed_at IS NULL",
            (now, challenge_id),
        )
        connection.execute(
            """INSERT INTO provider_assertions
               (assertion_hash, challenge_id, connected_account_id, accepted_at)
               VALUES(?,?,?,?)""",
            (claims.assertion_hash, challenge_id, account_id, now),
        )
        if action == "link":
            connection.commit()
            return {
                "schema_version": "provider-account-link.v1", "status": "linked",
                "provider": provider, "user_id": user_id,
            }

        family_id = "psf_" + secrets.token_urlsafe(18)
        refresh_token = _new_refresh_token()
        refresh_expires_at = _refresh_expiry(now)
        connection.execute(
            """INSERT INTO provider_session_families
               (family_id, user_id, connected_account_id, provider, client_platform,
                scopes, created_at, expires_at, last_rotated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                family_id, user_id, account_id, provider, str(current["client_platform"]),
                ",".join(sorted(scopes)), now, refresh_expires_at, now,
            ),
        )
        connection.execute(
            """INSERT INTO provider_refresh_tokens
               (token_hash, family_id, issued_at, expires_at)
               VALUES(?,?,?,?)""",
            (_hash_token(refresh_token), family_id, now, refresh_expires_at),
        )
        refreshed_profile = connection.execute(
            "SELECT name FROM user_profiles WHERE user_id = ?", (user_id,),
        ).fetchone()
        payload = _session_payload(
            connection, user_id=user_id, account_id=account_id, provider=provider,
            family_id=family_id, scopes=scopes, now=now,
            refresh_expires_at=refresh_expires_at, refresh_token=refresh_token,
            onboarding_required=not refreshed_profile or not str(refreshed_profile[0]).strip(),
        )
        payload["_client_platform"] = str(current["client_platform"])
        connection.commit()
        return payload
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def refresh_session(refresh_token: str, *, client_platform: ClientPlatform) -> dict[str, Any]:
    if client_platform not in {"native", "web"}:
        raise HTTPException(status_code=400, detail="The provider session channel is invalid.")
    if not isinstance(refresh_token, str) or not _SAFE_REFRESH.fullmatch(refresh_token):
        raise HTTPException(status_code=401, detail="The provider session is invalid or expired.")
    now = int(time.time())
    token_hash = _hash_token(refresh_token)
    db.init_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _cleanup_expired_session_families(connection, now)
        row = connection.execute(
            """SELECT token.consumed_at, token.expires_at AS token_expires_at,
                      family.*, account.status AS account_status,
                      account.account_kind AS account_kind
               FROM provider_refresh_tokens AS token
               JOIN provider_session_families AS family ON family.family_id = token.family_id
               JOIN connected_accounts AS account ON account.id = family.connected_account_id
               WHERE token.token_hash = ?""",
            (token_hash,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="The provider session is invalid or expired.")
        family_id = str(row["family_id"])
        compromise_reason = None
        if row["consumed_at"] is not None:
            compromise_reason = "refresh-reuse"
        elif row["client_platform"] != client_platform:
            # Delivery channel is part of the family authority. In particular,
            # posting a web-cookie token as JSON must never reveal its rotated
            # successor to JavaScript.
            compromise_reason = "refresh-channel-mismatch"
        if compromise_reason:
            connection.execute(
                """UPDATE provider_session_families
                   SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = ?
                   WHERE family_id = ?""",
                (now, compromise_reason, family_id),
            )
            connection.execute(
                "UPDATE gateway_sessions SET revoked_at = ? WHERE provider_session_id = ? AND revoked_at IS NULL",
                (now, family_id),
            )
            _revoke_push_delivery(connection, str(row["user_id"]), now)
            connection.commit()
            raise HTTPException(status_code=401, detail="The provider session was revoked.")
        invalid = (
            row["revoked_at"] is not None or int(row["expires_at"]) <= now
            or int(row["token_expires_at"]) <= now or row["account_status"] != "connected"
            or row["account_kind"] != "login" or row["provider"] not in {"apple", "google"}
        )
        if invalid:
            connection.execute(
                "UPDATE provider_session_families SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = COALESCE(revoke_reason, 'expired') WHERE family_id = ?",
                (now, family_id),
            )
            connection.execute(
                "UPDATE gateway_sessions SET revoked_at = ? WHERE provider_session_id = ? AND revoked_at IS NULL",
                (now, family_id),
            )
            connection.commit()
            raise HTTPException(status_code=401, detail="The provider session is invalid or expired.")
        scopes = frozenset(filter(None, str(row["scopes"]).split(",")))
        if not scopes or not scopes.issubset(KNOWN_SCOPES) or "response" in scopes:
            raise HTTPException(status_code=503, detail="Provider session configuration is invalid.")
        consumed = connection.execute(
            "UPDATE provider_refresh_tokens SET consumed_at = ? WHERE token_hash = ? AND consumed_at IS NULL",
            (now, token_hash),
        ).rowcount
        if consumed != 1:
            raise HTTPException(status_code=409, detail="The provider session changed; retry sign-in.")
        connection.execute(
            "UPDATE gateway_sessions SET revoked_at = ? WHERE provider_session_id = ? AND revoked_at IS NULL",
            (now, family_id),
        )
        next_refresh = _new_refresh_token()
        connection.execute(
            """INSERT INTO provider_refresh_tokens
               (token_hash, family_id, issued_at, expires_at) VALUES(?,?,?,?)""",
            (_hash_token(next_refresh), family_id, now, int(row["expires_at"])),
        )
        connection.execute(
            "UPDATE provider_session_families SET last_rotated_at = ? WHERE family_id = ?",
            (now, family_id),
        )
        profile = connection.execute(
            "SELECT name FROM user_profiles WHERE user_id = ?", (row["user_id"],),
        ).fetchone()
        payload = _session_payload(
            connection,
            user_id=str(row["user_id"]), account_id=str(row["connected_account_id"]),
            provider=str(row["provider"]), family_id=family_id, scopes=scopes, now=now,
            refresh_expires_at=int(row["expires_at"]), refresh_token=next_refresh,
            onboarding_required=not profile or not str(profile[0]).strip(),
        )
        connection.commit()
        return payload
    except HTTPException:
        if connection.in_transaction:
            connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def public_session_payload(payload: dict[str, Any], *, include_refresh_token: bool) -> dict[str, Any]:
    result = {key: value for key, value in payload.items() if key != "_refresh_token"}
    if include_refresh_token:
        result["refresh_token"] = payload["_refresh_token"]
    return result


def revoke_provider_family(connection: sqlite3.Connection, family_id: str, user_id: str, now: int) -> None:
    connection.execute(
        """UPDATE provider_session_families
           SET revoked_at = COALESCE(revoked_at, ?), revoke_reason = COALESCE(revoke_reason, 'logout')
           WHERE family_id = ?""",
        (now, family_id),
    )
    connection.execute(
        "UPDATE gateway_sessions SET revoked_at = ? WHERE provider_session_id = ? AND revoked_at IS NULL",
        (now, family_id),
    )
    _revoke_push_delivery(connection, user_id, now)
