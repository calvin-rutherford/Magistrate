import json
import sqlite3
import stat
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import (
    create_friend_beta_access_grant,
    list_friend_beta_access_grants,
    revoke_friend_beta_access_grant,
)
from app.main import app
from app.notifications import get_registered_push_token, list_registered_push_users, register_push_token
from scripts.friend_beta_access import main as access_cli


client = TestClient(app)


def _user() -> str:
    return f"friend-{uuid.uuid4().hex}"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_friend_beta_endpoint_is_explicitly_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MAGISTRATE_FRIEND_BETA_ENABLED", raising=False)
    response = client.post("/api/v1/auth/friend-beta/session", json={"access_code": "mgb_" + "A" * 43})
    assert response.status_code == 503
    assert "access code" not in response.text.lower()


def test_default_grant_is_digest_only_and_principal_scoped(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    user_id = _user()
    grant = create_friend_beta_access_grant(user_id)
    access_code = grant["access_code"]

    assert grant["scopes"] == ["account", "notifications", "read"]
    with sqlite3.connect(db.DB_PATH) as connection:
        row = connection.execute(
            "SELECT code_hash, user_id, scopes FROM friend_beta_access_grants WHERE grant_id = ?",
            (grant["grant_id"],),
        ).fetchone()
        assert row[1] == user_id
        assert row[2] == "account,notifications,read"
        assert row[0] != access_code
        assert access_code not in " ".join(str(value) for value in row)

    issued = client.post("/api/v1/auth/friend-beta/session", json={"access_code": access_code})
    assert issued.status_code == 200
    payload = issued.json()
    assert payload["user_id"] == user_id
    assert payload["auth_method"] == "friend-beta-access"
    assert payload["onboarding_required"] is True
    assert "access_code" not in payload
    assert access_code not in issued.text

    inspected = client.get("/api/v1/auth/session", headers=_headers(payload["session_token"]))
    assert inspected.status_code == 200
    assert inspected.json()["user_id"] == user_id
    assert inspected.json()["onboarding_required"] is True
    # Observer access cannot reach the shared command boundary.
    assert client.post(
        "/api/v1/captain/prompt",
        headers=_headers(payload["session_token"]),
        json={"text": "do work"},
    ).status_code == 403

    assert client.post(
        "/api/v1/account/profile",
        headers=_headers(payload["session_token"]),
        data={"name": "Beta\u2028Friend"},
    ).status_code == 422
    profile = client.post(
        "/api/v1/account/profile",
        headers=_headers(payload["session_token"]),
        data={"name": "Beta Friend"},
    )
    assert profile.status_code == 200
    assert profile.json()["user_id"] == user_id
    assert client.get(
        "/api/v1/auth/session", headers=_headers(payload["session_token"]),
    ).json()["onboarding_required"] is False


def test_disabling_friend_beta_immediately_closes_existing_friend_sessions_and_push(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    grant = create_friend_beta_access_grant(_user())
    issued = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": grant["access_code"]},
    ).json()
    register_push_token(grant["user_id"], "ExponentPushToken[friend-beta-device]")
    assert grant["user_id"] in list_registered_push_users()

    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "false")
    assert client.get(
        "/api/v1/auth/session", headers=_headers(issued["session_token"]),
    ).status_code == 401
    assert grant["user_id"] not in list_registered_push_users()
    assert get_registered_push_token(grant["user_id"]) is None
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    assert client.get(
        "/api/v1/auth/session", headers=_headers(issued["session_token"]),
    ).status_code == 401


def test_redeeming_again_replaces_only_that_grants_previous_bearer(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    first_grant = create_friend_beta_access_grant(_user())
    other_grant = create_friend_beta_access_grant(_user())

    first = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": first_grant["access_code"]},
    ).json()
    other = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": other_grant["access_code"]},
    ).json()
    replacement = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": first_grant["access_code"]},
    ).json()

    assert client.get("/api/v1/auth/session", headers=_headers(first["session_token"])).status_code == 401
    assert client.get("/api/v1/auth/session", headers=_headers(replacement["session_token"])).status_code == 200
    assert client.get("/api/v1/auth/session", headers=_headers(other["session_token"])).status_code == 200


def test_logout_or_operator_revoke_ends_grant_and_all_sessions(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    logout_grant = create_friend_beta_access_grant(_user())
    issued = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": logout_grant["access_code"]},
    ).json()
    register_push_token(logout_grant["user_id"], "ExponentPushToken[logged-out-friend]")
    assert logout_grant["user_id"] in list_registered_push_users()
    assert client.post(
        "/api/v1/auth/session/revoke", headers=_headers(issued["session_token"]),
    ).status_code == 200
    assert client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": logout_grant["access_code"]},
    ).status_code == 401
    assert logout_grant["user_id"] not in list_registered_push_users()
    assert get_registered_push_token(logout_grant["user_id"]) is None

    operator_grant = create_friend_beta_access_grant(_user())
    operator_session = client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": operator_grant["access_code"]},
    ).json()
    register_push_token(operator_grant["user_id"], "ExponentPushToken[operator-revoked-friend]")
    assert operator_grant["user_id"] in list_registered_push_users()
    assert revoke_friend_beta_access_grant(operator_grant["grant_id"])
    assert client.get(
        "/api/v1/auth/session", headers=_headers(operator_session["session_token"]),
    ).status_code == 401
    assert list_friend_beta_access_grants(user_id=operator_grant["user_id"])[0]["state"] == "revoked"
    assert operator_grant["user_id"] not in list_registered_push_users()
    assert get_registered_push_token(operator_grant["user_id"]) is None


def test_expired_grants_and_malformed_configuration_fail_closed(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "true")
    then = int(time.time()) - 7200
    expired = create_friend_beta_access_grant(_user(), ttl_seconds=3600, now=then)
    register_push_token(expired["user_id"], "ExponentPushToken[expired-friend]")
    assert expired["user_id"] not in list_registered_push_users()
    assert get_registered_push_token(expired["user_id"]) is None
    assert client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": expired["access_code"]},
    ).status_code == 401
    replacement = create_friend_beta_access_grant(expired["user_id"])
    assert replacement["grant_id"] != expired["grant_id"]
    assert get_registered_push_token(expired["user_id"]) is None
    register_push_token(expired["user_id"], "ExponentPushToken[replacement-friend]")
    assert expired["user_id"] in list_registered_push_users()

    monkeypatch.setenv("MAGISTRATE_FRIEND_BETA_ENABLED", "sometimes")
    assert client.post(
        "/api/v1/auth/friend-beta/session", json={"access_code": "mgb_" + "A" * 43},
    ).status_code == 503


@pytest.mark.parametrize("scopes", [
    ["read", "account", "command"],
    ["read", "account", "voice"],
])
def test_shared_runtime_scopes_require_explicit_acknowledgement(scopes):
    with pytest.raises(ValueError, match="shared-runtime"):
        create_friend_beta_access_grant(_user(), scopes=scopes)
    granted = create_friend_beta_access_grant(
        _user(), scopes=scopes, allow_shared_runtime_access=True,
    )
    assert set(granted["scopes"]) == set(scopes)


def test_one_principal_cannot_accumulate_active_device_grants():
    user_id = _user()
    first = create_friend_beta_access_grant(user_id)
    with pytest.raises(ValueError, match="active grant"):
        create_friend_beta_access_grant(user_id)
    assert revoke_friend_beta_access_grant(first["grant_id"])
    replacement = create_friend_beta_access_grant(user_id)
    assert replacement["grant_id"] != first["grant_id"]


def test_friend_principal_identifier_is_path_safe():
    for user_id in ("../friend", "friend/name", "friend name", "friend@example.test", "x" * 65):
        with pytest.raises(ValueError, match="URL-safe"):
            create_friend_beta_access_grant(user_id)


def test_response_producer_scope_can_never_be_issued_to_a_friend():
    with pytest.raises(ValueError, match="producer"):
        create_friend_beta_access_grant(
            _user(), scopes=["read", "account", "response"],
            allow_shared_runtime_access=True,
        )


def test_existing_session_table_is_migrated_additively(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as connection:
        connection.execute(
            """CREATE TABLE gateway_sessions (
               session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
               user_id TEXT NOT NULL, scopes TEXT NOT NULL, issued_at INTEGER NOT NULL,
               expires_at INTEGER NOT NULL, revoked_at INTEGER)""",
        )
        connection.execute(
            "INSERT INTO gateway_sessions VALUES(?,?,?,?,?,?,NULL)",
            ("legacy-session", "legacy-hash", "legacy-user", "read", 1, 4102444800),
        )
    monkeypatch.setattr(db, "DB_PATH", str(legacy_path))
    db.init_db()
    with sqlite3.connect(legacy_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(gateway_sessions)")}
        row = connection.execute(
            "SELECT session_id, token_hash, user_id, scopes, access_grant_id FROM gateway_sessions WHERE session_id='legacy-session'",
        ).fetchone()
        assert "access_grant_id" in columns
        assert row == ("legacy-session", "legacy-hash", "legacy-user", "read", None)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='friend_beta_access_grants'",
        ).fetchone() == ("friend_beta_access_grants",)


def test_cli_refuses_to_write_access_codes_inside_the_repository():
    user_id = _user()
    output = Path(__file__).resolve().parents[2] / f".friend-beta-{uuid.uuid4().hex}.json"
    assert access_cli([
        "issue", "--user-id", user_id, "--output", str(output),
    ]) == 2
    assert not output.exists()
    assert list_friend_beta_access_grants(user_id=user_id) == []


def test_cli_writes_the_only_code_copy_to_a_new_mode_0600_file(tmp_path, capsys):
    output = tmp_path / "invite.json"
    result = access_cli([
        "issue", "--user-id", _user(), "--ttl-hours", "24", "--output", str(output),
    ])
    assert result == 0
    visible = json.loads(capsys.readouterr().out)
    stored = json.loads(output.read_text())
    assert "access_code" not in visible
    assert stored["access_code"].startswith("mgb_")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    original = output.read_bytes()
    assert access_cli([
        "issue", "--user-id", _user(), "--ttl-hours", "24", "--output", str(output),
    ]) == 2
    assert output.read_bytes() == original
