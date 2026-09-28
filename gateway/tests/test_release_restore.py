"""Offline, synthetic online-backup/restore rehearsal for composed migrations.

This intentionally discovers every table. New workstreams must seed their own
representative rows here or add a migration fixture before claiming coverage.
It never connects to an operator database or changes a service lifecycle.
"""
import hashlib
import shutil
import sqlite3

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
from app.uploads import save_upload


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
    monkeypatch.setenv("MAGISTRATE_CHAT_UPLOAD_DIR", str(tmp_path / "uploads"))
    db.init_db()
    owner = "restore-owner"
    db.update_profile(owner, name="Spencer")
    db.upsert_connected_account(owner, "github", "fixture-user", access_token="synthetic-secret")
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
        objective="Preserve the release evidence", project="Magistrate",
        constraints=[], acceptance_criteria=["Restore all durable rows"], context_refs=[],
    ))
    submissions.accept(owner, claim)
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
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", ("retained-legacy", owner, "captain", 1_789_330_000_000, 1_789_330_000_000))
    expected = table_rows(source)
    expected_messages = native.submission(owner, "restore-original-message")["messages"]
    expected_decisions = decisions.pending(owner)
    for table in ("magi_objective_submissions", "firstmate_execution_events", "firstmate_decisions", "firstmate_decision_events", "activity_records", "oauth_credentials", "chat_uploads"):
        assert expected[table], f"Restore fixture must exercise {table}"

    # The same SQLite online-backup primitive used by guarded deployment, with
    # both handles closed before restoring to a NEW target (never over live DB).
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        with sqlite3.connect(backup) as snapshot:
            connection.backup(snapshot)
    backup.chmod(0o600)
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    shutil.copyfile(backup, restored)
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
    # The database contains file metadata, not file bytes. A complete production
    # recovery must separately restore the storage snapshot and secret versions.
    assert upload["status"] == "stored"
    assert b"synthetic file evidence" not in backup.read_bytes()
