"""Tenant-scoped GitHub App repository integration.

The Gateway owns provider authorization and read-only repository observation. It
never sends installation tokens to a client and it does not start, stop, or
schedule execution work.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import db
from app.auth import Principal, require_scope
from app.persistence import connect

GITHUB_API = "https://api.github.com"
MAX_WEBHOOK_BYTES = 2_000_000
MAX_SOURCE_BYTES = 1_000_000
REQUIRED_PERMISSIONS = {"contents": "read", "pull_requests": "read", "checks": "read", "metadata": "read"}
REQUIRED_EVENTS = frozenset({"installation", "installation_repositories", "repository", "pull_request", "check_run", "check_suite"})


class GitHubAppError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    slug: str
    private_key: str
    webhook_secret: str
    callback_base_url: str

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.slug and self.private_key and self.webhook_secret and self.callback_base_url)


def _private_key_value() -> str:
    inline = os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n").strip()
    path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
    if inline and path:
        raise RuntimeError("Configure only one of GITHUB_APP_PRIVATE_KEY and GITHUB_APP_PRIVATE_KEY_PATH.")
    if not path:
        return inline
    try:
        file_stat = os.stat(path, follow_symlinks=False)
        if not os.path.isabs(path) or not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY_PATH must name an absolute regular file.")
        if file_stat.st_uid not in {0, os.geteuid()}:
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY_PATH must be root/service-owned.")
        if file_stat.st_mode & 0o077:
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY_PATH must not be group/world accessible.")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as exc:
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY_PATH cannot be read safely.") from exc


def github_app_config() -> GitHubAppConfig:
    return GitHubAppConfig(
        app_id=os.getenv("GITHUB_APP_ID", "").strip(),
        slug=os.getenv("GITHUB_APP_SLUG", "").strip(),
        private_key=_private_key_value(),
        webhook_secret=os.getenv("GITHUB_APP_WEBHOOK_SECRET", "").strip(),
        callback_base_url=(os.getenv("MAGISTRATE_GITHUB_APP_CALLBACK_BASE_URL", "") or os.getenv("MAGISTRATE_OAUTH_CALLBACK_BASE_URL", "")).strip().rstrip("/"),
    )


def validate_github_app_configuration() -> bool:
    """Reject partially configured or unsafe activation without inventing values."""
    config = github_app_config()
    activation_fields = [config.app_id, config.slug, config.private_key, config.webhook_secret]
    if not any(activation_fields):
        return False
    if not all(activation_fields) or not config.callback_base_url:
        raise RuntimeError("GitHub App configuration is incomplete.")
    if not config.app_id.isdigit() or int(config.app_id) < 1:
        raise RuntimeError("GITHUB_APP_ID must be a positive integer.")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?", config.slug):
        raise RuntimeError("GITHUB_APP_SLUG is invalid.")
    parsed = urlsplit(config.callback_base_url)
    environment = os.getenv("MAGISTRATE_ENV", "").strip().lower()
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (environment in db.DEVELOPMENT_MODES and parsed.scheme == "http" and local):
        raise RuntimeError("The GitHub App callback base URL must use HTTPS.")
    if len(config.webhook_secret) < 32:
        raise RuntimeError("GITHUB_APP_WEBHOOK_SECRET must contain at least 32 characters.")
    try:
        key = serialization.load_pem_private_key(config.private_key.encode("utf-8"), password=None)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY is not a valid unencrypted PEM private key.") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY must be an RSA key of at least 2048 bits.")
    redirects = [item.strip() for item in os.getenv(
        "MAGISTRATE_GITHUB_APP_REDIRECT_URIS", "magistrate://prs"
    ).split(",") if item.strip()]
    if not redirects:
        raise RuntimeError("MAGISTRATE_GITHUB_APP_REDIRECT_URIS must permit at least one exact return URL.")
    try:
        for redirect in redirects:
            _safe_redirect(redirect)
    except GitHubAppError as exc:
        raise RuntimeError("MAGISTRATE_GITHUB_APP_REDIRECT_URIS contains an unsafe return URL.") from exc
    return True


def github_app_readiness() -> dict[str, Any]:
    try:
        configured = validate_github_app_configuration()
        config = github_app_config()
    except RuntimeError:
        configured = False
        config = None
    return {
        "schema_version": "github-app-readiness.v1",
        "status": "configured" if configured else "BLOCKED_EXTERNAL",
        "configured": configured,
        "app_slug": config.slug if configured and config else None,
        "required_permissions": REQUIRED_PERMISSIONS,
        "required_events": sorted(REQUIRED_EVENTS),
        "repository_selection": "selected_repositories_recommended",
        "installation_tokens": "server-only",
    }


def _now() -> int:
    return int(time.time())


def _safe_redirect(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise GitHubAppError("The return URL is not permitted.", 400)
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise GitHubAppError("The return URL is not permitted.", 400) from None
    structurally_safe = (
        not parsed.username and not parsed.password and not parsed.fragment
        and not parsed.query and (
            (parsed.scheme == "magistrate" and bool(parsed.netloc or parsed.path))
            or (parsed.scheme == "https" and bool(parsed.netloc))
            or (os.getenv("MAGISTRATE_ENV", "").lower() in db.DEVELOPMENT_MODES
                and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    )
    allowed = {
        item.strip() for item in os.getenv(
            "MAGISTRATE_GITHUB_APP_REDIRECT_URIS", "magistrate://prs"
        ).split(",") if item.strip()
    }
    if structurally_safe and value in allowed:
        return value
    raise GitHubAppError("The return URL is not permitted.", 400)


def _redirect(url: str, **params: str) -> str:
    parsed = urlsplit(url)
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    query.extend((key, value) for key, value in params.items() if value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class GitHubAppStore:
    def create_transaction(self, user_id: str, redirect_uri: str) -> str:
        redirect_uri = _safe_redirect(redirect_uri)
        state = secrets.token_urlsafe(32)
        now = _now()
        with connect(db.DB_PATH) as connection:
            connection.execute("DELETE FROM github_app_transactions WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO github_app_transactions(state_hash,user_id,redirect_uri,created_at,expires_at) VALUES(?,?,?,?,?)",
                (hashlib.sha256(state.encode("ascii")).hexdigest(), user_id, redirect_uri, now, now + 600),
            )
        return state

    def consume_transaction(self, state: str) -> tuple[str, str]:
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
            raise GitHubAppError("The GitHub installation state is invalid.", 400)
        digest = hashlib.sha256(state.encode("ascii", "strict")).hexdigest()
        now = _now()
        with connect(db.DB_PATH) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id,redirect_uri,expires_at,consumed_at FROM github_app_transactions WHERE state_hash=?",
                (digest,),
            ).fetchone()
            if not row or row[2] <= now or row[3] is not None:
                raise GitHubAppError("The GitHub installation state is expired or already used.", 400)
            connection.execute("UPDATE github_app_transactions SET consumed_at=? WHERE state_hash=?", (now, digest))
        return str(row[0]), str(row[1])

    @staticmethod
    def _installation_values(payload: dict[str, Any]) -> tuple[Any, ...]:
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        return (
            int(payload["id"]), account.get("id"), str(account.get("login") or "")[:255],
            str(account.get("type") or "")[:40], str(payload.get("repository_selection") or "selected")[:20],
            json.dumps(permissions, sort_keys=True, separators=(",", ":")),
            json.dumps(events, sort_keys=True, separators=(",", ":")), _now(),
        )

    def upsert_installation(self, payload: dict[str, Any], *, user_id: Optional[str] = None, status: Optional[str] = None) -> None:
        if not isinstance(payload.get("id"), int) or isinstance(payload.get("id"), bool) or payload["id"] < 1:
            raise GitHubAppError("GitHub installation payload is invalid.", 400)
        values = self._installation_values(payload)
        with connect(db.DB_PATH) as connection:
            existing = connection.execute("SELECT user_id,created_at FROM github_app_installations WHERE installation_id=?", (values[0],)).fetchone()
            if existing and user_id and existing[0] and existing[0] != user_id:
                raise GitHubAppError("This GitHub installation is already assigned to another account.", 409)
            owner = user_id or (existing[0] if existing else None)
            created = existing[1] if existing else _now()
            if status is None:
                current = connection.execute("SELECT status FROM github_app_installations WHERE installation_id=?", (values[0],)).fetchone()
                status = str(current[0]) if current else "active"
            connection.execute(
                """INSERT INTO github_app_installations
                   (installation_id,user_id,account_id,account_login,account_type,repository_selection,status,
                    permissions_json,events_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(installation_id) DO UPDATE SET user_id=excluded.user_id,account_id=excluded.account_id,
                    account_login=excluded.account_login,account_type=excluded.account_type,
                    repository_selection=excluded.repository_selection,status=excluded.status,
                    permissions_json=excluded.permissions_json,events_json=excluded.events_json,updated_at=excluded.updated_at""",
                (values[0], owner, values[1], values[2], values[3], values[4], status, values[5], values[6], created, values[7]),
            )

    def set_installation_status(self, installation_id: int, status: str) -> None:
        now = _now()
        with connect(db.DB_PATH) as connection:
            connection.execute(
                "UPDATE github_app_installations SET status=?,suspended_at=?,updated_at=? WHERE installation_id=?",
                (status, now if status == "suspended" else None, now, installation_id),
            )
            if status == "removed":
                connection.execute("UPDATE github_app_repositories SET active=0,deleted_at=?,updated_at=? WHERE installation_id=?", (now, now, installation_id))

    def installations(self, user_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT installation_id,account_login,account_type,repository_selection,status,permissions_json,events_json,suspended_at,last_reconciled_at FROM github_app_installations WHERE user_id=?"
        params: list[Any] = [user_id]
        if active_only:
            query += " AND status='active'"
        query += " ORDER BY installation_id"
        with connect(db.DB_PATH) as connection:
            rows = connection.execute(query, params).fetchall()
        return [{
            "installation_id": row[0], "account_login": row[1], "account_type": row[2],
            "repository_selection": row[3], "status": row[4], "permissions": json.loads(row[5]),
            "events": json.loads(row[6]), "suspended_at": row[7], "last_reconciled_at": row[8],
        } for row in rows]

    def installation_for_user(self, user_id: str, installation_id: int, *, require_active: bool = True) -> dict[str, Any]:
        rows = [item for item in self.installations(user_id) if item["installation_id"] == installation_id]
        if not rows:
            raise GitHubAppError("GitHub installation was not found.", 404)
        if require_active and rows[0]["status"] != "active":
            raise GitHubAppError("GitHub installation is not active.", 409)
        return rows[0]

    def upsert_repository(self, installation_id: int, repo: dict[str, Any], *, active: bool = True) -> None:
        owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
        repo_id = repo.get("id")
        if not isinstance(repo_id, int) or isinstance(repo_id, bool) or repo_id < 1:
            raise GitHubAppError("GitHub repository payload is invalid.", 400)
        name = str(repo.get("name") or "")[:255]
        owner_login = str(owner.get("login") or "")[:255]
        full_name = str(repo.get("full_name") or (f"{owner_login}/{name}" if owner_login and name else ""))[:512]
        with connect(db.DB_PATH) as connection:
            connection.execute(
                """INSERT INTO github_app_repositories
                   (installation_id,repository_id,owner_login,name,full_name,private,default_branch,html_url,active,deleted_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(installation_id,repository_id) DO UPDATE SET owner_login=excluded.owner_login,
                    name=excluded.name,full_name=excluded.full_name,private=excluded.private,
                    default_branch=excluded.default_branch,html_url=excluded.html_url,active=excluded.active,
                    deleted_at=excluded.deleted_at,updated_at=excluded.updated_at""",
                (installation_id, repo_id, owner_login, name, full_name, int(repo.get("private") is True),
                 str(repo.get("default_branch") or "")[:255], str(repo.get("html_url") or "")[:1024],
                 int(active), None if active else _now(), _now()),
            )

    def remove_repositories(self, installation_id: int, repository_ids: list[int]) -> None:
        if not repository_ids:
            return
        placeholders = ",".join("?" for _ in repository_ids)
        with connect(db.DB_PATH) as connection:
            connection.execute(
                f"UPDATE github_app_repositories SET active=0,deleted_at=?,updated_at=? WHERE installation_id=? AND repository_id IN ({placeholders})",
                (_now(), _now(), installation_id, *repository_ids),
            )

    def replace_repositories(self, installation_id: int, repositories: list[dict[str, Any]]) -> None:
        seen = [int(repo["id"]) for repo in repositories if isinstance(repo.get("id"), int) and not isinstance(repo.get("id"), bool)]
        for repo in repositories:
            self.upsert_repository(installation_id, repo)
        with connect(db.DB_PATH) as connection:
            if seen:
                placeholders = ",".join("?" for _ in seen)
                connection.execute(f"UPDATE github_app_repositories SET active=0,deleted_at=?,updated_at=? WHERE installation_id=? AND repository_id NOT IN ({placeholders})", (_now(), _now(), installation_id, *seen))
            else:
                connection.execute("UPDATE github_app_repositories SET active=0,deleted_at=?,updated_at=? WHERE installation_id=?", (_now(), _now(), installation_id))
            connection.execute("UPDATE github_app_installations SET last_reconciled_at=?,updated_at=? WHERE installation_id=?", (_now(), _now(), installation_id))

    def repositories(self, user_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        query = """SELECT r.installation_id,r.repository_id,r.owner_login,r.name,r.full_name,r.private,
                          r.default_branch,r.html_url,r.active,r.deleted_at,r.updated_at
                   FROM github_app_repositories r JOIN github_app_installations i
                     ON i.installation_id=r.installation_id WHERE i.user_id=?"""
        if not include_inactive:
            query += " AND i.status='active' AND r.active=1"
        query += " ORDER BY lower(r.full_name),r.repository_id"
        with connect(db.DB_PATH) as connection:
            rows = connection.execute(query, (user_id,)).fetchall()
        return [{"installation_id": r[0], "id": r[1], "owner": r[2], "name": r[3], "full_name": r[4],
                 "private": bool(r[5]), "default_branch": r[6], "html_url": r[7], "active": bool(r[8]),
                 "deleted_at": r[9], "updated_at": r[10]} for r in rows]

    def repository_for_user(self, user_id: str, repository_id: int) -> dict[str, Any]:
        item = next((repo for repo in self.repositories(user_id) if repo["id"] == repository_id), None)
        if not item:
            raise GitHubAppError("Repository was not found or is no longer authorized.", 404)
        return item

    def begin_delivery(self, delivery_id: str, event: str, action: Optional[str], payload_sha: str) -> bool:
        now = _now()
        with connect(db.DB_PATH) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_sha256,status FROM github_webhook_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row:
                if not hmac.compare_digest(row[0], payload_sha):
                    raise GitHubAppError("Webhook delivery identifier was reused with different content.", 409)
                if row[1] == "processed":
                    return False
                if row[1] == "processing":
                    raise GitHubAppError("Webhook delivery is already being processed.", 503)
                connection.execute("UPDATE github_webhook_deliveries SET status='processing',error_code=NULL WHERE delivery_id=?", (delivery_id,))
                return True
            connection.execute("INSERT INTO github_webhook_deliveries VALUES(?,?,?,?,?,?,?,?)", (delivery_id, event, action, payload_sha, "processing", now, None, None))
        return True

    def finish_delivery(self, delivery_id: str, *, error: Optional[str] = None) -> None:
        with connect(db.DB_PATH) as connection:
            connection.execute("UPDATE github_webhook_deliveries SET status=?,processed_at=?,error_code=? WHERE delivery_id=?", ("failed" if error else "processed", _now(), error, delivery_id))


class GitHubAppService:
    def __init__(self, store: Optional[GitHubAppStore] = None):
        self.store = store or GitHubAppStore()
        self._tokens: dict[int, tuple[str, int]] = {}

    def _config(self) -> GitHubAppConfig:
        if not validate_github_app_configuration():
            raise GitHubAppError("GitHub App activation is not configured.", 503)
        return github_app_config()

    def app_jwt(self) -> str:
        config = self._config()
        now = _now()
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=")
        claims = base64.urlsafe_b64encode(json.dumps({"iat": now - 30, "exp": now + 540, "iss": config.app_id}, separators=(",", ":")).encode()).rstrip(b"=")
        signing_input = header + b"." + claims
        key = serialization.load_pem_private_key(config.private_key.encode(), password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii")

    async def _request(self, method: str, path: str, *, token: str, params: Optional[dict[str, Any]] = None, json_body: Any = None) -> Any:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.request(method, GITHUB_API + path, headers=headers, params=params, json=json_body)
        if response.status_code == 404:
            raise GitHubAppError("GitHub resource was not found or is not authorized.", 404)
        if response.status_code in {401, 403}:
            raise GitHubAppError("GitHub rejected the installation authority.", 502)
        if response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubAppError("GitHub rate limit reached; try again later.", 503)
        if response.status_code >= 400:
            raise GitHubAppError("GitHub could not complete the repository request.", 502)
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubAppError("GitHub returned an unreadable response.", 502) from exc

    async def app_request(self, method: str, path: str) -> Any:
        return await self._request(method, path, token=self.app_jwt())

    async def installation_token(self, installation_id: int, *, renew: bool = False) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > _now() + 60 and not renew:
            return cached[0]
        data = await self._request("POST", f"/app/installations/{installation_id}/access_tokens", token=self.app_jwt(), json_body={"permissions": {key: value for key, value in REQUIRED_PERMISSIONS.items() if key != "metadata"}})
        token = data.get("token") if isinstance(data, dict) else None
        expires = data.get("expires_at") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token or not isinstance(expires, str):
            raise GitHubAppError("GitHub did not issue an installation token.", 502)
        try:
            expiration = int(datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp())
        except ValueError as exc:
            raise GitHubAppError("GitHub returned an invalid token expiration.", 502) from exc
        self._tokens[installation_id] = (token, expiration)
        return token

    async def installation_request(self, installation_id: int, method: str, path: str, *, params: Optional[dict[str, Any]] = None) -> Any:
        token = await self.installation_token(installation_id)
        try:
            return await self._request(method, path, token=token, params=params)
        except GitHubAppError as exc:
            if exc.status_code != 502:
                raise
            self._tokens.pop(installation_id, None)
            return await self._request(method, path, token=await self.installation_token(installation_id, renew=True), params=params)

    @staticmethod
    def validate_installation_permissions(payload: dict[str, Any]) -> None:
        permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
        missing = [name for name in REQUIRED_PERMISSIONS if permissions.get(name) not in {"read", "write"}]
        if missing:
            raise GitHubAppError(
                "GitHub App installation is missing required read permissions: " + ", ".join(sorted(missing)) + ".",
                409,
            )
        events = set(payload.get("events") or []) if isinstance(payload.get("events"), list) else set()
        missing_events = sorted(REQUIRED_EVENTS - events)
        if missing_events:
            raise GitHubAppError(
                "GitHub App is missing required webhook subscriptions: " + ", ".join(missing_events) + ".",
                409,
            )

    async def bind_installation(self, user_id: str, installation_id: int) -> None:
        payload = await self.app_request("GET", f"/app/installations/{installation_id}")
        if not isinstance(payload, dict) or payload.get("id") != installation_id:
            raise GitHubAppError("GitHub returned a different installation.", 502)
        self.validate_installation_permissions(payload)
        self.store.upsert_installation(payload, user_id=user_id, status="suspended" if payload.get("suspended_at") else "active")
        if not payload.get("suspended_at"):
            await self.reconcile(user_id, installation_id)

    async def reconcile(self, user_id: str, installation_id: int) -> list[dict[str, Any]]:
        self.store.installation_for_user(user_id, installation_id)
        try:
            installation = await self.app_request("GET", f"/app/installations/{installation_id}")
        except GitHubAppError as exc:
            if exc.status_code == 404:
                self.store.set_installation_status(installation_id, "removed")
            raise
        if not isinstance(installation, dict) or installation.get("id") != installation_id:
            raise GitHubAppError("GitHub returned a different installation.", 502)
        self.validate_installation_permissions(installation)
        status = "suspended" if installation.get("suspended_at") else "active"
        self.store.upsert_installation(installation, user_id=user_id, status=status)
        if status == "suspended":
            return []
        repositories: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            data = await self.installation_request(installation_id, "GET", "/installation/repositories", params={"per_page": 100, "page": page})
            batch = data.get("repositories") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise GitHubAppError("GitHub returned invalid repository data.", 502)
            repositories.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                break
            if page == 20:
                raise GitHubAppError("GitHub installation has more than the supported 2,000 repositories.", 422)
            page += 1
        self.store.replace_repositories(installation_id, repositories)
        return [repo for repo in self.store.repositories(user_id) if repo["installation_id"] == installation_id]

    async def repo_request(self, user_id: str, repository_id: int, suffix: str, *, params: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], Any]:
        repo = self.store.repository_for_user(user_id, repository_id)
        path = f"/repos/{quote(repo['owner'], safe='')}/{quote(repo['name'], safe='')}{suffix}"
        return repo, await self.installation_request(repo["installation_id"], "GET", path, params=params)

    @staticmethod
    def normalize_pull(repo: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        body = str(item.get("body") or "")
        summary = " ".join(body.replace("#", " ").split())[:240] or str(item.get("title") or "Pull request")
        return {"id": item.get("id"), "number": item.get("number"), "title": item.get("title"), "repository": repo["full_name"],
                "repository_id": repo["id"], "author": user.get("login") or "unknown", "branch": head.get("ref"), "head_sha": head.get("sha"),
                "state": str(item.get("state") or "unknown").upper(), "is_draft": item.get("draft") is True,
                "review_status": "REVIEW_REQUIRED", "mergeable": str(item.get("mergeable_state") or "UNKNOWN").upper(),
                "checks": {"status": "UNKNOWN", "passed": 0, "failed": 0, "pending": 0, "summary": "Open details for checks"},
                "reviews": [], "created_at": item.get("created_at"), "updated_at": item.get("updated_at"), "merged_at": item.get("merged_at"),
                "summary": summary, "body": body, "requires_attention": item.get("state") == "open" and item.get("draft") is not True,
                "url": item.get("html_url")}

    async def get_pull_requests(self, user_id: str, page: int = 1, per_page: int = 20, refresh: bool = False) -> dict[str, Any]:
        del refresh
        all_items: list[dict[str, Any]] = []
        for repo in self.store.repositories(user_id):
            _, data = await self.repo_request(user_id, repo["id"], "/pulls", params={"state": "open", "per_page": 100, "page": 1})
            if isinstance(data, list):
                all_items.extend(self.normalize_pull(repo, item) for item in data if isinstance(item, dict))
        all_items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        start = (page - 1) * per_page
        return {"items": all_items[start:start + per_page], "page": page, "per_page": per_page, "has_more": len(all_items) > start + per_page, "cached": False}

    async def get_pull_request(self, user_id: str, number: int, repository_id: Optional[int] = None, refresh: bool = False) -> dict[str, Any]:
        del refresh
        repositories = self.store.repositories(user_id)
        if repository_id is not None:
            repositories = [self.store.repository_for_user(user_id, repository_id)]
        matches: list[dict[str, Any]] = []
        for repo in repositories:
            try:
                _, item = await self.repo_request(user_id, repo["id"], f"/pulls/{number}")
                if isinstance(item, dict):
                    matches.append(self.normalize_pull(repo, item))
            except GitHubAppError as exc:
                if exc.status_code != 404:
                    raise
        if not matches:
            raise GitHubAppError("Pull request was not found in an authorized repository.", 404)
        if len(matches) > 1 and repository_id is None:
            raise GitHubAppError("Pull request number is ambiguous; select a repository.", 409)
        return matches[0]

    async def get_merged_pull_requests(self, user_id: str, limit: int = 20, refresh: bool = False) -> list[dict[str, Any]]:
        del refresh
        items: list[dict[str, Any]] = []
        for repo in self.store.repositories(user_id):
            _, data = await self.repo_request(user_id, repo["id"], "/pulls", params={"state": "closed", "per_page": min(100, limit)})
            if isinstance(data, list):
                items.extend(self.normalize_pull(repo, item) for item in data if isinstance(item, dict) and item.get("merged_at"))
        return sorted(items, key=lambda item: item.get("merged_at") or "", reverse=True)[:limit]

    def process_webhook(self, event: str, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "")
        installation = payload.get("installation") if isinstance(payload.get("installation"), dict) else {}
        installation_id = installation.get("id")
        if event == "installation" and isinstance(installation_id, int):
            if action == "deleted":
                self._tokens.pop(installation_id, None)
                self.store.set_installation_status(installation_id, "removed")
            elif action == "suspend":
                self._tokens.pop(installation_id, None)
                self.store.set_installation_status(installation_id, "suspended")
            else:
                status = "suspended" if installation.get("suspended_at") else "active"
                self.store.upsert_installation(installation, status=status)
        elif event == "installation_repositories" and isinstance(installation_id, int):
            self._tokens.pop(installation_id, None)
            self.store.upsert_installation(installation)
            for repo in payload.get("repositories_added") or []:
                if isinstance(repo, dict):
                    self.store.upsert_repository(installation_id, repo)
            removed = [item.get("id") for item in (payload.get("repositories_removed") or []) if isinstance(item, dict) and isinstance(item.get("id"), int)]
            self.store.remove_repositories(installation_id, removed)
        elif event == "repository" and isinstance(installation_id, int) and isinstance(payload.get("repository"), dict):
            if action == "deleted":
                self.store.remove_repositories(installation_id, [payload["repository"].get("id")])
            else:
                self.store.upsert_repository(installation_id, payload["repository"])


github_app_store = GitHubAppStore()
github_app_service = GitHubAppService(github_app_store)
router = APIRouter(prefix="/api/v1/github", tags=["github-app"])


def _raise(exc: GitHubAppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/app/status")
async def app_status(principal: Principal = Depends(require_scope("providers"))):
    readiness = github_app_readiness()
    installations = github_app_store.installations(principal.user_id)
    return {**readiness, "installations": installations, "repository_count": len(github_app_store.repositories(principal.user_id))}


@router.post("/app/install")
async def app_install(payload: dict[str, Any], principal: Principal = Depends(require_scope("providers"))):
    try:
        config = github_app_service._config()
        state = github_app_store.create_transaction(principal.user_id, str(payload.get("redirect_uri") or "magistrate://account"))
        return {"auth_url": f"https://github.com/apps/{quote(config.slug, safe='')}/installations/new?{urlencode({'state': state})}", "expires_in": 600}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/app/callback")
async def app_callback(installation_id: Optional[int] = Query(None), state: str = Query(...), setup_action: str = Query("install")):
    try:
        user_id, redirect_uri = github_app_store.consume_transaction(state)
        if setup_action not in {"install", "update"} or installation_id is None:
            return RedirectResponse(_redirect(redirect_uri, github_error="installation_cancelled"))
        if installation_id < 1:
            raise GitHubAppError("GitHub installation identifier is invalid.", 400)
        await github_app_service.bind_installation(user_id, installation_id)
        return RedirectResponse(_redirect(redirect_uri, github_status="installed"))
    except GitHubAppError as exc:
        if "redirect_uri" in locals():
            return RedirectResponse(_redirect(redirect_uri, github_error="installation_failed"))
        _raise(exc)


@router.post("/installations/{installation_id}/reconcile")
async def reconcile_installation(installation_id: int, principal: Principal = Depends(require_scope("providers"))):
    try:
        items = await github_app_service.reconcile(principal.user_id, installation_id)
        return {"status": "reconciled", "installation_id": installation_id, "repositories": items}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories")
async def list_repositories(principal: Principal = Depends(require_scope("providers"))):
    return {"items": github_app_store.repositories(principal.user_id)}


@router.get("/repositories/{repository_id}/branches")
async def list_branches(repository_id: int, page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100), principal: Principal = Depends(require_scope("providers"))):
    try:
        repo, items = await github_app_service.repo_request(principal.user_id, repository_id, "/branches", params={"page": page, "per_page": per_page})
        return {"repository": repo, "items": items if isinstance(items, list) else []}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories/{repository_id}/commits")
async def list_commits(repository_id: int, ref: Optional[str] = Query(None, max_length=255), page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100), principal: Principal = Depends(require_scope("providers"))):
    try:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if ref:
            params["sha"] = ref
        repo, items = await github_app_service.repo_request(principal.user_id, repository_id, "/commits", params=params)
        return {"repository": repo, "items": items if isinstance(items, list) else []}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories/{repository_id}/contents")
async def read_contents(repository_id: int, path: str = Query("", max_length=2048), ref: Optional[str] = Query(None, max_length=255), principal: Principal = Depends(require_scope("providers"))):
    try:
        clean_path = path.strip("/")
        segments = clean_path.split("/") if clean_path else []
        if any(segment in {"", ".", ".."} or "\\" in segment or any(ord(char) < 32 for char in segment) for segment in segments):
            raise GitHubAppError("The requested source path is invalid.", 422)
        suffix = "/contents" + ("/" + quote(clean_path, safe="/") if clean_path else "")
        repo, item = await github_app_service.repo_request(principal.user_id, repository_id, suffix, params={"ref": ref} if ref else None)
        if isinstance(item, list):
            return {"repository": repo, "path": path, "kind": "directory", "items": [{key: entry.get(key) for key in ("name", "path", "sha", "size", "type", "html_url")} for entry in item if isinstance(entry, dict)]}
        if not isinstance(item, dict) or item.get("type") != "file" or item.get("encoding") != "base64":
            raise GitHubAppError("The requested source is not a readable file.", 422)
        if not isinstance(item.get("size"), int) or item["size"] > MAX_SOURCE_BYTES:
            raise GitHubAppError("The requested source file is too large.", 413)
        try:
            encoded = "".join(str(item.get("content") or "").split())
            if len(encoded) > ((MAX_SOURCE_BYTES + 2) // 3) * 4:
                raise GitHubAppError("The requested source file is too large.", 413)
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) > MAX_SOURCE_BYTES:
                raise GitHubAppError("The requested source file is too large.", 413)
            content = decoded.decode("utf-8")
        except GitHubAppError:
            raise
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubAppError("The requested source file is not UTF-8 text.", 422) from exc
        return {"repository": repo, "path": item.get("path"), "kind": "file", "sha": item.get("sha"), "size": len(decoded), "content": content, "encoding": "utf-8"}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories/{repository_id}/pulls")
async def list_repository_pulls(repository_id: int, state: str = Query("open", pattern="^(open|closed|all)$"), page: int = Query(1, ge=1), per_page: int = Query(30, ge=1, le=100), principal: Principal = Depends(require_scope("providers"))):
    try:
        repo, items = await github_app_service.repo_request(principal.user_id, repository_id, "/pulls", params={"state": state, "page": page, "per_page": per_page})
        return {"repository": repo, "items": [github_app_service.normalize_pull(repo, item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories/{repository_id}/pulls/{number}")
async def get_repository_pull(repository_id: int, number: int, principal: Principal = Depends(require_scope("providers"))):
    try:
        return await github_app_service.get_pull_request(principal.user_id, number, repository_id)
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/repositories/{repository_id}/commits/{ref}/checks")
async def list_checks(repository_id: int, ref: str, principal: Principal = Depends(require_scope("providers"))):
    try:
        repo, data = await github_app_service.repo_request(principal.user_id, repository_id, f"/commits/{quote(ref, safe='')}/check-runs", params={"per_page": 100})
        return {"repository": repo, "total_count": data.get("total_count", 0) if isinstance(data, dict) else 0, "items": data.get("check_runs", []) if isinstance(data, dict) else []}
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/pulls")
async def list_pulls(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50), refresh: bool = Query(False), principal: Principal = Depends(require_scope("providers"))):
    try:
        return await github_app_service.get_pull_requests(principal.user_id, page, per_page, refresh)
    except GitHubAppError as exc:
        _raise(exc)


@router.get("/pulls/{number}")
async def get_pull(number: int, repository_id: Optional[int] = Query(None), refresh: bool = Query(False), principal: Principal = Depends(require_scope("providers"))):
    try:
        return await github_app_service.get_pull_request(principal.user_id, number, repository_id, refresh)
    except GitHubAppError as exc:
        _raise(exc)


@router.post("/webhooks")
async def github_webhook(request: Request, x_github_event: Optional[str] = Header(None), x_github_delivery: Optional[str] = Header(None), x_hub_signature_256: Optional[str] = Header(None)):
    try:
        config = github_app_service._config()
        if not x_github_event or not re.fullmatch(r"[a-z_]{1,64}", x_github_event):
            raise GitHubAppError("Missing or invalid GitHub event header.", 400)
        if not x_github_delivery or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", x_github_delivery):
            raise GitHubAppError("Missing or invalid GitHub delivery header.", 400)
        body = await request.body()
        if len(body) > MAX_WEBHOOK_BYTES:
            raise GitHubAppError("Webhook payload is too large.", 413)
        expected = "sha256=" + hmac.new(config.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
            raise GitHubAppError("Webhook signature is invalid.", 401)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAppError("Webhook payload is invalid JSON.", 400) from exc
        if not isinstance(payload, dict):
            raise GitHubAppError("Webhook payload must be an object.", 400)
        action = str(payload.get("action"))[:64] if payload.get("action") is not None else None
        digest = hashlib.sha256(body).hexdigest()
        if not github_app_store.begin_delivery(x_github_delivery, x_github_event, action, digest):
            return {"status": "duplicate", "delivery_id": x_github_delivery}
        try:
            github_app_service.process_webhook(x_github_event, payload)
        except Exception:
            github_app_store.finish_delivery(x_github_delivery, error="processing_failed")
            raise
        github_app_store.finish_delivery(x_github_delivery)
        return {"status": "processed", "delivery_id": x_github_delivery}
    except GitHubAppError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
