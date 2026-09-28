import hashlib
import hmac
import json
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import db
from app.github_app import (github_app_readiness, github_app_service,
                            github_app_store,
                            validate_github_app_configuration)
from app.main import app

client = TestClient(app)


@pytest.fixture
def github_config(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("GITHUB_APP_ID", "24680")
    monkeypatch.setenv("GITHUB_APP_SLUG", "magistrate-test-app")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "test-webhook-secret-that-is-long-enough")
    monkeypatch.setenv("MAGISTRATE_GITHUB_APP_CALLBACK_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("MAGISTRATE_GITHUB_APP_REDIRECT_URIS", "magistrate://prs")
    github_app_service._tokens.clear()
    return "test-webhook-secret-that-is-long-enough"


def _session(user_id: str) -> dict[str, str]:
    token = f"token-{user_id}-{time.time_ns()}"
    now = int(time.time())
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO user_profiles(user_id,name,email,created_at,updated_at) VALUES(?,?,?,?,?)",
            (user_id, user_id, f"{user_id}@example.test", now, now),
        )
        connection.execute(
            """INSERT INTO gateway_sessions(session_id,token_hash,user_id,scopes,issued_at,expires_at)
               VALUES(?,?,?,?,?,?)""",
            (f"session-{user_id}-{time.time_ns()}", hashlib.sha256(token.encode()).hexdigest(), user_id,
             "account,providers,read", now, now + 3600),
        )
    return {"Authorization": f"Bearer {token}"}


def _seed(user_id: str, installation_id: int, repository_id: int, name: str) -> None:
    github_app_store.upsert_installation({
        "id": installation_id,
        "account": {"id": installation_id + 1000, "login": user_id, "type": "Organization"},
        "repository_selection": "selected",
        "permissions": {"contents": "read", "pull_requests": "read", "checks": "read", "metadata": "read"},
        "events": ["installation", "installation_repositories", "repository"],
    }, user_id=user_id)
    github_app_store.upsert_repository(installation_id, {
        "id": repository_id, "name": name, "full_name": f"{user_id}/{name}",
        "owner": {"login": user_id}, "private": True, "default_branch": "main",
        "html_url": f"https://github.com/{user_id}/{name}",
    })


def test_configuration_is_truthful_and_partial_activation_fails(monkeypatch):
    for name in ("GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH", "GITHUB_APP_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGISTRATE_OAUTH_CALLBACK_BASE_URL", "https://gateway.example.test")
    assert validate_github_app_configuration() is False
    assert github_app_readiness()["status"] == "BLOCKED_EXTERNAL"
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    with pytest.raises(RuntimeError, match="incomplete"):
        validate_github_app_configuration()


@pytest.mark.asyncio
async def test_installation_token_is_server_only_cached_and_renewed(github_config, monkeypatch):
    issued = []

    async def fake_request(method, path, *, token, params=None, json_body=None):
        assert path == "/app/installations/991/access_tokens"
        assert token.count(".") == 2
        assert json_body == {"permissions": {"contents": "read", "pull_requests": "read", "checks": "read"}}
        issued.append(True)
        return {"token": "server-only-installation-token", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(github_app_service, "_request", fake_request)
    first = await github_app_service.installation_token(991)
    second = await github_app_service.installation_token(991)
    assert first == second == "server-only-installation-token"
    assert len(issued) == 1

    status = client.get("/api/v1/github/app/status", headers=_session("token-owner")).json()
    assert "server-only-installation-token" not in json.dumps(status)
    assert status["installation_tokens"] == "server-only"


def test_install_return_binds_authenticated_principal(github_config, monkeypatch):
    headers = _session("install-owner")
    rejected = client.post("/api/v1/github/app/install", headers=headers, json={"redirect_uri": "https://attacker.example/return"})
    assert rejected.status_code == 400
    start = client.post("/api/v1/github/app/install", headers=headers, json={"redirect_uri": "magistrate://prs"})
    assert start.status_code == 200
    state = parse_qs(urlsplit(start.json()["auth_url"]).query)["state"][0]

    async def app_request(method, path):
        assert (method, path) == ("GET", "/app/installations/5501")
        return {"id": 5501, "account": {"id": 99, "login": "acme", "type": "Organization"},
                "repository_selection": "selected", "permissions": {"contents": "read", "pull_requests": "read", "checks": "read", "metadata": "read"},
                "events": ["installation", "installation_repositories", "repository", "pull_request", "check_run", "check_suite"]}

    async def reconcile(user_id, installation_id):
        assert (user_id, installation_id) == ("install-owner", 5501)
        return []

    monkeypatch.setattr(github_app_service, "app_request", app_request)
    monkeypatch.setattr(github_app_service, "reconcile", reconcile)
    response = client.get("/api/v1/github/app/callback", params={"installation_id": 5501, "state": state, "setup_action": "install"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "magistrate://prs?github_status=installed"
    assert github_app_store.installation_for_user("install-owner", 5501)["account_login"] == "acme"

    replay = client.get("/api/v1/github/app/callback", params={"installation_id": 5501, "state": state}, follow_redirects=False)
    assert replay.status_code == 400


def test_installation_cannot_be_rebound_to_a_second_tenant(github_config, monkeypatch):
    _seed("binding-owner", 6001, 7001, "owned")
    headers = _session("binding-attacker")
    start = client.post("/api/v1/github/app/install", headers=headers, json={"redirect_uri": "magistrate://prs"})
    state = parse_qs(urlsplit(start.json()["auth_url"]).query)["state"][0]

    async def app_request(method, path):
        return {"id": 6001, "account": {"id": 44, "login": "binding-owner", "type": "Organization"},
                "repository_selection": "selected", "permissions": {"contents": "read", "pull_requests": "read", "checks": "read", "metadata": "read"},
                "events": ["installation", "installation_repositories", "repository", "pull_request", "check_run", "check_suite"]}

    monkeypatch.setattr(github_app_service, "app_request", app_request)
    response = client.get("/api/v1/github/app/callback", params={"installation_id": 6001, "state": state}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "magistrate://prs?github_error=installation_failed"
    assert github_app_store.installation_for_user("binding-owner", 6001)["account_login"] == "binding-owner"
    with pytest.raises(Exception, match="not found"):
        github_app_store.installation_for_user("binding-attacker", 6001)


def test_repository_reads_are_isolated_between_two_tenants(github_config, monkeypatch):
    _seed("tenant-a", 6101, 7101, "alpha-private")
    _seed("tenant-b", 6102, 7102, "beta-private")
    a_headers = _session("tenant-a")
    b_headers = _session("tenant-b")

    assert [item["id"] for item in client.get("/api/v1/github/repositories", headers=a_headers).json()["items"]] == [7101]
    assert [item["id"] for item in client.get("/api/v1/github/repositories", headers=b_headers).json()["items"]] == [7102]
    assert client.get("/api/v1/github/repositories/7102/branches", headers=a_headers).status_code == 404

    calls = []

    async def installation_request(installation_id, method, path, *, params=None):
        calls.append((installation_id, method, path, params))
        return [{"name": "main", "commit": {"sha": "abc"}}]

    monkeypatch.setattr(github_app_service, "installation_request", installation_request)
    response = client.get("/api/v1/github/repositories/7102/branches", headers=b_headers)
    assert response.status_code == 200
    assert calls == [(6102, "GET", "/repos/tenant-b/beta-private/branches", {"page": 1, "per_page": 30})]
    assert "token" not in response.text.lower()


def test_explicit_reconciliation_repairs_repository_selection(github_config, monkeypatch):
    _seed("reconcile-owner", 6351, 7351, "removed")
    headers = _session("reconcile-owner")
    installation = {"id": 6351, "account": {"id": 8, "login": "reconcile-owner", "type": "Organization"},
                    "repository_selection": "selected",
                    "permissions": {"contents": "read", "pull_requests": "read", "checks": "read", "metadata": "read"},
                    "events": ["installation", "installation_repositories", "repository", "pull_request", "check_run", "check_suite"]}
    replacement = {"id": 7352, "name": "selected", "full_name": "reconcile-owner/selected",
                   "owner": {"login": "reconcile-owner"}, "private": True, "default_branch": "main",
                   "html_url": "https://github.com/reconcile-owner/selected"}

    async def app_request(method, path):
        return installation

    async def installation_request(installation_id, method, path, *, params=None):
        assert path == "/installation/repositories"
        return {"repositories": [replacement]}

    monkeypatch.setattr(github_app_service, "app_request", app_request)
    monkeypatch.setattr(github_app_service, "installation_request", installation_request)
    response = client.post("/api/v1/github/installations/6351/reconcile", headers=headers)
    assert response.status_code == 200
    assert [repo["id"] for repo in response.json()["repositories"]] == [7352]
    with pytest.raises(Exception, match="no longer authorized"):
        github_app_store.repository_for_user("reconcile-owner", 7351)


def test_source_branches_commits_pulls_and_checks_use_authorized_installation(github_config, monkeypatch):
    _seed("source-owner", 6401, 7401, "workspace")
    headers = _session("source-owner")

    async def installation_request(installation_id, method, path, *, params=None):
        assert installation_id == 6401
        if path.endswith("/contents/src/main.py"):
            return {"type": "file", "path": "src/main.py", "sha": "source-sha", "size": 12,
                    "encoding": "base64", "content": "cHJpbnQoJ29rJykK\n"}
        if path.endswith("/commits/main/check-runs"):
            return {"total_count": 1, "check_runs": [{"id": 4, "name": "test", "status": "completed", "conclusion": "success"}]}
        if path.endswith("/commits"):
            return [{"sha": "commit-sha"}]
        if path.endswith("/pulls"):
            return [{"id": 55, "number": 5, "title": "Read source", "state": "open", "draft": False,
                     "user": {"login": "captain"}, "head": {"ref": "feature", "sha": "head-sha"},
                     "body": "Body", "html_url": "https://github.com/source-owner/workspace/pull/5"}]
        if path.endswith("/branches"):
            return [{"name": "main", "commit": {"sha": "commit-sha"}}]
        raise AssertionError(path)

    monkeypatch.setattr(github_app_service, "installation_request", installation_request)
    assert client.get("/api/v1/github/repositories/7401/branches", headers=headers).json()["items"][0]["name"] == "main"
    assert client.get("/api/v1/github/repositories/7401/commits?ref=main", headers=headers).json()["items"][0]["sha"] == "commit-sha"
    assert client.get("/api/v1/github/repositories/7401/pulls", headers=headers).json()["items"][0]["repository_id"] == 7401
    assert client.get("/api/v1/github/repositories/7401/commits/main/checks", headers=headers).json()["items"][0]["conclusion"] == "success"
    source = client.get("/api/v1/github/repositories/7401/contents", params={"path": "src/main.py", "ref": "main"}, headers=headers)
    assert source.status_code == 200
    assert source.json()["content"] == "print('ok')\n"
    assert client.get("/api/v1/github/repositories/7401/contents", params={"path": "../other"}, headers=headers).status_code == 422


def _webhook(secret: str, delivery: str, event: str, payload: dict):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post("/api/v1/github/webhooks", content=body, headers={
        "Content-Type": "application/json", "X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
    })


def test_verified_webhook_lifecycle_is_idempotent_and_tracks_rename_privacy_delete(github_config):
    secret = github_config
    _seed("webhook-owner", 6201, 7201, "before")
    installation = {"id": 6201, "account": {"id": 1, "login": "webhook-owner", "type": "Organization"},
                    "repository_selection": "selected", "permissions": {}, "events": []}
    renamed = {"id": 7201, "name": "after", "full_name": "webhook-owner/after", "owner": {"login": "webhook-owner"},
               "private": False, "default_branch": "trunk", "html_url": "https://github.com/webhook-owner/after"}

    bad = client.post("/api/v1/github/webhooks", content=b"{}", headers={"X-GitHub-Event": "repository", "X-GitHub-Delivery": "bad", "X-Hub-Signature-256": "sha256=bad"})
    assert bad.status_code == 401

    renamed_response = _webhook(secret, "delivery-rename", "repository", {"action": "renamed", "installation": installation, "repository": renamed})
    assert renamed_response.status_code == 200
    assert _webhook(secret, "delivery-rename", "repository", {"action": "renamed", "installation": installation, "repository": renamed}).json()["status"] == "duplicate"
    repo = github_app_store.repository_for_user("webhook-owner", 7201)
    assert (repo["full_name"], repo["private"], repo["default_branch"]) == ("webhook-owner/after", False, "trunk")

    assert _webhook(secret, "delivery-private", "repository", {"action": "privatized", "installation": installation,
        "repository": {**renamed, "private": True}}).status_code == 200
    assert github_app_store.repository_for_user("webhook-owner", 7201)["private"] is True

    assert _webhook(secret, "delivery-suspend", "installation", {"action": "suspend", "installation": installation}).status_code == 200
    assert github_app_store.repositories("webhook-owner") == []
    assert _webhook(secret, "delivery-unsuspend", "installation", {"action": "unsuspend", "installation": installation}).status_code == 200
    assert github_app_store.repository_for_user("webhook-owner", 7201)["id"] == 7201

    assert _webhook(secret, "delivery-delete", "repository", {"action": "deleted", "installation": installation, "repository": renamed}).status_code == 200
    with pytest.raises(Exception, match="no longer authorized"):
        github_app_store.repository_for_user("webhook-owner", 7201)


def test_repository_access_change_and_install_removal_fail_closed(github_config):
    secret = github_config
    _seed("access-owner", 6301, 7301, "one")
    installation = {"id": 6301, "account": {"id": 1, "login": "access-owner", "type": "Organization"},
                    "repository_selection": "selected", "permissions": {}, "events": []}
    two = {"id": 7302, "name": "two", "full_name": "access-owner/two", "owner": {"login": "access-owner"},
           "private": True, "default_branch": "main", "html_url": "https://github.com/access-owner/two"}
    response = _webhook(secret, "delivery-access", "installation_repositories", {
        "action": "removed", "installation": installation, "repositories_added": [two], "repositories_removed": [{"id": 7301}],
    })
    assert response.status_code == 200
    assert [repo["id"] for repo in github_app_store.repositories("access-owner")] == [7302]

    assert _webhook(secret, "delivery-remove", "installation", {"action": "deleted", "installation": installation}).status_code == 200
    assert github_app_store.repositories("access-owner") == []
