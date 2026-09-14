import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.activity_store import list_activity, snapshot_activity
from app.auth import issue_session
from app.firstmate_execution import (
    FirstmateExecutionService,
    FirstmateExecutionStore,
    FirstmateObjectiveAcceptedEvent,
    FirstmateObjectiveCompletedEvent,
)
from app.firstmate_execution_api import firstmate_execution_service
from app.magi_chat_api import magi_chat_service, magi_chat_store
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_model import MagiModelError, MagiModelResult
from app.main import app


client = TestClient(app)
EXECUTION_OWNER = "firstmate-execution-test-owner"
EXECUTION_HEADERS: dict[str, str] = {}


class CompletionModel:
    def __init__(self, *, fail_once: bool = False):
        self.fail_once = fail_once
        self.calls = []

    async def complete(self, messages, *, system_context, request_id):
        self.calls.append({
            "messages": messages,
            "system_context": system_context,
            "request_id": request_id,
        })
        if self.fail_once:
            self.fail_once = False
            raise MagiModelError("synthetic_completion_failure")
        return MagiModelResult("The health endpoint objective is complete and verified. ✅")


@pytest.fixture(autouse=True)
def execution_principal(monkeypatch):
    global EXECUTION_HEADERS
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", EXECUTION_OWNER)
    token = issue_session("test-bootstrap-secret")["session_token"]
    EXECUTION_HEADERS = {"Authorization": f"Bearer {token}"}



def native_origin(owner: str, suffix: str, *, store: MagiChatStore = magi_chat_store):
    prepared = store.prepare_submission(
        owner, f"execution-origin-{suffix}", "Add a health endpoint and test it.",
    )
    store.complete_submission(
        owner,
        prepared.assistant_message_id,
        prepared.attempt,
        "I accepted the objective and will keep you updated.",
        latency_ms=1,
    )
    return prepared


def accepted_event(origin, suffix: str, **overrides):
    event = {
        "schema_version": "firstmate.execution-event.v1",
        "event_id": f"event-{suffix}-accepted",
        "objective_id": f"objective-{suffix}",
        "task_id": f"task-{suffix}",
        "run_id": f"run-{suffix}",
        "occurred_at_ms": 1_789_330_000_000,
        "phase": "objective.accepted",
        "objective": {"title": "Add a health endpoint", "project": "Magistrate"},
        "chat": {
            "conversation_id": origin.conversation_id,
            "user_message_id": origin.user_message_id,
        },
    }
    event.update(overrides)
    return event


def progress_event(suffix: str, phase: str, index: int, **overrides):
    event = {
        "schema_version": "firstmate.execution-event.v1",
        "event_id": f"event-{suffix}-{phase.replace('.', '-')}-{index}",
        "objective_id": f"objective-{suffix}",
        "task_id": f"task-{suffix}",
        "run_id": f"run-{suffix}",
        "occurred_at_ms": 1_789_330_000_000 + index,
        "phase": phase,
    }
    event.update(overrides)
    return event


def completed_event(suffix: str, **overrides):
    event = progress_event(suffix, "objective.completed", 90)
    event["evidence"] = {
        "schema_version": "firstmate.completion-evidence.v1",
        "result": "completed",
        "verification": "verified",
        "checks": [
            {
                "check_id": "gateway-pytest",
                "kind": "test",
                "label": "Gateway focused tests",
                "status": "passed",
            },
            {
                "check_id": "acceptance-health",
                "kind": "acceptance",
                "label": "Health endpoint acceptance check",
                "status": "passed",
            },
        ],
        "artifacts": [
            {"kind": "pull-request", "url": "https://github.com/acme/magistrate/pull/42"},
            {"kind": "report", "report_id": f"report-{suffix}"},
            {"kind": "commit", "commit_sha": "abcdef1234567890"},
        ],
    }
    event.update(overrides)
    return event


def post_event(event, *, headers=None):
    return client.post(
        "/api/v1/firstmate/execution-events",
        headers=headers or EXECUTION_HEADERS,
        json=event,
    )


def objective_activity(objective_id: str):
    return [
        record for record in list_activity(EXECUTION_OWNER, limit=200)["records"]
        if record["objective_id"] == objective_id
        and record["source"]["instance_id"] == "firstmate:execution"
    ]


def test_execution_event_route_is_authenticated_strict_and_principal_owned(monkeypatch):
    model = CompletionModel()
    monkeypatch.setattr(magi_chat_service, "model", model)
    origin = native_origin(EXECUTION_OWNER, "auth")
    event = accepted_event(origin, "auth")

    assert client.post("/api/v1/firstmate/execution-events", json=event).status_code == 401
    injected = post_event({**event, "owner_user_id": "attacker"})
    assert injected.status_code == 422
    accepted = post_event(event)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    duplicate = post_event(event)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    changed_identity = post_event({**event, "run_id": "run-auth-other"})
    assert changed_identity.status_code == 409
    assert post_event(progress_event(
        "auth", "worker.started", 1, run_id="run-auth-other",
    )).status_code == 409
    assert post_event(progress_event(
        "auth", "worker.started", 2, task_id="task-auth-other",
    )).status_code == 409
    assert len(objective_activity("objective-auth")) == 1

    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "other-execution-owner")
    other_token = issue_session("test-bootstrap-secret")["session_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    assert client.get(
        "/api/v1/firstmate/execution-events/event-auth-accepted",
        headers=other_headers,
    ).status_code == 404
    assert post_event(progress_event("auth", "worker.started", 2), headers=other_headers).status_code == 404


def test_structured_progress_projects_every_required_stage_without_chat_prose(monkeypatch):
    model = CompletionModel()
    monkeypatch.setattr(magi_chat_service, "model", model)
    origin = native_origin(EXECUTION_OWNER, "timeline")
    assert post_event(accepted_event(origin, "timeline")).status_code == 200
    phases = [
        "worker.started",
        "implementation.started",
        "tests.started",
        "tests.failed",
        "implementation.started",
        "tests.started",
        "tests.passed",
        "review.started",
    ]
    for index, phase in enumerate(phases, start=1):
        response = post_event(progress_event("timeline", phase, index))
        assert response.status_code == 200, response.text
        assert response.json()["event"]["phase"] == phase
        assert response.json()["completion_message"] is None

    records = objective_activity("objective-timeline")
    assert [record["summary"] for record in records] == [
        "Objective was accepted for asynchronous execution.",
        "Execution capacity started for the objective.",
        "Implementation is underway.",
        "Verification tests are running.",
        "Verification tests failed; the objective remains active.",
        "Implementation is underway.",
        "Verification tests are running.",
        "Verification tests passed.",
        "Review is underway.",
    ]
    assert [record["kind"] for record in records] == [
        "objective.accepted",
        "worker.started",
        "implementation.started",
        "tests.started",
        "tests.failed",
        "implementation.started",
        "tests.started",
        "tests.passed",
        "review.started",
    ]
    assert [record["state"] for record in records] == [
        "active", "active", "active", "active", "failed",
        "active", "active", "completed", "active",
    ]
    assert model.calls == []
    projection = snapshot_activity(EXECUTION_OWNER, limit=1)
    assert "objective-timeline" in {
        record["objective_id"] for record in projection["focus_records"]
    }


@pytest.mark.parametrize(
    ("phase", "state"),
    [("objective.failed", "failed"), ("objective.cancelled", "cancelled")],
)
def test_noncompletion_terminal_events_end_activity_without_chat(
    monkeypatch, phase, state,
):
    model = CompletionModel()
    monkeypatch.setattr(magi_chat_service, "model", model)
    suffix = phase.replace(".", "-")
    origin = native_origin(EXECUTION_OWNER, suffix)
    assert post_event(accepted_event(origin, suffix)).status_code == 200

    terminal = post_event(progress_event(suffix, phase, 90))
    assert terminal.status_code == 200
    assert terminal.json()["completion_message"] is None
    records = objective_activity(f"objective-{suffix}")
    assert records[-1]["kind"] == phase
    assert records[-1]["state"] == state
    assert model.calls == []
    assert f"objective-{suffix}" not in {
        record["objective_id"] for record in snapshot_activity(EXECUTION_OWNER)["focus_records"]
    }
    assert post_event(completed_event(suffix)).status_code == 409


def test_verified_completion_persists_evidence_and_adds_one_native_assistant(monkeypatch):
    model = CompletionModel()
    monkeypatch.setattr(magi_chat_service, "model", model)
    monkeypatch.setenv("MAGISTRATE_MAGI_PROJECT_CONTEXT", "UNTRUSTED DEPLOYMENT CONTEXT")
    monkeypatch.setattr(
        magi_chat_service,
        "_profile_loader",
        lambda _owner: (_ for _ in ()).throw(AssertionError("profile is not an outcome fact")),
    )
    origin = native_origin(EXECUTION_OWNER, "complete")
    assert post_event(accepted_event(origin, "complete")).status_code == 200
    for index, phase in enumerate(
        ["worker.started", "implementation.started", "tests.started", "tests.passed", "review.started"],
        start=1,
    ):
        assert post_event(progress_event("complete", phase, index)).status_code == 200

    before = magi_chat_store.list_conversation(
        EXECUTION_OWNER, origin.conversation_id,
    )
    before_cursor = before["latest_change"]
    before_users = [message for message in before["messages"] if message["role"] == "user"]
    completion = post_event(completed_event("complete"))
    assert completion.status_code == 200, completion.text
    result = completion.json()
    assert result["status"] == "accepted"
    assert result["completion_message"]["state"] == "completed"
    generated_id = result["completion_message"]["assistant_message_id"]
    assert generated_id
    assert len(model.calls) == 1

    [provider_message] = model.calls[0]["messages"]
    assert provider_message.role == "user"
    assert model.calls[0]["system_context"] == MagiChatService._verified_outcome_system_context()
    assert "UNTRUSTED" not in model.calls[0]["system_context"]
    prefix, facts_json = provider_message.content.split("\n\n", 1)
    facts = json.loads(facts_json)
    assert "using only the verified facts" in prefix
    assert facts == {
        "schema_version": "magi.verified-outcome.v1",
        "result": "completed",
        "verification": "verified",
        "objective": {"title": "Add a health endpoint", "project": "Magistrate"},
        "completed_at_ms": completed_event("complete")["occurred_at_ms"],
        "checks": [
            {"check_id": "gateway-pytest", "kind": "test", "label": "Gateway focused tests", "status": "passed"},
            {"check_id": "acceptance-health", "kind": "acceptance", "label": "Health endpoint acceptance check", "status": "passed"},
        ],
        "artifacts": [
            {"kind": "pull-request", "value": "https://github.com/acme/magistrate/pull/42"},
            {"kind": "report", "value": "report-complete"},
            {"kind": "commit", "value": "abcdef1234567890"},
        ],
    }
    assert not ({"transcript", "summary", "tool_output", "terminal"} & facts.keys())

    conversation = client.get(
        f"/api/v1/magi/conversations/{origin.conversation_id}", headers=EXECUTION_HEADERS,
    )
    assert conversation.status_code == 200
    messages = conversation.json()["messages"]
    assert [message for message in messages if message["role"] == "user"] == before_users
    generated = next(message for message in messages if message["id"] == generated_id)
    assert generated["role"] == "assistant"
    assert generated["source"] == "magi-native"
    assert generated["status"] == "completed"
    assert generated["reply_to_message_id"] == origin.user_message_id
    assert generated["content"] == "The health endpoint objective is complete and verified. ✅"
    assert generated["turn_id"] != origin.turn_id

    replay = client.get(
        f"/api/v1/magi/conversations/{origin.conversation_id}/replay?after={before_cursor}",
        headers=EXECUTION_HEADERS,
    ).json()
    assert generated_id in {message["id"] for message in replay["messages"]}

    duplicate = post_event(completed_event("complete"))
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["completion_message"]["assistant_message_id"] == generated_id
    assert len(model.calls) == 1
    assert len([
        record for record in objective_activity("objective-complete")
        if record["kind"] == "objective.completed"
    ]) == 1
    assert "objective-complete" not in {
        record["objective_id"] for record in snapshot_activity(EXECUTION_OWNER)["focus_records"]
    }

    with sqlite3.connect(db.DB_PATH) as connection:
        evidence_raw, payload_raw = connection.execute(
            """SELECT evidence_json, payload_json FROM firstmate_execution_events
               WHERE owner_user_id = ? AND event_id = ?""",
            (EXECUTION_OWNER, "event-complete-objective-completed-90"),
        ).fetchone()
    assert json.loads(evidence_raw) == completed_event("complete")["evidence"]
    assert json.loads(payload_raw) == completed_event("complete")
    assert len(payload_raw.encode("utf-8")) < 64 * 1024


def test_completion_requires_closed_verified_evidence_and_known_causality(monkeypatch):
    model = CompletionModel()
    monkeypatch.setattr(magi_chat_service, "model", model)
    origin = native_origin(EXECUTION_OWNER, "evidence")

    assert post_event(completed_event("missing-objective")).status_code == 404
    assert post_event(accepted_event(origin, "evidence")).status_code == 200
    missing = completed_event("evidence")
    missing.pop("evidence")
    assert post_event(missing).status_code == 422
    failed_check = completed_event("evidence")
    failed_check["evidence"]["checks"][0]["status"] = "failed"
    assert post_event(failed_check).status_code == 422
    raw_prose = completed_event("evidence")
    raw_prose["evidence"]["transcript"] = "raw worker completion prose"
    assert post_event(raw_prose).status_code == 422
    unsafe_url = completed_event("evidence")
    unsafe_url["evidence"]["artifacts"][0]["url"] = "https://user:secret@example.com/pull/1"
    assert post_event(unsafe_url).status_code == 422
    assert model.calls == []


def test_failed_completion_generation_retries_explicitly_in_same_canonical_row(monkeypatch):
    model = CompletionModel(fail_once=True)
    monkeypatch.setattr(magi_chat_service, "model", model)
    origin = native_origin(EXECUTION_OWNER, "retry")
    assert post_event(accepted_event(origin, "retry")).status_code == 200

    failed = post_event(completed_event("retry"))
    assert failed.status_code == 200
    failed_result = failed.json()
    assert failed_result["completion_message"]["state"] == "failed"
    failed_id = failed_result["completion_message"]["assistant_message_id"]
    assert len(model.calls) == 1
    failed_message = next(
        message for message in magi_chat_store.list_conversation(
            EXECUTION_OWNER, origin.conversation_id,
        )["messages"] if message["id"] == failed_id
    )
    assert failed_message["status"] == "failed"
    assert failed_message["content"] == ""

    duplicate = post_event(completed_event("retry"))
    assert duplicate.json()["completion_message"]["state"] == "failed"
    assert len(model.calls) == 1
    assert client.post(
        "/api/v1/firstmate/execution-events/event-retry-objective-completed-90/wake",
        headers=EXECUTION_HEADERS,
        json={},
    ).status_code == 422
    retried = client.post(
        "/api/v1/firstmate/execution-events/event-retry-objective-completed-90/wake",
        headers=EXECUTION_HEADERS,
        json={"retry_failed": True},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["completion_message"]["state"] == "completed"
    assert retried.json()["completion_message"]["assistant_message_id"] == failed_id
    assert retried.json()["completion_message"]["attempt"] == 2
    assert len(model.calls) == 2
    completed_message = next(
        message for message in magi_chat_store.list_conversation(
            EXECUTION_OWNER, origin.conversation_id,
        )["messages"] if message["id"] == failed_id
    )
    assert completed_message["status"] == "completed"
    assert completed_message["revision"] == 4


@pytest.mark.asyncio
async def test_interrupted_completion_claim_is_recovered_from_durable_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "execution-recovery.sqlite3"))
    chat_store = MagiChatStore()
    origin = native_origin("recovery-owner", "durable", store=chat_store)
    execution_store = FirstmateExecutionStore()
    accepted = FirstmateObjectiveAcceptedEvent.model_validate(
        accepted_event(origin, "durable")
    )
    completed = FirstmateObjectiveCompletedEvent.model_validate(
        completed_event("durable")
    )
    execution_store.ingest("recovery-owner", accepted)
    execution_store.ingest("recovery-owner", completed)
    claim = execution_store.claim_completion(
        "recovery-owner", completed.event_id, retry_failed=False,
    )
    assert claim is not None

    model = CompletionModel()
    chat_service = MagiChatService(model, store=chat_store, profile_loader=lambda _: {})
    service = FirstmateExecutionService(chat_service, store=execution_store)
    prompt, facts_hash = chat_service._verified_outcome_prompt(service._outcome_facts(claim))
    del prompt
    pending = chat_store.prepare_generated_assistant(
        "recovery-owner",
        completed.event_id,
        facts_hash,
        conversation_id=origin.conversation_id,
        reply_to_message_id=origin.user_message_id,
    )
    assert pending.status == "pending"
    assert chat_store.recover_orphaned_pending() == 1

    recovered_count = await service.recover_pending()
    assert recovered_count >= 1
    tasks = list(service._generation_tasks.values())
    if tasks:
        await asyncio.gather(*tasks)
    if service._recovery_task:
        await service._recovery_task
    result = execution_store.inspect("recovery-owner", completed.event_id)
    assert result["completion_message"]["state"] == "completed"
    assert result["completion_message"]["assistant_message_id"] == pending.assistant_message_id
    projection = snapshot_activity("recovery-owner")
    assert projection["focus_records"] == []
    assert projection["summary"] == {
        "active_objectives": 0, "operation_count": 0, "pending_decisions": 0,
    }
    assert len(model.calls) == 1


def test_execution_request_body_has_a_hard_preparse_bound():
    oversized = b'{"padding":"' + (b"x" * (64 * 1024)) + b'"}'
    response = client.post(
        "/api/v1/firstmate/execution-events",
        headers={**EXECUTION_HEADERS, "Content-Type": "application/json"},
        content=oversized,
    )
    assert response.status_code == 413

    dishonest = client.post(
        "/api/v1/firstmate/execution-events",
        headers={
            **EXECUTION_HEADERS,
            "Content-Type": "application/json",
            "Content-Length": "1",
        },
        content=oversized,
    )
    assert dishonest.status_code == 413
