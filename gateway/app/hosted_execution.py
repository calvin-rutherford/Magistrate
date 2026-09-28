"""Provider-neutral hosted execution for public-SaaS objectives.

The Gateway owns durable objective/lease state.  An external isolation backend
owns ephemeral container/VM lifecycle through a small mTLS API.  Product reads
never call that backend and workers receive no user or provider credential.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path as FilePath
import re
import secrets
import sqlite3
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.billing import BillingError
from app.persistence import connect
from app.firstmate_execution import (
    FirstmateExecutionConflict,
    FirstmateExecutionEventContract,
    FirstmateExecutionNotFound,
    FirstmateMeasuredUsage,
    FirstmateNativeChatOrigin,
    FirstmateObjectiveAcceptedEvent,
    FirstmateObjectiveFacts,
    FirstmateProgressEvent,
)

_ENABLED = {"1", "true", "yes", "on"}
_IMAGE = re.compile(r"^[A-Za-z0-9._/:@-]+@sha256:[0-9a-f]{64}$")
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PERMISSION = re.compile(r"^(?:contents|pull_requests|issues|workflows):(read|write)$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_NO_WORKER_USAGE = FirstmateMeasuredUsage(
    provider="magistrate", model="no-worker", input_tokens=0,
    output_tokens=0, compute_milliseconds=0,
)


def _now_ms() -> int:
    return int(time.time_ns() // 1_000_000)


def hosted_execution_enabled() -> bool:
    return os.getenv("MAGISTRATE_HOSTED_EXECUTION_ENABLED", "").strip().lower() in _ENABLED


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _https_url(name: str) -> str:
    value = os.getenv(name, "").strip().rstrip("/")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.query or parsed.path not in {"", "/"}):
        raise RuntimeError(f"{name} must be an HTTPS origin without embedded credentials")
    return value


def _private_file(name: str, *, secret: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if not value or not os.path.isabs(value) or os.path.realpath(value) != os.path.abspath(value):
        raise RuntimeError(f"{name} must be an absolute symlink-free file path")
    try:
        info = os.stat(value, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"{name} is unavailable") from exc
    unsafe_mode = info.st_mode & (0o077 if secret else 0o022)
    if (not FilePath(value).is_file() or os.path.islink(value) or unsafe_mode
            or info.st_uid not in {0, os.geteuid()}):
        raise RuntimeError(f"{name} has unsafe ownership or permissions")
    return value


@dataclass(frozen=True)
class HostedExecutionConfig:
    worker_image: str
    gateway_url: str
    backend_url: str
    github_broker_url: str
    client_cert_path: str
    client_key_path: str
    ca_path: str
    identity_key: bytes
    network_hosts: tuple[str, ...]
    github_permissions: tuple[str, ...]
    max_global: int
    max_per_tenant: int
    cpu_millis: int
    memory_mib: int
    workspace_mib: int
    deadline_seconds: int
    cleanup_seconds: int
    poll_seconds: int
    lease_ms: int = 60_000

    @classmethod
    def from_env(cls) -> "HostedExecutionConfig | None":
        if not hosted_execution_enabled():
            return None
        image = os.getenv("MAGISTRATE_WORKER_IMAGE", "").strip()
        if not _IMAGE.fullmatch(image):
            raise RuntimeError("MAGISTRATE_WORKER_IMAGE must be pinned by sha256 digest")
        identity_key = os.getenv("MAGISTRATE_WORKER_IDENTITY_KEY", "").encode()
        if len(identity_key) < 32:
            raise RuntimeError("MAGISTRATE_WORKER_IDENTITY_KEY must contain at least 32 bytes")
        gateway_url = _https_url("MAGISTRATE_WORKER_GATEWAY_URL")
        gateway_host = (urlparse(gateway_url).hostname or "").lower()
        hosts = tuple(dict.fromkeys(
            item.strip().lower() for item in os.getenv("MAGISTRATE_WORKER_NETWORK_HOSTS", "").split(",") if item.strip()
        ))
        if not hosts or gateway_host not in hosts or any(not _HOST.fullmatch(host) for host in hosts):
            raise RuntimeError("MAGISTRATE_WORKER_NETWORK_HOSTS must be a hostname allowlist containing the Gateway")
        permissions = tuple(dict.fromkeys(
            item.strip() for item in os.getenv("MAGISTRATE_GITHUB_PERMISSIONS", "").split(",") if item.strip()
        ))
        if not permissions or any(not _PERMISSION.fullmatch(item) for item in permissions):
            raise RuntimeError("MAGISTRATE_GITHUB_PERMISSIONS must be an explicit bounded permission list")
        return cls(
            worker_image=image,
            gateway_url=gateway_url,
            backend_url=_https_url("MAGISTRATE_ISOLATION_BACKEND_URL"),
            github_broker_url=_https_url("MAGISTRATE_GITHUB_TOKEN_BROKER_URL"),
            client_cert_path=_private_file("MAGISTRATE_ISOLATION_CLIENT_CERT"),
            client_key_path=_private_file("MAGISTRATE_ISOLATION_CLIENT_KEY", secret=True),
            ca_path=_private_file("MAGISTRATE_ISOLATION_CA"),
            identity_key=identity_key,
            network_hosts=hosts,
            github_permissions=permissions,
            max_global=_integer("MAGISTRATE_WORKER_MAX_GLOBAL", 20, 1, 1000),
            max_per_tenant=_integer("MAGISTRATE_WORKER_MAX_PER_TENANT", 2, 1, 100),
            cpu_millis=_integer("MAGISTRATE_WORKER_CPU_MILLIS", 2000, 100, 32000),
            memory_mib=_integer("MAGISTRATE_WORKER_MEMORY_MIB", 2048, 128, 131072),
            workspace_mib=_integer("MAGISTRATE_WORKER_WORKSPACE_MIB", 8192, 128, 1048576),
            deadline_seconds=_integer("MAGISTRATE_WORKER_DEADLINE_SECONDS", 3600, 60, 86400),
            cleanup_seconds=_integer("MAGISTRATE_WORKER_CLEANUP_SECONDS", 300, 0, 86400),
            poll_seconds=_integer("MAGISTRATE_WORKER_POLL_SECONDS", 5, 1, 300),
        )

    def opaque(self, purpose: str, *values: str, length: int = 32) -> str:
        return hmac.new(self.identity_key, "\0".join((purpose, *values)).encode(), hashlib.sha256).hexdigest()[:length]

    def identities(self, owner: str, project: str, objective_id: str) -> tuple[str, str, str]:
        tenant = self.opaque("tenant", owner)
        isolation = "iso_" + self.opaque("isolation", owner, project)
        execution = "hex_" + self.opaque("execution", owner, objective_id)
        return tenant, isolation, execution


class HostedObjectiveDispatcher:
    """Accept the already-durable submission for the hosted controller."""
    async def submit(self, *, task_id: str, title: str, project: str, body: str):
        from app.magi_firstmate_tools import ObjectiveDispatchError, ObjectiveDispatchReceipt
        try:
            configured = HostedExecutionConfig.from_env()
        except RuntimeError as exc:
            raise ObjectiveDispatchError("hosted-runtime-unavailable") from exc
        if configured is None:
            raise ObjectiveDispatchError("hosted-runtime-unavailable")
        return ObjectiveDispatchReceipt(already_present=False)


@dataclass(frozen=True)
class IsolationStatus:
    state: str
    usage: FirstmateMeasuredUsage | None = None


class IsolationTransport(Protocol):
    async def ensure(self, execution_id: str, spec: dict[str, Any]) -> str: ...
    async def status(self, execution_id: str) -> IsolationStatus: ...
    async def cancel(self, execution_id: str) -> None: ...
    async def delete(self, execution_id: str) -> None: ...
    async def github_credential(self, request: dict[str, Any]) -> dict[str, Any]: ...


class IsolationBackendClient:
    """mTLS client for the provider-neutral isolation and credential APIs."""
    def __init__(self, config: HostedExecutionConfig) -> None:
        self.config = config

    async def _request(self, base_url: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=base_url, verify=self.config.ca_path,
                cert=(self.config.client_cert_path, self.config.client_key_path),
                timeout=15.0, follow_redirects=False, trust_env=False,
            ) as client:
                async with client.stream(method, path, **kwargs) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 64 * 1024:
                            raise RuntimeError("Hosted backend response exceeded its byte bound")
                        body.extend(chunk)
                    return httpx.Response(
                        response.status_code, headers=response.headers, content=bytes(body),
                        request=response.request,
                    )
        except httpx.HTTPError as exc:
            raise RuntimeError("Hosted backend request failed") from exc

    @staticmethod
    def _payload(response: httpx.Response, message: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise RuntimeError(message) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(message)
        return payload

    async def ensure(self, execution_id: str, spec: dict[str, Any]) -> str:
        response = await self._request(
            self.config.backend_url, "PUT", f"/v1/executions/{execution_id}",
            headers={"Idempotency-Key": execution_id}, json=spec,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Isolation backend refused launch ({response.status_code})")
        payload = self._payload(response, "Isolation backend returned an invalid launch receipt")
        if set(payload) != {"schema_version", "execution_id", "status"}:
            raise RuntimeError("Isolation backend returned an invalid launch receipt")
        if payload != {"schema_version": "magistrate.isolation-receipt.v1", "execution_id": execution_id,
                       "status": payload["status"]} or payload["status"] not in {"accepted", "existing"}:
            raise RuntimeError("Isolation backend returned a conflicting launch receipt")
        return execution_id

    async def status(self, execution_id: str) -> IsolationStatus:
        response = await self._request(self.config.backend_url, "GET", f"/v1/executions/{execution_id}")
        if response.status_code == 404:
            return IsolationStatus("missing")
        if response.status_code != 200:
            raise RuntimeError(f"Isolation backend status failed ({response.status_code})")
        payload = self._payload(response, "Isolation backend returned an invalid status")
        state = payload.get("status")
        terminal = state in {"succeeded", "failed", "cancelled"}
        expected = {"schema_version", "execution_id", "status", "usage"} if terminal else {
            "schema_version", "execution_id", "status",
        }
        if (set(payload) != expected
                or payload.get("schema_version") != "magistrate.isolation-status.v1"
                or payload.get("execution_id") != execution_id
                or state not in {"queued", "running", "succeeded", "failed", "cancelled"}):
            raise RuntimeError("Isolation backend returned an invalid status")
        usage = None
        if terminal:
            try:
                usage = FirstmateMeasuredUsage.model_validate(payload["usage"])
            except ValueError as exc:
                raise RuntimeError("Isolation backend returned invalid measured usage") from exc
        return IsolationStatus(str(state), usage)

    async def cancel(self, execution_id: str) -> None:
        response = await self._request(
            self.config.backend_url, "POST", f"/v1/executions/{execution_id}/cancel",
            headers={"Idempotency-Key": f"cancel:{execution_id}"},
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"Isolation backend cancellation failed ({response.status_code})")
        payload = self._payload(response, "Isolation backend returned an invalid cancellation receipt")
        if payload != {"schema_version": "magistrate.isolation-cancellation.v1",
                       "execution_id": execution_id, "status": "requested"}:
            raise RuntimeError("Isolation backend returned an invalid cancellation receipt")

    async def delete(self, execution_id: str) -> None:
        response = await self._request(self.config.backend_url, "DELETE", f"/v1/executions/{execution_id}")
        if response.status_code not in {200, 202, 204, 404}:
            raise RuntimeError(f"Isolation backend cleanup failed ({response.status_code})")

    async def github_credential(self, request: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(self.config.github_broker_url, "POST", "/v1/github/credentials", json=request)
        if response.status_code != 201:
            raise RuntimeError("GitHub credential broker refused the scoped request")
        payload = self._payload(response, "GitHub credential broker returned an invalid scoped credential")
        if (set(payload) != {"schema_version", "token", "expires_at", "repository", "permissions"}
                or payload.get("schema_version") != "magistrate.github-credential.v1"
                or not isinstance(payload.get("token"), str) or not 16 <= len(payload["token"]) <= 2048
                or any(ord(char) < 33 or ord(char) > 126 for char in payload["token"])
                or type(payload.get("expires_at")) is not int
                or not isinstance(payload.get("repository"), str)
                or not _REPOSITORY.fullmatch(payload["repository"])
                or payload.get("permissions") != list(self.config.github_permissions)):
            raise RuntimeError("GitHub credential broker returned an invalid scoped credential")
        if payload["expires_at"] <= int(time.time()) or payload["expires_at"] > int(time.time()) + 3600:
            raise RuntimeError("GitHub credential lifetime exceeds the worker bound")
        return payload


class HostedExecutionStore:
    def _connect(self) -> sqlite3.Connection:
        db.init_db()
        connection = connect(db.DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def claim(self, config: HostedExecutionConfig) -> dict[str, Any] | None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            submissions = connection.execute(
                """SELECT submission.* FROM magi_objective_submissions submission
                   LEFT JOIN hosted_execution_runs run ON run.objective_id = submission.objective_id
                   WHERE submission.status = 'accepted' AND run.objective_id IS NULL
                   ORDER BY submission.accepted_at, submission.created_at LIMIT 200"""
            ).fetchall()
            for row in submissions:
                contract = json.loads(row["contract_json"])
                tenant, isolation, execution = config.identities(row["owner_user_id"], contract["project"], row["objective_id"])
                run_hash = hashlib.sha256(f"hosted-run-v1\0{row['owner_user_id']}\0{row['objective_id']}".encode()).hexdigest()
                worker_token = secrets.token_urlsafe(48)
                connection.execute(
                    """INSERT OR IGNORE INTO hosted_execution_runs
                       (owner_user_id, objective_id, task_id, run_id, project, tenant_key,
                        isolation_key, backend_execution_id, worker_token_hash, worker_token_enc,
                        state, attempt_count, lease_id, lease_expires_at, last_error_code,
                        cleaned_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, NULL, NULL, NULL, NULL, ?, ?)""",
                    (row["owner_user_id"], row["objective_id"], row["task_id"], f"run_{run_hash[:32]}",
                     contract["project"], tenant, isolation, execution,
                     hashlib.sha256(worker_token.encode()).hexdigest(), db.encrypt_token(worker_token), now, now),
                )
            connection.execute(
                """UPDATE hosted_execution_runs SET state='queued', lease_id=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE state='launching' AND lease_expires_at < ?""", (now, now),
            )
            active = connection.execute(
                "SELECT COUNT(*) FROM hosted_execution_runs WHERE state IN ('launching','running')"
            ).fetchone()[0]
            if active >= config.max_global:
                return None
            candidates = connection.execute(
                """SELECT run.*, submission.conversation_id, submission.user_message_id,
                          submission.contract_json, submission.display_title, submission.accepted_at
                   FROM hosted_execution_runs run JOIN magi_objective_submissions submission
                     ON submission.objective_id=run.objective_id
                   WHERE run.state='queued' AND NOT EXISTS (
                       SELECT 1 FROM objective_cancellation_requests cancellation
                       WHERE cancellation.owner_user_id=run.owner_user_id
                         AND cancellation.objective_id=run.objective_id
                         AND cancellation.status='requested'
                   ) ORDER BY run.created_at LIMIT 200"""
            ).fetchall()
            selected = None
            for row in candidates:
                tenant_active = connection.execute(
                    "SELECT COUNT(*) FROM hosted_execution_runs WHERE tenant_key=? AND state IN ('launching','running')",
                    (row["tenant_key"],),
                ).fetchone()[0]
                if tenant_active < config.max_per_tenant:
                    selected = row
                    break
            if selected is None:
                return None
            lease = secrets.token_hex(16)
            changed = connection.execute(
                """UPDATE hosted_execution_runs SET state='launching', attempt_count=attempt_count+1,
                   lease_id=?, lease_expires_at=?, last_error_code=NULL, updated_at=?
                   WHERE objective_id=? AND state='queued'""",
                (lease, now + config.lease_ms, now, selected["objective_id"]),
            ).rowcount
            if changed != 1:
                return None
            result = dict(selected)
            result["lease_id"] = lease
            result["attempt_count"] += 1
            return result

    def launched(self, objective_id: str, lease_id: str) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE hosted_execution_runs SET state='running', lease_id=NULL,
                   lease_expires_at=NULL, updated_at=?
                   WHERE objective_id=? AND state='launching' AND lease_id=?""",
                (_now_ms(), objective_id, lease_id),
            ).rowcount
        if changed != 1:
            raise RuntimeError("Hosted execution launch lease was lost")

    def launch_failed(self, objective_id: str, lease_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE hosted_execution_runs SET state='queued', lease_id=NULL,
                   lease_expires_at=NULL, last_error_code='launch_failed', updated_at=?
                   WHERE objective_id=? AND state='launching' AND lease_id=?""",
                (_now_ms(), objective_id, lease_id),
            )

    def queued_cancellations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run.*, submission.conversation_id, submission.user_message_id,
                          submission.contract_json, submission.display_title, submission.accepted_at
                   FROM hosted_execution_runs run JOIN magi_objective_submissions submission
                     ON submission.objective_id=run.objective_id
                   WHERE run.state='queued' AND EXISTS (
                       SELECT 1 FROM objective_cancellation_requests cancellation
                       WHERE cancellation.owner_user_id=run.owner_user_id
                         AND cancellation.objective_id=run.objective_id
                         AND cancellation.status='requested'
                   ) ORDER BY run.created_at LIMIT 100"""
            ).fetchall()
        return [dict(row) for row in rows]

    def active(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run.*, objective.terminal_phase,
                          EXISTS (
                              SELECT 1 FROM objective_cancellation_requests cancellation
                              WHERE cancellation.owner_user_id=run.owner_user_id
                                AND cancellation.objective_id=run.objective_id
                                AND cancellation.status='requested'
                          ) AS cancellation_requested
                   FROM hosted_execution_runs run
                   LEFT JOIN firstmate_execution_objectives objective
                     ON objective.owner_user_id=run.owner_user_id AND objective.objective_id=run.objective_id
                   WHERE run.state='running' ORDER BY run.created_at LIMIT 1000"""
            ).fetchall()
        return [dict(row) for row in rows]

    def terminal_timestamp(self, objective_id: str) -> int:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """UPDATE hosted_execution_runs SET terminal_observed_at=COALESCE(terminal_observed_at,?)
                   WHERE objective_id=?""", (now, objective_id),
            )
            row = connection.execute(
                "SELECT terminal_observed_at FROM hosted_execution_runs WHERE objective_id=?", (objective_id,),
            ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("Hosted execution disappeared while recording terminal time")
        return int(row[0])

    def mark_terminal(self, owner: str, objective_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE hosted_execution_runs SET state='terminal', lease_id=NULL,
                   lease_expires_at=NULL, worker_token_hash=?, worker_token_enc='', updated_at=?
                   WHERE owner_user_id=? AND objective_id=?""",
                (f"retired:{objective_id}", _now_ms(), owner, objective_id),
            )

    def pending_cleanup(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM hosted_execution_runs WHERE state='terminal' AND cleaned_at IS NULL LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def retire_owner(self, owner: str) -> list[dict[str, Any]]:
        """Fence every owner workload before account data can be erased."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM hosted_execution_runs
                   WHERE owner_user_id=? AND cleaned_at IS NULL
                   ORDER BY created_at, objective_id""",
                (owner,),
            ).fetchall()
            now = _now_ms()
            for row in rows:
                connection.execute(
                    """UPDATE hosted_execution_runs SET state='terminal', lease_id=NULL,
                       lease_expires_at=NULL, worker_token_hash=?, worker_token_enc='', updated_at=?
                       WHERE owner_user_id=? AND objective_id=?""",
                    (f"retired:{row['objective_id']}", now, owner, row["objective_id"]),
                )
        return [dict(row) for row in rows]

    def cleaned(self, objective_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE hosted_execution_runs SET cleaned_at=?, updated_at=? WHERE objective_id=?",
                (_now_ms(), _now_ms(), objective_id),
            )

    def workload(self, objective_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT run.*, submission.contract_json, submission.contract_sha256,
                          submission.conversation_id, submission.user_message_id,
                          submission.project_id
                   FROM hosted_execution_runs run JOIN magi_objective_submissions submission
                     ON submission.objective_id=run.objective_id WHERE run.objective_id=?""",
                (objective_id,),
            ).fetchone()
        return dict(row) if row else None

    def github_target(self, run: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve exactly one current owner-authorized repository for a run."""
        project_id = run.get("project_id")
        if not project_id:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT app_repo.installation_id, app_repo.repository_id,
                          app_repo.full_name, project_repo.repository_id
                   FROM project_repositories AS project_repo
                   JOIN github_app_repositories AS app_repo
                     ON lower(app_repo.full_name) = lower(project_repo.full_name)
                    AND app_repo.active = 1
                   JOIN github_app_installations AS installation
                     ON installation.installation_id = app_repo.installation_id
                    AND installation.user_id = project_repo.owner_user_id
                    AND installation.status = 'active'
                   WHERE project_repo.owner_user_id = ?
                     AND project_repo.project_id = ?
                     AND project_repo.provider = 'github'
                   ORDER BY app_repo.installation_id, app_repo.repository_id
                   LIMIT 2""",
                (run["owner_user_id"], project_id),
            ).fetchall()
        if len(rows) != 1:
            return None
        return {
            "installation_id": int(rows[0][0]),
            "provider_repository_id": int(rows[0][1]),
            "repository": str(rows[0][2]),
            "project_repository_id": str(rows[0][3]),
        }


class HostedExecutionController:
    def __init__(self, config: HostedExecutionConfig, transport: IsolationTransport,
                 *, store: HostedExecutionStore | None = None) -> None:
        self.config, self.transport = config, transport
        self.store = store or HostedExecutionStore()

    def launch_spec(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "magistrate.isolated-execution.v1",
            "execution_id": run["backend_execution_id"],
            "tenant_key": run["tenant_key"],
            "isolation_key": run["isolation_key"],
            "image": self.config.worker_image,
            "command": ["/opt/magistrate/bin/firstmate-worker"],
            "environment": {
                "MAGISTRATE_OBJECTIVE_ID": run["objective_id"],
                "MAGISTRATE_GATEWAY_URL": self.config.gateway_url,
                "MAGISTRATE_WORKER_TOKEN": db.decrypt_token(run["worker_token_enc"]),
            },
            "isolation": {
                "filesystem": "ephemeral", "process": "dedicated", "run_as_root": False,
                "read_only_root": True, "no_new_privileges": True, "capabilities": [],
                "network": {"default": "deny", "allow_https_hosts": list(self.config.network_hosts), "ingress": "deny"},
            },
            "limits": {"cpu_millis": self.config.cpu_millis, "memory_mib": self.config.memory_mib,
                       "workspace_mib": self.config.workspace_mib, "wall_seconds": self.config.deadline_seconds,
                       "processes": 256},
            "cleanup_after_seconds": self.config.cleanup_seconds,
        }

    async def _accepted_event(self, run: dict[str, Any]) -> None:
        from app.firstmate_execution_api import firstmate_execution_service
        contract = json.loads(run["contract_json"])
        try:
            facts = FirstmateObjectiveFacts(title=run.get("display_title") or "Hosted objective", project=contract["project"])
        except ValueError:
            facts = FirstmateObjectiveFacts(title="Hosted objective", project=contract["project"])
        digest = hashlib.sha256(f"accepted\0{run['owner_user_id']}\0{run['objective_id']}".encode()).hexdigest()
        event = FirstmateObjectiveAcceptedEvent(
            schema_version="firstmate.execution-event.v1", event_id=f"hev_{digest[:32]}",
            objective_id=run["objective_id"], task_id=run["task_id"], run_id=run["run_id"],
            occurred_at_ms=int(run["accepted_at"] or run["created_at"]), phase="objective.accepted",
            objective=facts, chat=FirstmateNativeChatOrigin(
                conversation_id=run["conversation_id"], user_message_id=run["user_message_id"]),
        )
        await firstmate_execution_service.ingest(run["owner_user_id"], event)

    async def _worker_started_event(self, run: dict[str, Any]) -> None:
        from app.firstmate_execution_api import firstmate_execution_service
        digest = hashlib.sha256(
            f"worker-started\0{run['owner_user_id']}\0{run['objective_id']}".encode()
        ).hexdigest()
        event = FirstmateProgressEvent(
            schema_version="firstmate.execution-event.v1", event_id=f"hev_{digest[:32]}",
            objective_id=run["objective_id"], task_id=run["task_id"], run_id=run["run_id"],
            occurred_at_ms=int(run["updated_at"]), phase="worker.started",
        )
        await firstmate_execution_service.ingest(run["owner_user_id"], event)

    async def process_once(self) -> bool:
        run = await asyncio.to_thread(self.store.claim, self.config)
        if run is None:
            return False
        try:
            await self._accepted_event(run)
            receipt = await self.transport.ensure(run["backend_execution_id"], self.launch_spec(run))
            if receipt != run["backend_execution_id"]:
                raise RuntimeError("Isolation backend changed execution identity")
            await asyncio.to_thread(self.store.launched, run["objective_id"], run["lease_id"])
        except Exception:
            await asyncio.to_thread(self.store.launch_failed, run["objective_id"], run["lease_id"])
            raise
        return True

    async def reconcile_once(self) -> None:
        from app.firstmate_execution import FirstmateObjectiveTerminalEvent
        from app.firstmate_execution_api import firstmate_execution_service
        # A cancellation can win before capacity is allocated. Persist normal
        # accepted/cancelled facts without ever creating external execution.
        for run in await asyncio.to_thread(self.store.queued_cancellations):
            await self._accepted_event(run)
            digest = hashlib.sha256(
                f"prelaunch-cancel\0{run['owner_user_id']}\0{run['objective_id']}".encode()
            ).hexdigest()
            terminal_at = await asyncio.to_thread(self.store.terminal_timestamp, run["objective_id"])
            event = FirstmateObjectiveTerminalEvent(
                schema_version="firstmate.execution-event.v1", event_id=f"hev_{digest[:32]}",
                objective_id=run["objective_id"], task_id=run["task_id"], run_id=run["run_id"],
                occurred_at_ms=terminal_at, phase="objective.cancelled",
                usage=_NO_WORKER_USAGE,
            )
            await firstmate_execution_service.ingest(run["owner_user_id"], event)
            await asyncio.to_thread(self.store.mark_terminal, run["owner_user_id"], run["objective_id"])
        for run in await asyncio.to_thread(self.store.active):
            if run.get("terminal_phase"):
                await asyncio.to_thread(self.store.mark_terminal, run["owner_user_id"], run["objective_id"])
                continue
            if run.get("cancellation_requested"):
                # The cancellation row is durable authority. Reassert the
                # idempotent backend tombstone until its status is observed.
                await self.transport.cancel(run["backend_execution_id"])
            observed = await self.transport.status(run["backend_execution_id"])
            status = observed.state
            if status == "missing":
                await self.transport.ensure(run["backend_execution_id"], self.launch_spec(run))
            elif status in {"running", "succeeded", "failed", "cancelled"}:
                await self._worker_started_event(run)
            if status in {"succeeded", "failed", "cancelled"}:
                if observed.usage is None:
                    raise RuntimeError("Isolation backend omitted terminal measured usage")
                terminal_phase = "objective.cancelled" if status == "cancelled" else "objective.failed"
                digest = hashlib.sha256(
                    f"worker-exit\0{terminal_phase}\0{run['owner_user_id']}\0{run['objective_id']}".encode()
                ).hexdigest()
                terminal_at = await asyncio.to_thread(self.store.terminal_timestamp, run["objective_id"])
                event = FirstmateObjectiveTerminalEvent(
                    schema_version="firstmate.execution-event.v1", event_id=f"hev_{digest[:32]}",
                    objective_id=run["objective_id"], task_id=run["task_id"], run_id=run["run_id"],
                    occurred_at_ms=terminal_at, phase=terminal_phase,
                    usage=observed.usage,
                )
                await firstmate_execution_service.ingest(run["owner_user_id"], event)
                await asyncio.to_thread(self.store.mark_terminal, run["owner_user_id"], run["objective_id"])
        for run in await asyncio.to_thread(self.store.pending_cleanup):
            await self.transport.delete(run["backend_execution_id"])
            await asyncio.to_thread(self.store.cleaned, run["objective_id"])

    async def retire_owner(self, owner: str) -> None:
        """Fence, cancel, and remove every workload before account erasure."""
        runs = await asyncio.to_thread(self.store.retire_owner, owner)
        for run in runs:
            if run["state"] in {"launching", "running"}:
                await self.transport.cancel(run["backend_execution_id"])
            await self.transport.delete(run["backend_execution_id"])
            await asyncio.to_thread(self.store.cleaned, run["objective_id"])

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
                while await self.process_once():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print("Hosted execution controller unavailable:", type(exc).__name__)
            await asyncio.sleep(self.config.poll_seconds)

    async def authenticate(self, objective_id: str, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="A workload bearer is required.")
        token = authorization[7:]
        if not token or len(token) > 256:
            raise HTTPException(status_code=401, detail="The workload bearer is invalid.")
        run = await asyncio.to_thread(self.store.workload, objective_id)
        if run is None or run["state"] != "running":
            raise HTTPException(status_code=404, detail="Hosted objective not found.")
        supplied = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(supplied, run["worker_token_hash"]):
            raise HTTPException(status_code=403, detail="The workload identity does not own this objective.")
        return run


class HostedDecisionCommandAdapter:
    """Durable out-of-band answer delivery to the owning worker."""
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def open_identity(self, task_id: str) -> str | None:
        db.init_db()
        with connect(db.DB_PATH, timeout=10) as connection:
            row = connection.execute(
                """SELECT lifecycle_identity FROM firstmate_decisions WHERE task_id=?
                   AND state IN ('pending','answering') ORDER BY updated_at DESC LIMIT 1""", (task_id,),
            ).fetchone()
        return str(row[0]) if row else None

    async def answer_decision(self, task_id: str, lifecycle_identity: str, answer: str, *, allow_closed_replay: bool = False):
        from app.firstmate_decisions import FirstmateCommandResult, FirstmateDecisionError, _validated_answer
        encoded = _validated_answer(answer)
        current = await self.open_identity(task_id)
        if current is not None and current != lifecycle_identity:
            return FirstmateCommandResult(False, "stale")
        if current is None and not allow_closed_replay:
            return FirstmateCommandResult(False, "stale")
        digest = hashlib.sha256(encoded).hexdigest()
        delivery = "hda_" + hashlib.sha256(f"hosted-answer-v1\0{task_id}\0{lifecycle_identity}\0{digest}".encode()).hexdigest()[:32]
        now = _now_ms()
        with connect(db.DB_PATH, timeout=10) as connection:
            run = connection.execute(
                "SELECT owner_user_id,objective_id FROM hosted_execution_runs WHERE task_id=? AND state='running'", (task_id,),
            ).fetchone()
            if run is None:
                raise FirstmateDecisionError("command_unavailable", "The hosted Firstmate worker is unavailable.", 503)
            connection.execute(
                """INSERT OR IGNORE INTO hosted_decision_deliveries
                   (delivery_id,owner_user_id,objective_id,task_id,lifecycle_identity,answer_enc,
                    answer_sha256,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
                (delivery, run[0], run[1], task_id, lifecycle_identity, db.encrypt_token(answer), digest, now, now),
            )
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            with connect(db.DB_PATH, timeout=10) as connection:
                row = connection.execute("SELECT status FROM hosted_decision_deliveries WHERE delivery_id=?", (delivery,)).fetchone()
            if row and row[0] == "accepted":
                return FirstmateCommandResult(True, "answered")
            if row and row[0] == "rejected":
                return FirstmateCommandResult(False, "rejected")
            await asyncio.sleep(0.25)
        raise FirstmateDecisionError("command_unavailable", "The hosted Firstmate worker did not acknowledge the answer.", 503)


class HostedEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: FirstmateExecutionEventContract


class HostedDecisionAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    delivery_id: str = Field(pattern=r"^hda_[0-9a-f]{32}$")
    status: str = Field(pattern=r"^(accepted|rejected)$")


router = APIRouter(prefix="/api/v1/hosted-execution", tags=["Hosted execution"])
_runtime: HostedExecutionController | None = None
_OBJECTIVE_PATH = Path(pattern=r"^mgo_[0-9a-f]{32}$")


def get_hosted_controller() -> HostedExecutionController | None:
    global _runtime
    config = HostedExecutionConfig.from_env()
    if config is None:
        return None
    if _runtime is None or _runtime.config != config:
        _runtime = HostedExecutionController(config, IsolationBackendClient(config))
    return _runtime


async def _workload(objective_id: str, authorization: str | None) -> tuple[HostedExecutionController, dict[str, Any]]:
    controller = get_hosted_controller()
    if controller is None:
        raise HTTPException(status_code=404, detail="Hosted execution is not enabled.")
    return controller, await controller.authenticate(objective_id, authorization)


@router.get("/objectives/{objective_id}")
async def get_hosted_objective(objective_id: str = _OBJECTIVE_PATH, authorization: str | None = Header(default=None)):
    _controller, run = await _workload(objective_id, authorization)
    return {"schema_version": "magistrate.hosted-objective.v1", "objective_id": run["objective_id"],
            "task_id": run["task_id"], "run_id": run["run_id"], "project": run["project"],
            "contract": json.loads(run["contract_json"]), "contract_sha256": run["contract_sha256"]}


@router.post("/objectives/{objective_id}/github-credential")
async def get_hosted_github_credential(objective_id: str = _OBJECTIVE_PATH,
                                       authorization: str | None = Header(default=None)):
    controller, run = await _workload(objective_id, authorization)
    target = await asyncio.to_thread(controller.store.github_target, run)
    if target is None:
        raise HTTPException(
            status_code=409,
            detail="This project does not have exactly one currently authorized GitHub repository.",
        )
    request = {
        "schema_version": "magistrate.github-credential-request.v1",
        "execution_id": run["backend_execution_id"],
        "tenant_key": run["tenant_key"],
        "installation_id": target["installation_id"],
        "repository_id": target["provider_repository_id"],
        "repository": target["repository"],
        "permissions": list(controller.config.github_permissions),
        "expires_within_seconds": min(3600, controller.config.deadline_seconds),
    }
    try:
        credential = await controller.transport.github_credential(request)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail="A scoped GitHub credential is unavailable.") from exc
    if credential.get("repository") != target["repository"]:
        raise HTTPException(status_code=503, detail="The credential broker returned a conflicting repository.")
    return credential


@router.get("/objectives/{objective_id}/decision-answers")
async def get_hosted_decision_answers(objective_id: str = _OBJECTIVE_PATH,
                                      authorization: str | None = Header(default=None)):
    _controller, run = await _workload(objective_id, authorization)
    with connect(db.DB_PATH, timeout=10) as connection:
        rows = connection.execute(
            """SELECT delivery_id,lifecycle_identity,answer_enc,answer_sha256
               FROM hosted_decision_deliveries WHERE owner_user_id=? AND objective_id=?
               AND task_id=? AND status='pending' ORDER BY created_at LIMIT 10""",
            (run["owner_user_id"], objective_id, run["task_id"]),
        ).fetchall()
    return {"schema_version": "magistrate.hosted-decision-answers.v1", "answers": [
        {"delivery_id": row[0], "lifecycle_identity": row[1],
         "answer": db.decrypt_token(row[2]), "answer_sha256": row[3]} for row in rows]}


@router.post("/objectives/{objective_id}/decision-answers/ack")
async def ack_hosted_decision_answer(ack: HostedDecisionAck, objective_id: str = _OBJECTIVE_PATH,
                                     authorization: str | None = Header(default=None)):
    _controller, run = await _workload(objective_id, authorization)
    with connect(db.DB_PATH, timeout=10) as connection:
        changed = connection.execute(
            """UPDATE hosted_decision_deliveries SET status=?,updated_at=? WHERE delivery_id=?
               AND owner_user_id=? AND objective_id=? AND status='pending'""",
            (ack.status, _now_ms(), ack.delivery_id, run["owner_user_id"], objective_id),
        ).rowcount
        if changed == 0:
            existing = connection.execute(
                "SELECT status FROM hosted_decision_deliveries WHERE delivery_id=? AND owner_user_id=? AND objective_id=?",
                (ack.delivery_id, run["owner_user_id"], objective_id),
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Decision delivery not found.")
            if existing[0] != ack.status:
                raise HTTPException(status_code=409, detail="Decision delivery is already terminal.")
    return {"schema_version": "magistrate.hosted-decision-ack.v1", "status": ack.status, "delivery_id": ack.delivery_id}


@router.post("/objectives/{objective_id}/decision-events")
async def post_hosted_decision_events(objective_id: str = _OBJECTIVE_PATH,
                                      authorization: str | None = Header(default=None),
                                      batch: dict[str, Any] = Body(...)):
    _controller, run = await _workload(objective_id, authorization)
    from app.firstmate_client import FirstmateClient
    from app.firstmate_decisions import (
        FirstmateDecisionError, FirstmateDecisionEventBatch, FirstmateDecisionService,
    )
    try:
        contract = FirstmateDecisionEventBatch.model_validate(batch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The decision event batch is invalid.") from exc
    if any(event.task_id != run["task_id"] for event in contract.events):
        raise HTTPException(status_code=409, detail="A decision event does not belong to this hosted execution.")
    expected_source = f"firstmate:hosted:{run['backend_execution_id'][4:]}"
    if contract.source_instance_id != expected_source:
        raise HTTPException(status_code=409, detail="The decision source does not belong to this hosted execution.")
    service = FirstmateDecisionService(
        FirstmateClient(), command=HostedDecisionCommandAdapter(), source_instance_id=expected_source)
    try:
        decisions = await service.ingest_events(run["owner_user_id"], contract)
    except FirstmateDecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"schema_version": "firstmate.decision-events-result.v1", "status": "accepted",
            "pending_count": len(decisions), "observed_at": contract.observed_at}


@router.post("/objectives/{objective_id}/events")
async def post_hosted_event(envelope: HostedEventEnvelope, objective_id: str = _OBJECTIVE_PATH,
                            authorization: str | None = Header(default=None)):
    controller, run = await _workload(objective_id, authorization)
    event = envelope.event
    if event.phase == "objective.accepted" or (event.objective_id, event.task_id, event.run_id) != (
            run["objective_id"], run["task_id"], run["run_id"]):
        raise HTTPException(status_code=409, detail="The event does not belong to this hosted execution.")
    from app.firstmate_execution_api import firstmate_execution_service
    try:
        result = await firstmate_execution_service.ingest(run["owner_user_id"], event)
    except FirstmateExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstmateExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if event.phase in {"objective.completed", "objective.failed", "objective.cancelled"}:
        await asyncio.to_thread(controller.store.mark_terminal, run["owner_user_id"], objective_id)
    return result
