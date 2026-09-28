import json
import sqlite3

import httpx
from fastapi.testclient import TestClient

from app import db
from app.auth import issue_session
from app.firstmate_execution import FirstmateExecutionService, FirstmateExecutionStore
import app.firstmate_execution_api as execution_api
import app.magi_chat_api as magi_api
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_firstmate_tools import (
    FirstmateObjectiveTools,
    ObjectiveDispatchReceipt,
    ObjectiveSubmissionStore,
)
from app.magi_model import OpenAIMagiModel
from app.main import app


ACCEPTANCE_REQUEST = (
    'Create a small test project called magi-execution-test with a README containing '
    '"Hello from Magistrate."'
)


class RepresentativeFirstmateBackend:
    """Closest safe worker boundary: dispatch performs real work in tmp_path.

    A live Firstmate dispatch can start the shared execution runtime, so this
    focused regression substitutes only that external boundary. Everything from
    the authenticated HTTP endpoint through objective persistence and structured
    completion evidence remains production code.
    """

    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []
        self.started = False

    async def submit(self, **request):
        self.calls.append(request)
        [payload_line] = [
            line for line in request["body"].splitlines() if line.startswith("Payload: ")
        ]
        contract = json.loads(payload_line.removeprefix("Payload: "))
        assert contract["objective"] == ACCEPTANCE_REQUEST
        assert contract["project"] == "Magistrate"
        self.started = True
        project = self.workspace / "magi-execution-test"
        project.mkdir(parents=True)
        (project / "README.md").write_text("Hello from Magistrate.\n", encoding="utf-8")
        return ObjectiveDispatchReceipt(already_present=False)


def test_acceptance_request_delegates_once_and_completes_only_from_evidence(monkeypatch, tmp_path):
    database = tmp_path / "magi-execution-handoff.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(database))
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "magi-execution-owner")
    monkeypatch.setenv("MAGISTRATE_SESSION_SCOPES", "read,command")

    provider_requests = []
    objective_tool_calls = 0
    backend = RepresentativeFirstmateBackend(tmp_path / "worker")

    objective_arguments = json.dumps({
        "objective": ACCEPTANCE_REQUEST,
        "project": "Magistrate",
        "constraints": ["Keep execution internals out of Native Chat."],
        "acceptance_criteria": [
            "The magi-execution-test project exists.",
            'README.md contains exactly "Hello from Magistrate."',
        ],
        "context_refs": [],
    }, separators=(",", ":"))

    async def provider(request: httpx.Request) -> httpx.Response:
        nonlocal objective_tool_calls
        body = json.loads(request.content)
        provider_requests.append(body)
        assert request.url.path == "/v1/responses"
        if body.get("tools"):
            assert body["tool_choice"] == "required"
            assert body["parallel_tool_calls"] is False
            assert [tool["name"] for tool in body["tools"]] == [
                "firstmate__submit_objective", "magi__remember", "magi__respond",
            ]
            assert body["input"][-1] == {"role": "user", "content": ACCEPTANCE_REQUEST}
            objective_tool_calls += 1
            return httpx.Response(200, json={
                "status": "completed",
                "output": [{
                    "type": "function_call",
                    "call_id": "call_magi_execution_acceptance",
                    "name": "firstmate__submit_objective",
                    "arguments": objective_arguments,
                }],
            })
        if body["instructions"] == MagiChatService._verified_outcome_system_context():
            project = tmp_path / "worker" / "magi-execution-test"
            assert (project / "README.md").read_text(encoding="utf-8") == (
                "Hello from Magistrate.\n"
            )
            facts = json.loads(body["input"][0]["content"].split("\n\n", 1)[1])
            assert facts["result"] == "completed"
            assert facts["verification"] == "verified"
            assert facts["checks"] == [{
                "check_id": "acceptance-readme",
                "kind": "acceptance",
                "label": "Project and README contents verified",
                "status": "passed",
            }]
            return httpx.Response(200, json={
                "status": "completed",
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": (
                            "magi-execution-test is complete. Its README was verified to contain "
                            '“Hello from Magistrate.”'
                        ),
                    }],
                }],
            })
        assert body["input"][-1]["type"] == "function_call_output"
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "I've accepted that objective. I'll keep you updated here.",
                }],
            }],
        })

    chat_store = MagiChatStore()
    objective_store = ObjectiveSubmissionStore()
    chat_service = MagiChatService(
        OpenAIMagiModel(
            api_key="test-only",
            model="test-model",
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(provider),
        ),
        store=chat_store,
        profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(
            store=objective_store,
            dispatcher=backend,
        ),
    )
    execution_service = FirstmateExecutionService(
        chat_service,
        store=FirstmateExecutionStore(),
    )
    monkeypatch.setattr(magi_api, "magi_chat_service", chat_service)
    monkeypatch.setattr(execution_api, "firstmate_execution_service", execution_service)

    token = issue_session("test-bootstrap-secret")["session_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)
    submission_body = {
        "client_message_id": "magi-execution-acceptance-0001",
        "content": ACCEPTANCE_REQUEST,
    }

    submitted = client.post("/api/v1/magi/messages", headers=headers, json=submission_body)
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert result["status"] == "completed"
    assert result["assistant_message"]["content"] == (
        "I've accepted that objective. I'll keep you updated here."
    )
    assert "complete" not in result["assistant_message"]["content"].lower()
    assert objective_tool_calls == 1
    assert backend.started is True
    assert len(backend.calls) == 1
    assert (tmp_path / "worker" / "magi-execution-test" / "README.md").read_text(
        encoding="utf-8"
    ) == "Hello from Magistrate.\n"

    duplicate = client.post("/api/v1/magi/messages", headers=headers, json=submission_body)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert objective_tool_calls == 1
    assert len(backend.calls) == 1

    with sqlite3.connect(database) as connection:
        objectives = connection.execute(
            """SELECT objective_id, task_id, status FROM magi_objective_submissions
               WHERE owner_user_id = ?""",
            ("magi-execution-owner",),
        ).fetchall()
    assert len(objectives) == 1
    objective_id, task_id, status = objectives[0]
    assert status == "accepted"

    event_base = {
        "schema_version": "firstmate.execution-event.v1",
        "objective_id": objective_id,
        "task_id": task_id,
        "run_id": "run-magi-execution-acceptance",
    }
    accepted_event = {
        **event_base,
        "event_id": "event-magi-execution-accepted",
        "occurred_at_ms": 1_789_330_000_000,
        "phase": "objective.accepted",
        "objective": {
            "title": "Create magi-execution-test",
            "project": "Magistrate",
        },
        "chat": {
            "conversation_id": result["conversation"]["id"],
            "user_message_id": result["user_message"]["id"],
        },
    }
    accepted = client.post(
        "/api/v1/firstmate/execution-events", headers=headers, json=accepted_event,
    )
    assert accepted.status_code == 200, accepted.text
    started = client.post(
        "/api/v1/firstmate/execution-events",
        headers=headers,
        json={
            **event_base,
            "event_id": "event-magi-execution-worker-started",
            "occurred_at_ms": 1_789_330_000_001,
            "phase": "worker.started",
        },
    )
    assert started.status_code == 200, started.text

    conversation_url = f"/api/v1/magi/conversations/{result['conversation']['id']}"
    before_evidence = client.get(conversation_url, headers=headers)
    assert before_evidence.status_code == 200
    assert len(before_evidence.json()["messages"]) == 2
    assert all(
        "complete" not in message["content"].lower()
        for message in before_evidence.json()["messages"]
        if message["role"] == "assistant"
    )

    completion = {
        **event_base,
        "event_id": "event-magi-execution-completed",
        "occurred_at_ms": 1_789_330_000_002,
        "phase": "objective.completed",
    }
    rejected = client.post(
        "/api/v1/firstmate/execution-events", headers=headers, json=completion,
    )
    assert rejected.status_code == 422
    assert len(client.get(conversation_url, headers=headers).json()["messages"]) == 2

    completion["evidence"] = {
        "schema_version": "firstmate.completion-evidence.v1",
        "result": "completed",
        "verification": "verified",
        "checks": [{
            "check_id": "acceptance-readme",
            "kind": "acceptance",
            "label": "Project and README contents verified",
            "status": "passed",
        }],
        "artifacts": [{
            "kind": "report",
            "report_id": "magi-execution-test-artifacts",
        }],
    }
    completed = client.post(
        "/api/v1/firstmate/execution-events", headers=headers, json=completion,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["completion_message"]["state"] == "completed"

    final_messages = client.get(conversation_url, headers=headers).json()["messages"]
    assert len(final_messages) == 3
    assert final_messages[-1]["content"] == (
        "magi-execution-test is complete. Its README was verified to contain "
        '“Hello from Magistrate.”'
    )
    visible_chat = "\n".join(message["content"] for message in final_messages).lower()
    for internal in (
        "firstmate", "submit_objective", "tool call", "tool result", "task_id",
        "objective_id", "herdr", "terminal", objective_id.lower(), task_id.lower(),
    ):
        assert internal not in visible_chat

    replayed_completion = client.post(
        "/api/v1/firstmate/execution-events", headers=headers, json=completion,
    )
    assert replayed_completion.status_code == 200
    assert replayed_completion.json()["status"] == "duplicate"
    assert objective_tool_calls == 1
    assert len(backend.calls) == 1
    assert len(provider_requests) == 3
