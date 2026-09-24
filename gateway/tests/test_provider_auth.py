import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.provider_auth import (
    ProviderClaims, validate_provider_auth_configuration, verify_identity_token,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwt(private_key, kid, claims):
    header = _b64(json.dumps({"alg": "RS256", "kid": kid}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signed = f"{header}.{payload}".encode("ascii")
    signature = private_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


@pytest.mark.asyncio
async def test_google_assertion_is_signature_audience_time_and_nonce_bound(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_GOOGLE_CLIENT_IDS", "google-client.apps.example")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com", "sub": "provider-subject-1",
        "aud": "google-client.apps.example", "iat": now, "exp": now + 300,
        "nonce": "n" * 43, "email": "person@example.com", "email_verified": True,
        "name": "Person One",
    }
    token = _jwt(key, "test-key", claims)
    verified = await verify_identity_token("google", token, "n" * 43, keys=[jwk], now=now)
    assert verified.subject == "provider-subject-1"
    assert verified.email == "person@example.com"
    assert verified.audience == "google-client.apps.example"
    with pytest.raises(HTTPException) as mismatch:
        await verify_identity_token("google", token, "x" * 43, keys=[jwk], now=now)
    assert mismatch.value.status_code == 401


@pytest.mark.asyncio
async def test_apple_assertion_requires_the_sha256_bound_nonce(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_APPLE_CLIENT_IDS", "io.magistrate.test")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "alg": "RS256", "use": "sig", "kid": "apple-test-key",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    now = int(time.time())
    raw_nonce = "a" * 43
    token = _jwt(key, "apple-test-key", {
        "iss": "https://appleid.apple.com", "sub": "apple-provider-subject",
        "aud": "io.magistrate.test", "iat": now, "exp": now + 300,
        "nonce": hashlib.sha256(raw_nonce.encode("ascii")).hexdigest(),
    })
    verified = await verify_identity_token("apple", token, raw_nonce, keys=[jwk], now=now)
    assert verified.subject == "apple-provider-subject"
    with pytest.raises(HTTPException) as mismatch:
        await verify_identity_token("apple", token, "b" * 43, keys=[jwk], now=now)
    assert mismatch.value.status_code == 401


def test_public_provider_configuration_is_platform_specific(monkeypatch):
    for name in (
        "MAGISTRATE_APPLE_CLIENT_IDS", "MAGISTRATE_APPLE_SERVICE_ID",
        "MAGISTRATE_APPLE_TEAM_ID", "MAGISTRATE_APPLE_KEY_ID",
        "MAGISTRATE_APPLE_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGISTRATE_GOOGLE_WEB_CLIENT_IDS", "google-web-client")
    monkeypatch.delenv("MAGISTRATE_GOOGLE_IOS_CLIENT_IDS", raising=False)
    monkeypatch.delenv("MAGISTRATE_GOOGLE_CLIENT_IDS", raising=False)
    configuration = TestClient(app).get("/api/v1/auth/provider/configuration")
    assert configuration.status_code == 200
    assert configuration.json() == {
        "schema_version": "provider-auth-configuration.v1",
        "apple": False, "apple_native": False, "apple_web": False,
        "google": True, "google_native": False, "google_web": True,
    }


def test_production_provider_configuration_requires_both_redirect_channels(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_ENV", "production")
    for name in (
        "MAGISTRATE_APPLE_CLIENT_IDS", "MAGISTRATE_APPLE_SERVICE_ID",
        "MAGISTRATE_APPLE_TEAM_ID", "MAGISTRATE_APPLE_KEY_ID",
        "MAGISTRATE_APPLE_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGISTRATE_GOOGLE_WEB_CLIENT_IDS", "google-web-client")
    monkeypatch.setenv("MAGISTRATE_GOOGLE_IOS_CLIENT_IDS", "google-ios-client")
    monkeypatch.delenv("MAGISTRATE_GOOGLE_CLIENT_IDS", raising=False)
    monkeypatch.delenv("MAGISTRATE_AUTH_REDIRECT_URIS", raising=False)
    with pytest.raises(RuntimeError, match="redirect configuration is incomplete"):
        validate_provider_auth_configuration()

    monkeypatch.setenv("MAGISTRATE_AUTH_REDIRECT_URIS", "https://app.example.test/")
    with pytest.raises(RuntimeError, match="redirect configuration is incomplete"):
        validate_provider_auth_configuration()

    monkeypatch.setenv(
        "MAGISTRATE_AUTH_REDIRECT_URIS",
        "https://app.example.test/,com.googleusercontent.apps.example:/oauthredirect",
    )
    validate_provider_auth_configuration()


def test_client_platform_cannot_select_the_native_refresh_channel_from_a_web_redirect(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_GOOGLE_CLIENT_IDS", "google-web-client,google-ios-client")
    native_with_web_redirect = TestClient(app).post(
        "/api/v1/auth/provider/challenge",
        json={
            "provider": "google", "client_platform": "native",
            "redirect_uri": "https://app.example.test/",
        },
    )
    assert native_with_web_redirect.status_code == 422


def test_web_provider_session_is_cookie_backed_revocable_and_principal_stable(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_GOOGLE_CLIENT_IDS", "google-web-client")
    monkeypatch.setenv("MAGISTRATE_GOOGLE_WEB_CLIENT_IDS", "google-web-client")
    sequence = iter(["assertion-web-1", "assertion-web-2"])

    async def verified(provider, identity_token, raw_nonce):
        assert provider == "google"
        return ProviderClaims(
            provider="google", subject="stable-google-subject", email="stable@example.com",
            email_verified=True, name="Stable Person",
            assertion_hash=hashlib.sha256(next(sequence).encode()).hexdigest(),
            audience="google-web-client",
        )

    monkeypatch.setattr("app.provider_auth.verify_identity_token", verified)
    client = TestClient(app)

    challenge = client.post("/api/v1/auth/provider/challenge", json={
        "provider": "google", "client_platform": "web",
        "redirect_uri": "https://app.example.test/",
    })
    assert challenge.status_code == 200
    nonce = challenge.json()["nonce"]
    signed_in = client.post("/api/v1/auth/provider/exchange", json={
        "provider": "google", "challenge_id": challenge.json()["challenge_id"],
        "nonce": nonce, "identity_token": "first-token",
        "redirect_uri": "https://app.example.test/",
    })
    assert signed_in.status_code == 200
    assert "refresh_token" not in signed_in.json()
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    first_user = signed_in.json()["user_id"]
    token = signed_in.json()["session_token"]
    inspected = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert inspected.json()["auth_method"] == "google"

    refreshed = client.post("/api/v1/auth/provider/refresh", json={})
    assert refreshed.status_code == 200
    assert "refresh_token" not in refreshed.json()
    refreshed_access = refreshed.json()["session_token"]
    web_refresh = client.cookies.get("magistrate_provider_refresh")
    assert web_refresh
    # A cookie-backed family's authority can never be converted into a JSON
    # refresh credential, even by a caller that somehow learns its current bytes.
    crossed_channel = TestClient(app).post(
        "/api/v1/auth/provider/refresh", json={"refresh_token": web_refresh},
    )
    assert crossed_channel.status_code == 401
    assert "refresh_token" not in crossed_channel.json()
    assert client.get(
        "/api/v1/auth/session", headers={"Authorization": f"Bearer {refreshed_access}"},
    ).status_code == 401

    challenge2 = client.post("/api/v1/auth/provider/challenge", json={
        "provider": "google", "client_platform": "web",
        "redirect_uri": "https://app.example.test/",
    }).json()
    signed_in_again = client.post("/api/v1/auth/provider/exchange", json={
        "provider": "google", "challenge_id": challenge2["challenge_id"],
        "nonce": challenge2["nonce"], "identity_token": "second-token",
        "redirect_uri": "https://app.example.test/",
    })
    assert signed_in_again.status_code == 200
    assert signed_in_again.json()["user_id"] == first_user
    second_access = signed_in_again.json()["session_token"]
    revoked = client.post(
        "/api/v1/auth/session/revoke", headers={"Authorization": f"Bearer {second_access}"},
    )
    assert revoked.status_code == 200
    assert client.get(
        "/api/v1/auth/session", headers={"Authorization": f"Bearer {second_access}"},
    ).status_code == 401
    assert client.post("/api/v1/auth/provider/refresh", json={}).status_code == 401


def test_native_refresh_rotates_and_reuse_retires_the_family(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_APPLE_CLIENT_IDS", "io.magistrate.test")
    counter = 0

    async def verified(provider, identity_token, raw_nonce):
        nonlocal counter
        counter += 1
        return ProviderClaims(
            provider="apple", subject="stable-apple-subject", email=None,
            email_verified=False, name="Apple Person",
            assertion_hash=hashlib.sha256(f"apple-{counter}".encode()).hexdigest(),
            audience="io.magistrate.test",
        )

    monkeypatch.setattr("app.provider_auth.verify_identity_token", verified)
    client = TestClient(app)
    challenge = client.post("/api/v1/auth/provider/challenge", json={
        "provider": "apple", "client_platform": "native",
    }).json()
    signed_in = client.post("/api/v1/auth/provider/exchange", json={
        "provider": "apple", "challenge_id": challenge["challenge_id"],
        "nonce": challenge["nonce"], "identity_token": "apple-token",
    })
    assert signed_in.status_code == 200
    old_refresh = signed_in.json()["refresh_token"]

    rotated = client.post("/api/v1/auth/provider/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != old_refresh
    rotated_access = rotated.json()["session_token"]

    replay = client.post("/api/v1/auth/provider/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    assert client.get(
        "/api/v1/auth/session", headers={"Authorization": f"Bearer {rotated_access}"},
    ).status_code == 401
