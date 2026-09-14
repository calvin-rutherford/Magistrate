import asyncio
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import attention_service as attention_module
from app import db
from app.auth import Principal
from app.firstmate_decisions import (
    ANSWER_DECISION_DEFINITION,
    FIRSTMATE_ANSWER_DECISION_TOOL,
    FirstmateCommandResult,
    FirstmateDecisionCommandAdapter,
    FirstmateDecisionError,
    FirstmateDecisionService,
    get_firstmate_decision_magi_context,
    handle_firstmate_answer_decision,
    parse_answer_decision_arguments,
)


class FakeFirstmate:
    fm_home = "/tmp/fm-decision-home"
    fm_root = "/tmp/fm-decision-root"
    fm_root_is_explicit = False

    def __init__(self, records):
        self.records = records
        self.generated = datetime.now(timezone.utc).replace(microsecond=0)

    def bump(self):
        self.generated += timedelta(seconds=1)

    async def get_snapshot(self):
        return {
            "schema": "fm-fleet-snapshot.v1",
            "generated": self.generated.isoformat().replace("+00:00", "Z"),
            "fm_home": self.fm_home,
            "backlog": {"records": self.records},
        }


class FakeCommand:
    def __init__(self, identities):
        self.identities = dict(identities)
        self.answers = []
        self.result = FirstmateCommandResult(True, "answered")

    async def open_identity(self, task_id):
        return self.identities.get(task_id)

    async def answer_decision(
        self, task_id, lifecycle_identity, answer, *, allow_closed_replay=False
    ):
        self.answers.append((task_id, lifecycle_identity, answer, allow_closed_replay))
        if self.result.ok:
            self.identities[task_id] = None
        return self.result


def captain_record(task_id="task-alpha", *, title="Choose an implementation", question="Which path should the worker take?"):
    return {
        "structured": True,
        "id": task_id,
        "state": "in_flight",
        "title": title,
        "repo": "Magistrate",
        "hold_reason": question,
        "hold_kind": "captain",
        "hold_bucket": "live",
        "unresolved_blocker_ids": [],
        "captain_actionable": True,
    }


async def ingest_legacy_snapshot(service, firstmate):
    """Exercise the explicit migration adapter; ordinary reads never call it."""
    return await service.reconcile_snapshot("owner", await firstmate.get_snapshot())


@pytest.fixture
def isolated_decision_db(tmp_path, monkeypatch):
    path = tmp_path / "gateway.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "owner")
    db.init_db()
    return path


def principal(user_id="owner", session_id="session-owner", scopes=frozenset({"command"})):
    return Principal(
        user_id=user_id,
        scopes=scopes,
        session_id=session_id,
        expires_at=int(time.time()) + 3_600,
    )


def insert_native_user_message(
    message_id,
    content,
    *,
    owner="owner",
    created_at=None,
    conversation_id=None,
):
    now = int(time.time() * 1000) if created_at is None else created_at
    conversation_id = conversation_id or f"magc_{owner}"
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT OR IGNORE INTO magi_conversations
               (id,owner_user_id,is_default,created_at,updated_at) VALUES(?,?,1,?,?)""",
            (conversation_id, owner, now, now),
        )
        sequence_index = connection.execute(
            "SELECT COALESCE(MAX(sequence_index), 0) + 1 FROM magi_messages WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO magi_messages
               (id,conversation_id,owner_user_id,turn_id,role,content,status,source,
                client_message_id,reply_to_message_id,attachments_json,sequence_index,
                revision,attempt_count,error_code,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                message_id,
                conversation_id,
                owner,
                f"turn-{message_id}",
                "user",
                content,
                "completed",
                "text",
                f"client-{message_id}",
                None,
                "[]",
                sequence_index,
                1,
                1,
                None,
                now,
                now,
            ),
        )


@pytest.mark.asyncio
async def test_structured_holds_have_stable_owner_scoped_identity_and_natural_projections(
    isolated_decision_db,
):
    firstmate = FakeFirstmate([captain_record()])
    command = FakeCommand({"task-alpha": "2026-09-13T20:00:00Z#2"})
    service = FirstmateDecisionService(firstmate, command=command)

    first = await ingest_legacy_snapshot(service, firstmate)
    repeated = await ingest_legacy_snapshot(service, firstmate)
    assert len(first) == 1
    assert repeated[0]["decision_id"] == first[0]["decision_id"]
    assert repeated[0]["revision"] == 1
    assert repeated[0]["lifecycle_identity"] == "2026-09-13T20:00:00Z#2"
    with sqlite3.connect(db.DB_PATH) as connection:
        event_rows = connection.execute(
            "SELECT event_type,payload_json FROM firstmate_decision_events WHERE owner_user_id='owner'"
        ).fetchall()
    assert len(event_rows) == 1
    assert event_rows[0][0] == "decision.required"
    assert json.loads(event_rows[0][1])["question"] == "Which path should the worker take?"

    with pytest.raises(FirstmateDecisionError) as foreign_projection:
        await service.reconcile("observer")
    assert foreign_projection.value.code == "forbidden"

    attention = service.attention_items("owner", decisions=first)
    assert attention == [
        {
            "id": f"captain-question-{first[0]['decision_id']}",
            "provider": "firstmate",
            "title": "Choose an implementation",
            "subtitle": "Which path should the worker take?",
            "priority": "HIGH",
            "status": "needs-decision",
            "url": f"/attention?item=captain-question-{first[0]['decision_id']}",
            "deep_link": f"/attention?item=captain-question-{first[0]['decision_id']}",
            "target_id": first[0]["decision_id"],
            "project": "Magistrate",
            "requires_action": True,
            "notification_kind": "captain_question",
            "revision": "1",
            "context": {
                "decision_id": first[0]["decision_id"],
                "decision_revision": 1,
                "answer_via": "Magi Chat",
                "source_status": "current",
            },
        }
    ]
    public_projection = json.dumps(attention)
    assert "task-alpha" not in public_projection
    assert "2026-09-13T20:00:00Z#2" not in public_projection

    context = await get_firstmate_decision_magi_context(
        principal(scopes=frozenset({"read"})), service=service, refresh=False
    )
    assert context["source_status"] == "available"
    assert context["decisions"] == [
        {
            "decision_id": first[0]["decision_id"],
            "revision": 1,
            "title": "Choose an implementation",
            "question": "Which path should the worker take?",
            "project": "Magistrate",
            "answer_tool": "firstmate.answer_decision",
            "confirmation_required": True,
        }
    ]

    # A fresh structured snapshot without the hold resolves it. Replaying the
    # older observation cannot reopen or revise the resolved decision.
    old_generated = firstmate.generated
    firstmate.records = []
    firstmate.bump()
    assert await ingest_legacy_snapshot(service, firstmate) == []
    resolved = service.store.get("owner", first[0]["decision_id"])
    assert resolved["state"] == "resolved"
    assert resolved["revision"] == 2
    firstmate.records = [captain_record()]
    firstmate.generated = old_generated
    assert await ingest_legacy_snapshot(service, firstmate) == []
    assert service.store.get("owner", first[0]["decision_id"])["revision"] == 2


@pytest.mark.asyncio
async def test_unified_attention_includes_owner_decision_without_legacy_action(
    isolated_decision_db, monkeypatch
):
    firstmate = FakeFirstmate([captain_record()])
    decision_service = FirstmateDecisionService(
        firstmate,
        command=FakeCommand({"task-alpha": "2026-09-13T20:00:00Z#2"}),
    )
    await ingest_legacy_snapshot(decision_service, firstmate)
    monkeypatch.setattr(
        attention_module.github_service,
        "get_pull_requests",
        AsyncMock(return_value={"items": []}),
    )
    monkeypatch.setattr(attention_module.jira_adapter, "get_assigned_issues", AsyncMock(return_value=[]))
    monkeypatch.setattr(attention_module.teams_adapter, "get_mentions", AsyncMock(return_value=[]))

    service = attention_module.AttentionService(decision_service)
    items = await service.get_unified_attention_items("owner")
    assert len(items) == 1
    assert items[0]["provider"] == "firstmate"
    assert items[0]["subtitle"] == "Which path should the worker take?"
    assert items[0]["context"]["answer_via"] == "Magi Chat"
    assert "action" not in items[0]


@pytest.mark.asyncio
async def test_answer_handler_uses_exact_native_bytes_confirmation_and_idempotent_resume(
    isolated_decision_db,
):
    firstmate = FakeFirstmate([captain_record()])
    command = FakeCommand({"task-alpha": "2026-09-13T20:00:00Z#2"})
    service = FirstmateDecisionService(firstmate, command=command)
    decision = (await ingest_legacy_snapshot(service, firstmate))[0]
    answer = "Use option B.\nPreserve the bounded adapter exactly."
    insert_native_user_message("mgm_answer_one", answer)
    arguments = {
        "decision_id": decision["decision_id"],
        "decision_revision": decision["revision"],
    }

    with pytest.raises(FirstmateDecisionError) as unconfirmed:
        await handle_firstmate_answer_decision(
            principal(),
            arguments,
            native_user_message_id="mgm_answer_one",
            confirmation_token="not-prepared",
            service=service,
        )
    assert unconfirmed.value.code == "confirmation_invalid"
    assert command.answers == []

    prepared = await handle_firstmate_answer_decision(
        principal(), arguments, native_user_message_id="mgm_answer_one", service=service
    )
    assert prepared["status"] == "confirmation_required"
    assert prepared["reversible"] is False
    assert prepared["target"]["question"] == "Which path should the worker take?"
    assert "task-alpha" not in json.dumps(prepared)
    assert ANSWER_DECISION_DEFINITION.name == "firstmate.answer_decision"
    assert ANSWER_DECISION_DEFINITION.parameters == FIRSTMATE_ANSWER_DECISION_TOOL["function"]["parameters"]
    assert "answer" not in json.dumps(ANSWER_DECISION_DEFINITION.parameters)

    completed = await handle_firstmate_answer_decision(
        principal(),
        arguments,
        native_user_message_id="mgm_answer_one",
        confirmation_token=prepared["confirmation_token"],
        service=service,
    )
    assert completed["status"] == "succeeded"
    assert completed["evidence"] == {
        "provider": "firstmate",
        "operation": "firstmate.answer_decision",
        "confirmation_recorded": True,
        "release_requested": True,
        "answer_bytes": len(answer.encode("utf-8")),
    }
    assert answer not in json.dumps(completed)
    assert command.answers == [
        ("task-alpha", "2026-09-13T20:00:00Z#2", answer, False)
    ]

    # The exact duplicate returns durable evidence without invoking Firstmate,
    # even when the caller no longer has the original token.
    replay = await handle_firstmate_answer_decision(
        principal(),
        arguments,
        native_user_message_id="mgm_answer_one",
        confirmation_token="already-consumed",
        service=service,
    )
    assert replay["status"] == "succeeded"
    assert replay["idempotent"] is True
    assert len(command.answers) == 1

    with sqlite3.connect(db.DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM firstmate_decision_answers").fetchone()
        columns = {entry[1] for entry in connection.execute("PRAGMA table_info(firstmate_decision_answers)")}
    assert "answer" not in columns and "content" not in columns
    assert row["answer_sha256"] == hashlib.sha256(answer.encode("utf-8")).hexdigest()
    assert row["native_user_message_id"] == "mgm_answer_one"


@pytest.mark.asyncio
async def test_concurrent_exact_duplicate_does_not_invoke_firstmate_twice(isolated_decision_db):
    firstmate = FakeFirstmate([captain_record()])
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingCommand(FakeCommand):
        async def answer_decision(
            self, task_id, lifecycle_identity, answer, *, allow_closed_replay=False
        ):
            self.answers.append((task_id, lifecycle_identity, answer, allow_closed_replay))
            entered.set()
            await release.wait()
            self.identities[task_id] = None
            return FirstmateCommandResult(True, "answered")

    command = BlockingCommand({"task-alpha": "2026-09-13T20:00:00Z#2"})
    first_service = FirstmateDecisionService(firstmate, command=command)
    second_service = FirstmateDecisionService(firstmate, command=command)
    decision = (await ingest_legacy_snapshot(first_service, firstmate))[0]
    insert_native_user_message("mgm_concurrent_answer", "Use the bounded path.")
    arguments = {"decision_id": decision["decision_id"], "decision_revision": 1}
    prepared = await first_service.prepare_tool_answer(
        principal(), arguments, native_user_message_id="mgm_concurrent_answer"
    )

    first_execution = asyncio.create_task(
        first_service.execute_tool_answer(
            principal(),
            arguments,
            native_user_message_id="mgm_concurrent_answer",
            confirmation_token=prepared["confirmation_token"],
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    insert_native_user_message("mgm_competing_answer", "Use another path.")
    with pytest.raises(FirstmateDecisionError) as competing:
        await second_service.prepare_tool_answer(
            principal(), arguments, native_user_message_id="mgm_competing_answer"
        )
    assert competing.value.code == "duplicate"

    duplicate = await second_service.execute_tool_answer(
        principal(),
        arguments,
        native_user_message_id="mgm_concurrent_answer",
        confirmation_token=prepared["confirmation_token"],
    )
    assert duplicate["status"] == "pending"
    assert duplicate["idempotent"] is True
    assert len(command.answers) == 1

    release.set()
    completed = await asyncio.wait_for(first_execution, timeout=1)
    assert completed["status"] == "succeeded"
    assert len(command.answers) == 1


@pytest.mark.asyncio
async def test_answers_reject_malformed_stale_foreign_unconfirmed_and_resolved_requests(
    isolated_decision_db,
):
    firstmate = FakeFirstmate([captain_record()])
    command = FakeCommand({"task-alpha": "2026-09-13T20:00:00Z#2"})
    service = FirstmateDecisionService(firstmate, command=command)
    decision = (await ingest_legacy_snapshot(service, firstmate))[0]
    insert_native_user_message("mgm_superseded_answer", "Use the old choice.")
    insert_native_user_message("mgm_fresh_answer", "Choose the smaller change.")
    arguments = {"decision_id": decision["decision_id"], "decision_revision": 1}

    with pytest.raises(FirstmateDecisionError) as superseded:
        await service.prepare_tool_answer(
            principal(), arguments, native_user_message_id="mgm_superseded_answer"
        )
    assert superseded.value.code == "native_message_stale"

    with pytest.raises(FirstmateDecisionError) as malformed:
        await service.prepare_tool_answer(
            principal(),
            {**arguments, "answer": "model-controlled text is forbidden"},
            native_user_message_id="mgm_fresh_answer",
        )
    assert malformed.value.code == "malformed"
    with pytest.raises(FirstmateDecisionError) as duplicate_json_key:
        parse_answer_decision_arguments(
            '{"decision_id":"%s","decision_revision":1,"decision_revision":2}'
            % decision["decision_id"]
        )
    assert duplicate_json_key.value.code == "malformed"

    with pytest.raises(FirstmateDecisionError) as foreign:
        await service.prepare_tool_answer(
            principal("not-owner", "foreign-session"),
            arguments,
            native_user_message_id="mgm_fresh_answer",
        )
    assert foreign.value.code == "forbidden"
    with pytest.raises(FirstmateDecisionError) as read_only:
        await service.prepare_tool_answer(
            principal(scopes=frozenset({"read"})),
            arguments,
            native_user_message_id="mgm_fresh_answer",
        )
    assert read_only.value.code == "unauthorized"

    with pytest.raises(FirstmateDecisionError) as stale:
        await service.prepare_tool_answer(
            principal(),
            {**arguments, "decision_revision": 2},
            native_user_message_id="mgm_fresh_answer",
        )
    assert stale.value.code == "stale"

    insert_native_user_message("mgm_foreign_msg", "Foreign answer.", owner="other")
    with pytest.raises(FirstmateDecisionError) as foreign_message:
        await service.prepare_tool_answer(
            principal(), arguments, native_user_message_id="mgm_foreign_msg"
        )
    assert foreign_message.value.code == "native_message_not_found"

    prepared = await service.prepare_tool_answer(
        principal(), arguments, native_user_message_id="mgm_fresh_answer"
    )
    with pytest.raises(FirstmateDecisionError) as wrong_session:
        await service.execute_tool_answer(
            principal(session_id="another-session"),
            arguments,
            native_user_message_id="mgm_fresh_answer",
            confirmation_token=prepared["confirmation_token"],
        )
    assert wrong_session.value.code == "confirmation_invalid"
    assert command.answers == []

    firstmate.records = []
    firstmate.bump()
    await ingest_legacy_snapshot(service, firstmate)
    resolved = service.store.get("owner", decision["decision_id"])
    assert resolved["state"] == "resolved"
    with pytest.raises(FirstmateDecisionError) as already_resolved:
        await service.prepare_tool_answer(
            principal(),
            {
                "decision_id": decision["decision_id"],
                "decision_revision": resolved["revision"],
            },
            native_user_message_id="mgm_fresh_answer",
        )
    assert already_resolved.value.code == "already_resolved"
    assert command.answers == []


@pytest.mark.asyncio
async def test_changed_source_revision_invalidates_confirmation_and_conflicts_fail_closed(
    isolated_decision_db,
):
    firstmate = FakeFirstmate([captain_record()])
    command = FakeCommand({"task-alpha": "2026-09-13T20:00:00Z#2"})
    service = FirstmateDecisionService(firstmate, command=command)
    decision = (await ingest_legacy_snapshot(service, firstmate))[0]
    insert_native_user_message("mgm_revision_answer", "Take the revised path.")
    prepared = await service.prepare_tool_answer(
        principal(),
        {"decision_id": decision["decision_id"], "decision_revision": 1},
        native_user_message_id="mgm_revision_answer",
    )

    firstmate.records = [captain_record(question="Which revised path should the worker take?")]
    firstmate.bump()
    revised = (await ingest_legacy_snapshot(service, firstmate))[0]
    assert revised["decision_id"] == decision["decision_id"]
    assert revised["revision"] == 2
    with pytest.raises(FirstmateDecisionError) as stale:
        await service.execute_tool_answer(
            principal(),
            {"decision_id": decision["decision_id"], "decision_revision": 1},
            native_user_message_id="mgm_revision_answer",
            confirmation_token=prepared["confirmation_token"],
        )
    assert stale.value.code == "stale"
    assert command.answers == []

    # Reusing one producer observation timestamp for different semantic bytes
    # is not treated as a legitimate update.
    firstmate.records = [captain_record(question="A conflicting same-time question?")]
    with pytest.raises(FirstmateDecisionError) as conflict:
        await ingest_legacy_snapshot(service, firstmate)
    assert conflict.value.code == "source_conflict"


@pytest.mark.asyncio
async def test_command_adapter_scrubs_environment_and_uses_private_decision_file(tmp_path, monkeypatch):
    root = tmp_path / "root"
    home = tmp_path / "home"
    runtime_home = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    home.mkdir()
    runtime_home.mkdir()
    script = root / "bin" / "fm-captain-hold.sh"
    script.write_text(
        """#!/bin/sh
set -eu
test -z "${MAGISTRATE_SECRET_KEY+x}"
case "$1" in
  open)
    printf '%s\\n' '2026-09-13T20:00:00Z#2'
    ;;
  answer)
    test "$3" = --decision-file
    test "$5" = --release
    test "$(stat -c %a "$4")" = 600
    printf '%s\\n' "$4" > "$FM_HOME/input-path"
    cp "$4" "$FM_HOME/captured-answer"
    printf 'released: %s\\n' "$2"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    script.chmod(0o700)

    class LocalFirstmate:
        fm_root = str(root)
        fm_home = str(home)
        fm_root_is_explicit = False

        @staticmethod
        def get_trusted_tool_path():
            return "/usr/bin:/bin"

        @staticmethod
        def get_trusted_runtime_home():
            return str(runtime_home)

    monkeypatch.setenv("MAGISTRATE_ENV", "test")
    monkeypatch.setenv("MAGISTRATE_SECRET_KEY", "must-not-enter-child")
    adapter = FirstmateDecisionCommandAdapter(LocalFirstmate(), timeout_seconds=5)
    result = await adapter.answer_decision(
        "task-alpha", "2026-09-13T20:00:00Z#2", "Exact answer bytes.\n"
    )
    assert result == FirstmateCommandResult(True, "answered")
    assert (home / "captured-answer").read_bytes() == b"Exact answer bytes.\n"
    private_path = Path((home / "input-path").read_text(encoding="utf-8").strip())
    assert not private_path.exists()

    stale = await adapter.answer_decision(
        "task-alpha", "2026-09-13T20:00:00Z#99", "Must not be submitted."
    )
    assert stale == FirstmateCommandResult(False, "stale")
    assert (home / "captured-answer").read_bytes() == b"Exact answer bytes.\n"
