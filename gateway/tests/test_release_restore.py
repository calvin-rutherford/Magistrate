"""Offline, synthetic online-backup/restore rehearsal for composed migrations.

Discovers every table and seeds the merged identity, GitHub, credits, memory,
routing, hosted queue, object and perception domains as well as native evidence.
It never connects to an operator database or changes a service lifecycle.
"""
import hashlib
from pathlib import Path
import shutil
import sqlite3
import time

import pytest

from app import db
from app.firstmate_decisions import FirstmateDecisionRequiredEvent, FirstmateDecisionStore
from app.firstmate_execution import (
    FirstmateExecutionStore, FirstmateObjectiveAcceptedEvent,
    FirstmateObjectiveCompletedEvent,
)
from app.magi_chat_store import MagiChatStore, MagiChatNotFound
from app.magi_firstmate_tools import ObjectiveSubmissionStore, FirstmateSubmitObjectiveContract
from app.magi_tool_protocol import MagiToolContext
from app.uploads import save_upload, get_upload, associate_uploads
from app.auth import Principal
from app.billing import CreditLedger
from app.github_app import github_app_store
from app.hosted_execution import HostedExecutionConfig, HostedExecutionStore
from app.magi_routing import ModelRouteContext, ModelRouteStore, RouteCategory, load_routing_catalog
from app.perception import PerceptionEventContract, ingest_perception_event
from app.projects import create_project, bind_github_repository, get_project, ProjectError
from app.push_receipts import PushDeliveryStore
from app.project_memory import MemoryScope, ProjectMemoryStore
from scripts import storage_ops


def table_rows(path):
    with sqlite3.connect(path) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {name: sorted(connection.execute(
            'SELECT * FROM "' + name.replace('"', '""') + '"'
        ).fetchall(), key=repr) for name in names}


def test_composed_schema_backup_restore_preserves_causality_evidence_and_secrets(monkeypatch, tmp_path):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(source))
    objects = tmp_path / "objects"
    monkeypatch.setenv("MAGISTRATE_OBJECT_STORAGE_DIR", str(objects))
    # Start with a populated pre-GitHub/credit/context/hosted/file migration DB.
    # Legacy conversation preservation is also exercised by the v1 fixture in
    # test_magi_native_chat; this covers the composed ordered migration chain.
    with monkeypatch.context() as old_schema:
        old_schema.setattr(db, "_SCHEMA_MIGRATIONS", db._SCHEMA_MIGRATIONS[:2])
        db.init_db()
        owner = "restore-owner"
        db.update_profile(owner, name="Spencer")
        db.upsert_connected_account(owner, "github", "fixture-user", access_token="synthetic-secret")
        project = create_project(owner, name="Magistrate")
        with sqlite3.connect(source) as connection:
            assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (3,)
    db.init_db()
    assert get_project(owner, project["id"])["name"] == "Magistrate"
    db.update_profile("restore-other", name="Other tenant")
    push = PushDeliveryStore(str(source))
    push_claim = push.claim_send(owner, "restore-attention", "restore-fingerprint", "synthetic-token")
    push.accepted(push_claim, "restore-ticket")
    github_app_store.upsert_installation({"id": 8501, "account": {"id": 9501, "login": "fixture-org", "type": "Organization"},
        "repository_selection": "selected", "permissions": {"contents": "read"}, "events": ["installation"]}, user_id=owner)
    github_app_store.upsert_repository(8501, {"id": 10501, "name": "fixture", "full_name": "fixture-org/fixture",
        "owner": {"login": "fixture-org"}, "private": True, "default_branch": "main"})
    bind_github_repository(owner, project["id"], full_name="fixture-org/fixture", html_url="https://github.com/fixture-org/fixture", provider_repository_id="10501")
    memories = ProjectMemoryStore()
    scope = MemoryScope.for_project(owner, project["id"])
    for text in ("Preserve the Alder release fact.", "Preserve the revised Alder release fact."):
        memory = memories.put(owner, scope, memory_key="restore-fact", kind="conversation-fact",
            title="Alder release fact", content=text, source_kind="test", source_id="restore-fixture", actor_session_id="restore-session")
    memories.search(owner, scope, "Alder", purpose="restore-fixture", actor_session_id="restore-session")
    native = MagiChatStore()
    origin = native.prepare_submission(owner, "restore-original-message", "Preserve this exact fact: café 東京.")
    native.complete_submission(owner, origin.assistant_message_id, origin.attempt, "Accepted.\n", latency_ms=1)
    pending = native.prepare_submission(owner, "restore-interrupted-message", "Pending at backup time.")
    context = MagiToolContext(
        owner_user_id=owner, conversation_id=origin.conversation_id,
        turn_id=origin.turn_id, user_message_id=origin.user_message_id,
        assistant_message_id=origin.assistant_message_id, command_authorized=True,
    )
    submissions = ObjectiveSubmissionStore()
    claim = submissions.claim(context=context, invocation_key=hashlib.sha256(b"restore-invocation").hexdigest(), contract=FirstmateSubmitObjectiveContract(
        objective="Preserve the Alder release evidence", project=project["id"],
        constraints=[], acceptance_criteria=["Restore all durable rows"], context_refs=[],
    ))
    submissions.accept(owner, claim)
    assert memory["id"] in claim.context_json
    ledger = CreditLedger()
    ledger.reserve_objective(owner, claim.objective_id, idempotency_key="restore-reservation")
    ledger.settle_objective(owner, claim.objective_id, {
        "provider": "openai", "model": "gpt-4o-mini", "input_tokens": 1000,
        "output_tokens": 100, "compute_milliseconds": 10,
    }, event_id="restore-completed")
    billing_before = ledger.summary(owner)
    routes = ModelRouteStore()
    routes.reserve(ModelRouteContext(owner, RouteCategory.DIRECT_CONVERSATION, True),
        "restore-route", load_routing_catalog().candidates[0], ordinal=1, estimated_micro_usd=100,
        monthly_budget_micro_usd=10000, fallback_from=None)
    hosted_config = HostedExecutionConfig(
        worker_image="registry.example/worker@sha256:" + "a" * 64,
        gateway_url="https://gateway.internal", backend_url="https://isolation.internal",
        github_broker_url="https://broker.internal", client_cert_path="/unused/cert",
        client_key_path="/unused/key", ca_path="/unused/ca", identity_key=b"restore-fixture-identity-key-value",
        network_hosts=("gateway.internal",), github_permissions=("contents:write",),
        max_global=2, max_per_tenant=1, cpu_millis=1000, memory_mib=512, workspace_mib=1024,
        deadline_seconds=600, cleanup_seconds=60, poll_seconds=1,
    )
    # Store-only launch claim: no backend request or worker lifecycle occurs.
    hosted = HostedExecutionStore().claim(hosted_config)
    assert hosted["objective_id"] == claim.objective_id
    execution = FirstmateExecutionStore()
    identity = dict(schema_version="firstmate.execution-event.v1", objective_id=claim.objective_id,
                    task_id=claim.task_id, run_id="restore-run", occurred_at_ms=1_789_330_000_000)
    execution.ingest(owner, FirstmateObjectiveAcceptedEvent(
        **identity, event_id="restore-accepted", phase="objective.accepted",
        objective={"title": "Preserve release evidence", "project": "Magistrate"},
        chat={"conversation_id": origin.conversation_id, "user_message_id": origin.user_message_id},
    ))
    execution.ingest(owner, FirstmateObjectiveCompletedEvent(
        **{**identity, "occurred_at_ms": identity["occurred_at_ms"] + 1},
        event_id="restore-completed", phase="objective.completed",
        evidence={"schema_version": "firstmate.completion-evidence.v1", "result": "completed",
                  "verification": "verified", "checks": [{"check_id": "restore-check", "kind": "test", "label": "Synthetic acceptance", "status": "passed"}],
                  "artifacts": [{"kind": "report", "report_id": "restore-report"}]},
    ))
    decisions = FirstmateDecisionStore()
    decision = FirstmateDecisionRequiredEvent(
        schema_version="firstmate.decision-event.v1", event_type="decision.required",
        source_instance_id="firstmate:restore", source_event_id="fmde_" + "a" * 32,
        task_id="restore-next-task", lifecycle_identity="2026-09-01#1",
        title="Choose a path", question="Which approved path?", project="Magistrate",
        observed_at=1_789_330_000_000, close_mode="release",
    )
    decisions.apply_snapshot(owner, decision.source_instance_id, decision.observed_at, "b" * 64, [decision])
    upload = save_upload(owner, "evidence.txt", "text/plain", b"synthetic file evidence")
    associate_uploads(owner, origin.user_message_id, [upload["upload_id"]])
    perception = PerceptionEventContract.model_validate({
        "schema_version": "magistrate.perception-event.v1", "event_id": "pev_restore_fixture_12345",
        "client": {"client_id": "restore-device", "device_class": "phone", "adapter_id": "restore", "adapter_version": "1.0"},
        "modality": "text", "observed_at_ms": int(time.time() * 1000),
        "context": {"project_id": project["id"], "conversation_id": origin.conversation_id}, "confidence": 0.5,
        "consent": {"captured": True, "purpose": "context", "retention_seconds": 3600},
        "artifact_ref": upload["upload_id"],
        "intent": {"kind": "note", "impact": "none", "provenance": {"adapter_transform": "none"}},
    })
    assert ingest_perception_event(perception, Principal(user_id=owner, session_id="restore-session", scopes=frozenset({"read"}), expires_at=int(time.time()) + 3600)).get("authorization")["executes_action"] is False
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", ("retained-legacy", owner, "captain", 1_789_330_000_000, 1_789_330_000_000))
    expected = table_rows(source)
    expected_messages = native.submission(owner, "restore-original-message")["messages"]
    expected_decisions = decisions.pending(owner)
    for table in ("magi_objective_submissions", "firstmate_execution_events", "firstmate_decisions", "firstmate_decision_events", "activity_records", "oauth_credentials", "chat_uploads", "chat_message_attachments",
                  "workspaces", "projects", "project_repositories", "github_app_installations", "github_app_repositories",
                  "billing_accounts", "credit_ledger", "credit_reservations", "execution_usage_ledger",
                  "project_memory_entries", "project_memory_revisions", "project_memory_terms", "project_memory_audit", "project_memory_retrievals",
                  "magi_model_routes", "hosted_execution_runs", "perception_events", "notification_push_deliveries"):
        assert expected[table], f"Restore fixture must exercise {table}"

    # Exercise the actual operator backup/restore contract, plus independent
    # file snapshots. No live DB is replaced and the snapshot stays immutable.
    source.chmod(0o600)
    manifest = storage_ops.backup(source, backup)
    assert manifest["table_counts"] == {table: len(rows) for table, rows in expected.items()}
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    shutil.copytree(objects, tmp_path / "object-snapshot")
    storage_ops.restore(backup, restored)
    shutil.copytree(tmp_path / "object-snapshot", tmp_path / "restored-objects")
    monkeypatch.setenv("MAGISTRATE_OBJECT_STORAGE_DIR", str(tmp_path / "restored-objects"))
    monkeypatch.setattr(db, "DB_PATH", str(restored))
    db.init_db()
    db.init_db()  # Composition must be repeatable, not just fresh-install safe.
    assert table_rows(restored) == expected
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        encrypted = connection.execute("SELECT access_token_enc FROM oauth_credentials").fetchone()[0]
        assert db.decrypt_token(encrypted) == "synthetic-secret"
    assert CreditLedger().summary(owner) == billing_before
    assert PushDeliveryStore(str(restored)).summary(owner) == {"pending": 1}
    assert get_project(owner, project["id"])["repositories"][0]["provider_repository_id"] == "10501"
    with pytest.raises(ProjectError):
        get_project("restore-other", project["id"])
    assert github_app_store.repository_for_user(owner, 10501)["full_name"] == "fixture-org/fixture"
    assert ProjectMemoryStore().search(owner, scope, "Alder", purpose="restore-check", actor_session_id="restore-session")[0]["id"] == memory["id"]
    restored_upload = get_upload(owner, upload["upload_id"])
    assert Path(restored_upload["path"]).read_bytes() == b"synthetic file evidence"
    assert get_upload("restore-other", upload["upload_id"]) is None
    with sqlite3.connect(restored) as connection:
        token = connection.execute("SELECT worker_token_enc FROM hosted_execution_runs").fetchone()[0]
        assert db.decrypt_token(token) == db.decrypt_token(hosted["worker_token_enc"])
    recovered = MagiChatStore()
    assert recovered.submission(owner, "restore-original-message")["messages"] == expected_messages
    assert FirstmateDecisionStore().pending(owner) == expected_decisions
    with pytest.raises(MagiChatNotFound):
        recovered.list_conversation("other-owner", origin.conversation_id)
    assert recovered.submission(owner, "restore-interrupted-message")["status"] == "pending"
    recovered.recover_orphaned_pending()
    failed = recovered.submission(owner, "restore-interrupted-message")
    assert failed["status"] == "failed"
    assert failed["assistant_message"]["id"] == pending.assistant_message_id
    # The database contains file metadata, not bytes. This synthetic fixture
    # separately restored storage with the same key; it is not a live drill.
    assert upload["status"] == "stored"
    assert b"synthetic file evidence" not in backup.read_bytes()
