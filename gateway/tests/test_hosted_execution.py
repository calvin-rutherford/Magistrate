import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import db
from app.auth import Principal
from app.firstmate_execution import FirstmateProgressEvent
from app.firstmate_execution_api import post_firstmate_execution_event
from app.hosted_execution import HostedExecutionConfig, HostedExecutionController, HostedExecutionStore
from app.magi_chat_api import magi_chat_store
from app.magi_firstmate_tools import FirstmateSubmitObjectiveContract, ObjectiveSubmissionStore
from app.magi_tool_protocol import MagiToolContext

IMAGE = "registry.example/magistrate-worker@sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "hosted-execution.sqlite3"))
    db.init_db()


def config(**changes):
    values = dict(
        worker_image=IMAGE,
        gateway_url="https://gateway.internal",
        backend_url="https://isolation.internal",
        github_broker_url="https://github-broker.internal",
        client_cert_path="/unused/cert",
        client_key_path="/unused/key",
        ca_path="/unused/ca",
        identity_key=b"x" * 32,
        network_hosts=("gateway.internal", "github.com", "api.github.com"),
        github_permissions=("contents:write", "pull_requests:write"),
        max_global=4,
        max_per_tenant=1,
        cpu_millis=2000,
        memory_mib=2048,
        workspace_mib=8192,
        deadline_seconds=3600,
        cleanup_seconds=60,
        poll_seconds=1,
        lease_ms=100,
    )
    values.update(changes)
    return HostedExecutionConfig(**values)


class FakeIsolationBackend:
    def __init__(self):
        self.executions = {}
        self.ensure_calls = []
        self.deleted = []

    async def ensure(self, execution_id, spec):
        self.ensure_calls.append(execution_id)
        previous = self.executions.get(execution_id)
        if previous is not None:
            assert previous["spec"] == spec
        self.executions[execution_id] = {"spec": json.loads(json.dumps(spec)), "status": "running"}
        return execution_id

    async def status(self, execution_id):
        row = self.executions.get(execution_id)
        return row["status"] if row else "missing"

    async def cancel(self, execution_id):
        if execution_id in self.executions:
            self.executions[execution_id]["status"] = "cancelled"

    async def delete(self, execution_id):
        self.deleted.append(execution_id)
        self.executions.pop(execution_id, None)

    async def github_credential(self, request):
        return {"schema_version": "magistrate.github-credential.v1", "token": "short-lived",
                "expires_at": 1, "repository": "example/repo", "permissions": request["permissions"]}


def accepted_submission(owner: str, suffix: str):
    origin = magi_chat_store.prepare_submission(owner, f"hosted-client-{suffix}", "Implement the isolated objective.")
    magi_chat_store.complete_submission(owner, origin.assistant_message_id, origin.attempt, "Accepted.", latency_ms=1)
    contract = FirstmateSubmitObjectiveContract(
        objective=f"Implement hosted objective {suffix}", project="Magistrate",
        constraints=["Keep tenant data isolated"], acceptance_criteria=["Tests pass"], context_refs=[],
    )
    context = MagiToolContext(
        owner_user_id=owner, conversation_id=origin.conversation_id, turn_id=origin.turn_id,
        user_message_id=origin.user_message_id, assistant_message_id=origin.assistant_message_id,
        command_authorized=True,
    )
    store = ObjectiveSubmissionStore()
    claim = store.claim(context=context, invocation_key=(suffix.encode().hex() * 64)[:64], contract=contract)
    store.accept(owner, claim)
    return claim


def test_hosted_configuration_fails_closed_and_accepts_only_pinned_mtls_boundary(monkeypatch, tmp_path):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path in (cert, key, ca):
        path.write_text("test", encoding="utf-8")
        path.chmod(0o600)
    settings = {
        "MAGISTRATE_HOSTED_EXECUTION_ENABLED": "true",
        "MAGISTRATE_WORKER_IMAGE": IMAGE,
        "MAGISTRATE_WORKER_GATEWAY_URL": "https://gateway.internal",
        "MAGISTRATE_ISOLATION_BACKEND_URL": "https://isolation.internal",
        "MAGISTRATE_GITHUB_TOKEN_BROKER_URL": "https://broker.internal",
        "MAGISTRATE_ISOLATION_CLIENT_CERT": str(cert),
        "MAGISTRATE_ISOLATION_CLIENT_KEY": str(key),
        "MAGISTRATE_ISOLATION_CA": str(ca),
        "MAGISTRATE_WORKER_IDENTITY_KEY": "x" * 32,
        "MAGISTRATE_WORKER_NETWORK_HOSTS": "gateway.internal,github.com",
        "MAGISTRATE_GITHUB_PERMISSIONS": "contents:write,pull_requests:write",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    loaded = HostedExecutionConfig.from_env()
    assert loaded and loaded.worker_image == IMAGE
    monkeypatch.setenv("MAGISTRATE_WORKER_IMAGE", "registry.example/worker:latest")
    with pytest.raises(RuntimeError, match="sha256"):
        HostedExecutionConfig.from_env()
    monkeypatch.setenv("MAGISTRATE_WORKER_IMAGE", IMAGE)
    key.chmod(0o640)
    with pytest.raises(RuntimeError, match="unsafe"):
        HostedExecutionConfig.from_env()


@pytest.mark.asyncio
async def test_hosted_mode_rejects_user_scoped_execution_producer_route(monkeypatch):
    monkeypatch.setenv("MAGISTRATE_HOSTED_EXECUTION_ENABLED", "true")
    event = FirstmateProgressEvent(
        schema_version="firstmate.execution-event.v1", event_id="event-user-forgery",
        objective_id="mgo_" + "1" * 32, task_id="task-user-forgery",
        run_id="run-user-forgery", occurred_at_ms=1, phase="worker.started",
    )
    principal = Principal("tenant-user", frozenset({"command"}), "session", 9_999_999_999)
    with pytest.raises(HTTPException) as denied:
        await post_firstmate_execution_event(event, principal)
    assert denied.value.status_code == 403


def test_launch_contract_is_provider_neutral_opaque_and_strictly_bounded():
    cfg = config()
    tenant_a, isolation_a, execution_a = cfg.identities("tenant-a", "Magistrate", "mgo_" + "1" * 32)
    tenant_b, isolation_b, _ = cfg.identities("tenant-b", "Magistrate", "mgo_" + "1" * 32)
    assert tenant_a != tenant_b and isolation_a != isolation_b
    run = {
        "backend_execution_id": execution_a, "tenant_key": tenant_a, "isolation_key": isolation_a,
        "objective_id": "mgo_" + "1" * 32, "worker_token_enc": db.encrypt_token("worker-secret"),
    }
    spec = HostedExecutionController(cfg, FakeIsolationBackend()).launch_spec(run)
    rendered = json.dumps(spec, sort_keys=True)
    assert "tenant-a" not in rendered and "hosted objective" not in rendered.lower()
    assert spec["image"] == IMAGE
    assert spec["command"] == ["/opt/magistrate/bin/firstmate-worker"]
    assert spec["isolation"] == {
        "filesystem": "ephemeral", "process": "dedicated", "run_as_root": False,
        "read_only_root": True, "no_new_privileges": True, "capabilities": [],
        "network": {"default": "deny", "allow_https_hosts": list(cfg.network_hosts), "ingress": "deny"},
    }
    assert spec["limits"] == {"cpu_millis": 2000, "memory_mib": 2048, "workspace_mib": 8192,
                              "wall_seconds": 3600, "processes": 256}
    assert "objective" not in rendered.lower().replace("objective_id", "")


@pytest.mark.asyncio
async def test_accepted_objective_automatically_launches_exactly_one_execution():
    claim = accepted_submission("hosted-owner-a", "launch-a")
    backend = FakeIsolationBackend()
    controller = HostedExecutionController(config(), backend)
    assert await controller.process_once() is True
    assert await controller.process_once() is False
    await controller.reconcile_once()
    await controller.reconcile_once()  # stable event identity/payload on polling retries
    assert len(backend.executions) == 1
    with sqlite3.connect(db.DB_PATH) as connection:
        run = connection.execute(
            "SELECT state,attempt_count,backend_execution_id FROM hosted_execution_runs WHERE objective_id=?",
            (claim.objective_id,),
        ).fetchone()
        phases = [row[0] for row in connection.execute(
            "SELECT phase FROM firstmate_execution_events WHERE objective_id=? ORDER BY created_at",
            (claim.objective_id,),
        )]
    assert run[0:2] == ("running", 1)
    assert run[2] in backend.executions
    assert phases == ["objective.accepted", "worker.started"]


@pytest.mark.asyncio
async def test_expired_launch_lease_recovers_without_duplicate_execution_or_event():
    claim = accepted_submission("hosted-owner-recovery", "recovery")
    cfg = config()
    backend = FakeIsolationBackend()
    controller = HostedExecutionController(cfg, backend)
    run = HostedExecutionStore().claim(cfg)
    assert run and run["objective_id"] == claim.objective_id
    await controller._accepted_event(run)
    await backend.ensure(run["backend_execution_id"], controller.launch_spec(run))
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute("UPDATE hosted_execution_runs SET lease_expires_at=0 WHERE objective_id=?", (claim.objective_id,))
    assert await controller.process_once() is True
    assert len(backend.executions) == 1
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute(
            "SELECT attempt_count FROM hosted_execution_runs WHERE objective_id=?", (claim.objective_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM firstmate_execution_events WHERE objective_id=? AND phase='objective.accepted'",
            (claim.objective_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_per_tenant_concurrency_queues_second_objective_but_other_tenant_can_run():
    first = accepted_submission("hosted-owner-limit", "limit-one")
    second = accepted_submission("hosted-owner-limit", "limit-two")
    third = accepted_submission("hosted-owner-other", "limit-three")
    controller = HostedExecutionController(config(max_global=2, max_per_tenant=1), FakeIsolationBackend())
    assert await controller.process_once() is True
    assert await controller.process_once() is True
    assert await controller.process_once() is False
    with sqlite3.connect(db.DB_PATH) as connection:
        states = dict(connection.execute(
            "SELECT objective_id,state FROM hosted_execution_runs WHERE objective_id IN (?,?,?)",
            (first.objective_id, second.objective_id, third.objective_id),
        ).fetchall())
    assert states[first.objective_id] == "running"
    assert states[second.objective_id] == "queued"
    assert states[third.objective_id] == "running"


@pytest.mark.asyncio
async def test_failed_worker_is_terminally_projected_and_ephemeral_capacity_is_cleaned():
    claim = accepted_submission("hosted-owner-failure", "worker-failure")
    backend = FakeIsolationBackend()
    controller = HostedExecutionController(config(), backend)
    await controller.process_once()
    run = HostedExecutionStore().workload(claim.objective_id)
    backend.executions[run["backend_execution_id"]]["status"] = "failed"
    await controller.reconcile_once()
    await controller.reconcile_once()  # terminal cleanup is safe to replay
    with sqlite3.connect(db.DB_PATH) as connection:
        stored = connection.execute(
            "SELECT state,worker_token_enc,cleaned_at FROM hosted_execution_runs WHERE objective_id=?",
            (claim.objective_id,),
        ).fetchone()
        phases = [row[0] for row in connection.execute(
            "SELECT phase FROM firstmate_execution_events WHERE objective_id=? ORDER BY created_at",
            (claim.objective_id,),
        )]
    assert stored[0] == "terminal" and stored[1] == "" and stored[2] is not None
    assert phases == ["objective.accepted", "worker.started", "objective.failed"]
    assert backend.deleted == [run["backend_execution_id"]]


@pytest.mark.asyncio
async def test_prelaunch_cancellation_never_allocates_a_worker_and_becomes_observed():
    claim = accepted_submission("hosted-owner-cancel", "cancel-before-launch")
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO objective_cancellation_requests
               (request_id,owner_user_id,objective_id,task_id,actor_session_id,idempotency_key,
                status,notification_status,notification_attempt_count,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'requested','delivered',1,1,1)""",
            ("ocr_" + "1" * 32, "hosted-owner-cancel", claim.objective_id, claim.task_id,
             "session", "cancel-key-0001"),
        )
    backend = FakeIsolationBackend()
    controller = HostedExecutionController(config(), backend)
    assert await controller.process_once() is False  # syncs the queue but cancellation excludes launch
    await controller.reconcile_once()
    assert backend.executions == {}
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute(
            "SELECT state FROM hosted_execution_runs WHERE objective_id=?", (claim.objective_id,),
        ).fetchone()[0] == "terminal"
        assert connection.execute(
            "SELECT status FROM objective_cancellation_requests WHERE objective_id=?", (claim.objective_id,),
        ).fetchone()[0] == "observed"
        phases = [row[0] for row in connection.execute(
            "SELECT phase FROM firstmate_execution_events WHERE objective_id=? ORDER BY created_at", (claim.objective_id,),
        )]
    assert phases == ["objective.accepted", "objective.cancelled"]


@pytest.mark.asyncio
async def test_worker_bearer_cannot_cross_tenant_boundary():
    first = accepted_submission("hosted-owner-auth-a", "auth-one")
    second = accepted_submission("hosted-owner-auth-b", "auth-two")
    controller = HostedExecutionController(config(max_per_tenant=2), FakeIsolationBackend())
    await controller.process_once()
    await controller.process_once()
    run_a = HostedExecutionStore().workload(first.objective_id)
    run_b = HostedExecutionStore().workload(second.objective_id)
    token_a = db.decrypt_token(run_a["worker_token_enc"])
    assert (await controller.authenticate(first.objective_id, f"Bearer {token_a}"))["owner_user_id"] == "hosted-owner-auth-a"
    with pytest.raises(Exception) as denied:
        await controller.authenticate(second.objective_id, f"Bearer {token_a}")
    assert getattr(denied.value, "status_code", None) == 403
    assert run_a["isolation_key"] != run_b["isolation_key"]
