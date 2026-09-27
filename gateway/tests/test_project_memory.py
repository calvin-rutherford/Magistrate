import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.account_lifecycle import delete_account
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_firstmate_tools import (
    FIRSTMATE_SUBMIT_OBJECTIVE,
    MAGI_REMEMBER,
    FirstmateObjectiveTools,
    ObjectiveDispatchReceipt,
    ObjectiveSubmissionStore,
)
from app.magi_model import MagiModelResult, MagiModelToolCall
from app.magi_tool_protocol import MagiToolContext
from app.auth import issue_session
from app.main import app
from app.projects import create_project
from app.project_memory import (
    MAX_CONTEXT_CHARS,
    MagiContextAssembler,
    MemoryConflict,
    MemoryNotFound,
    MemoryScope,
    ProjectMemoryStore,
)


def scope(owner="captain-a", *, org="org-a", workspace="workspace-a", project="Magistrate", repo=""):
    return MemoryScope.for_owner(
        owner,
        organization_id=org,
        workspace_id=workspace,
        project_id=project,
        repository_id=repo,
    )


def put(store, owner, selected_scope, key, text, *, kind="conversation-fact", importance=3):
    return store.put(
        owner,
        selected_scope,
        memory_key=key,
        kind=kind,
        title=text.split(".", 1)[0][:200],
        content=text,
        importance=importance,
        source_kind="test",
        source_id=key,
        actor_session_id="session-test",
    )


def test_memory_api_derives_owner_and_enforces_scopes(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "memory-api.sqlite3"))
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "memory-user-a")
    db.update_profile("memory-user-a", name="Memory A", email="memory-a@example.test")
    token_a = issue_session("test-bootstrap-secret")["session_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}
    project_a = create_project("memory-user-a", name="Magistrate")
    scope_a = f"?project_id={project_a['id']}"
    client = TestClient(app)
    body = {
        "memory_key": "decision-api-v1",
        "kind": "user-decision",
        "title": "API version decision",
        "content": "Keep the public API on version one for this release.",
        "importance": 4,
        "source_id": "settings-form",
    }
    assert client.put(
        "/api/v1/magi/memory/entries/decision-api-v1" + scope_a, json=body,
    ).status_code == 401
    created = client.put(
        "/api/v1/magi/memory/entries/decision-api-v1" + scope_a,
        headers=headers_a, json=body,
    )
    assert created.status_code == 200, created.text
    entry_id = created.json()["entry"]["id"]
    found = client.get(
        "/api/v1/magi/memory/search?q=public+API+version"
        f"&project_id={project_a['id']}", headers=headers_a,
    )
    assert found.status_code == 200
    assert [item["id"] for item in found.json()["results"]] == [entry_id]

    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "memory-user-b")
    db.update_profile("memory-user-b", name="Memory B", email="memory-b@example.test")
    token_b = issue_session("test-bootstrap-secret")["session_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}
    project_b = create_project("memory-user-b", name="Magistrate")
    scope_b = f"&project_id={project_b['id']}"
    forged_project = client.get(
        "/api/v1/magi/memory/search?q=public+API+version"
        f"&project_id={project_a['id']}", headers=headers_b,
    )
    assert forged_project.status_code == 404
    foreign = client.get(
        "/api/v1/magi/memory/search?q=public+API+version" + scope_b,
        headers=headers_b,
    )
    assert foreign.status_code == 200
    assert foreign.json()["results"] == []
    assert client.delete(
        f"/api/v1/magi/memory/entries/{entry_id}?project_id={project_b['id']}",
        headers=headers_b,
    ).status_code == 404

    monkeypatch.setenv("MAGISTRATE_SESSION_SCOPES", "read")
    token_read = issue_session("test-bootstrap-secret")["session_token"]
    assert client.put(
        "/api/v1/magi/memory/entries/decision-api-v1" + scope_a,
        headers={"Authorization": f"Bearer {token_read}"}, json=body,
    ).status_code == 403


def test_memory_is_searchable_revisioned_durable_and_auditable(monkeypatch, tmp_path):
    path = tmp_path / "memory.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    store = ProjectMemoryStore()
    selected_scope = scope()

    created = put(
        store, "captain-a", selected_scope, "architecture-db",
        "Use PostgreSQL for durable production metadata.",
        kind="architecture-decision", importance=5,
    )
    unchanged = put(
        store, "captain-a", selected_scope, "architecture-db",
        "Use PostgreSQL for durable production metadata.",
        kind="architecture-decision", importance=5,
    )
    updated = put(
        store, "captain-a", selected_scope, "architecture-db",
        "Use PostgreSQL with row-level tenant policies for durable production metadata.",
        kind="architecture-decision", importance=5,
    )
    assert created["id"] == unchanged["id"] == updated["id"]
    assert (created["revision"], unchanged["revision"], updated["revision"]) == (1, 1, 2)

    # A fresh store/process sees the same provider-independent bytes.
    result = ProjectMemoryStore().search(
        "captain-a", selected_scope, "PostgreSQL tenant metadata",
        purpose="test-restart", actor_session_id="session-test",
    )
    assert [item["id"] for item in result] == [created["id"]]
    assert "row-level tenant policies" in result[0]["content"]

    audit = store.audit("captain-a", selected_scope)
    assert audit["chain_valid"] is True
    assert [event["operation"] for event in reversed(audit["events"])] == ["created", "updated"]
    assert audit["retrievals"][0]["purpose"] == "test-restart"
    assert "PostgreSQL" not in json.dumps(audit["retrievals"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE project_memory_audit SET event_sha256 = ? WHERE sequence = 1",
            ("f" * 64,),
        )
    assert store.audit("captain-a", selected_scope)["chain_valid"] is False

    store.delete(
        "captain-a", selected_scope, created["id"], actor_session_id="session-test",
    )
    assert store.search(
        "captain-a", selected_scope, "PostgreSQL",
        purpose="test-delete", actor_session_id="session-test",
    ) == []
    with pytest.raises(MemoryConflict):
        put(store, "captain-a", selected_scope, "architecture-db", "Reuse deleted identity.")
    with sqlite3.connect(path) as connection:
        revisions = connection.execute(
            "SELECT revision, snapshot_json FROM project_memory_revisions WHERE entry_id = ? ORDER BY revision",
            (created["id"],),
        ).fetchall()
    assert [row[0] for row in revisions] == [1, 2, 3]
    assert revisions[-1][1] == '{"deleted":true}'


def test_every_scope_dimension_and_owner_prevent_leakage(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "isolation.sqlite3"))
    store = ProjectMemoryStore()
    base = scope(repo="acme/api")
    project_wide = scope(repo="")
    put(store, "captain-a", project_wide, "shared-standard", "Shared observability standard for Magistrate.")
    put(store, "captain-a", base, "api-secret-design", "API repository uses envelope encryption.")
    put(store, "captain-a", scope(repo="acme/web"), "web-design", "Web repository uses browser sessions.")
    put(store, "captain-a", scope(project="Other", repo="acme/api"), "other-project", "Other project envelope encryption.")
    put(store, "captain-a", scope(workspace="workspace-b", repo="acme/api"), "other-workspace", "Other workspace envelope encryption.")
    put(store, "captain-a", scope(org="org-b", repo="acme/api"), "other-org", "Other organization envelope encryption.")
    put(store, "captain-b", scope("captain-b", repo="acme/api"), "other-owner", "Other owner envelope encryption.")

    results = store.search(
        "captain-a", base, "envelope encryption observability standard",
        purpose="isolation-test", actor_session_id="session-test", limit=20,
    )
    assert {item["source"]["id"] for item in results} == {"api-secret-design", "shared-standard"}
    assert all(item["repository_id"] in {"", "acme/api"} for item in results)

    # A forged tenant cannot be used even by a direct internal caller.
    forged = MemoryScope(
        scope("captain-b").tenant_id, base.organization_id, base.workspace_id,
        base.project_id, base.repository_id,
    )
    with pytest.raises(ValueError, match="tenant"):
        store.search(
            "captain-a", forged, "envelope encryption",
            purpose="forged", actor_session_id="session-test",
        )


def test_account_erasure_removes_memory_projection_audit_and_index(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "memory-erasure.sqlite3"))
    owner = "memory-erasure-owner"
    db.update_profile(owner, name="Memory Erasure", email="erase@example.test")
    project = create_project(owner, name="Erasure Project")
    selected_scope = MemoryScope.for_project(owner, project["id"])
    put(
        ProjectMemoryStore(), owner, selected_scope, "erase-decision",
        "Delete this architecture decision with the account.",
        kind="architecture-decision",
    )
    ProjectMemoryStore().search(
        owner, selected_scope, "architecture decision",
        purpose="erasure-test", actor_session_id="session-test",
    )

    result = delete_account(owner, confirmation=f"DELETE {owner}")
    assert result["status"] == "deleted"
    with sqlite3.connect(db.DB_PATH) as connection:
        for table in (
            "project_memory_entries", "project_memory_terms",
            "project_memory_revisions", "project_memory_audit",
            "project_memory_retrievals",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_memory_bounds_and_security_fail_closed(monkeypatch, tmp_path):
    import app.project_memory as memory_module

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bounds.sqlite3"))
    monkeypatch.setattr(memory_module, "MAX_MEMORY_ENTRIES_PER_SCOPE", 2)
    store = ProjectMemoryStore()
    selected_scope = scope()
    put(store, "captain-a", selected_scope, "one", "First bounded memory fact.")
    put(store, "captain-a", selected_scope, "two", "Second bounded memory fact.")
    with pytest.raises(MemoryConflict, match="scope is full"):
        put(store, "captain-a", selected_scope, "three", "Third bounded memory fact.")
    with pytest.raises(MemoryConflict, match="Immutable memory identity"):
        store.put(
            "captain-a", selected_scope, memory_key="one", kind="conversation-fact",
            title="Changed immutable fact", content="Changed immutable memory fact.",
            source_kind="test", source_id="one", actor_session_id="session-test",
            immutable=True,
        )
    with pytest.raises(ValueError, match="credential"):
        put(
            store, "captain-a", selected_scope, "credential",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        )
    with pytest.raises(ValueError, match="limit"):
        store.search(
            "captain-a", selected_scope, "bounded memory", limit=21,
            purpose="bounds", actor_session_id="session-test",
        )
    with pytest.raises(MemoryNotFound):
        store.delete(
            "captain-a", scope(project="Other"),
            put.__name__.replace("put", "pmem_invalid"), actor_session_id="session-test",
        )


def test_context_is_selective_bounded_and_combines_modality_and_multimodal_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "context.sqlite3"))
    store = ProjectMemoryStore()
    selected_scope = scope(project="Magistrate", repo="acme/api")
    put(store, "captain-a", selected_scope, "goal-search", "Goal: improve semantic search ranking for API documentation.", kind="goal")
    put(store, "captain-a", selected_scope, "unrelated-color", "The dashboard accent color is violet.", kind="preference")
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO firstmate_execution_objectives
               (owner_user_id, objective_id, task_id, run_id, accepted_event_id,
                conversation_id, origin_message_id, title, project, terminal_event_id,
                terminal_phase, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("captain-a", "objective-search", "task-search", "run-search", "event-accepted",
             "mgc_context", "mgm_context", "Improve semantic search ranking", "Magistrate",
             "event-completed", "objective.completed", 10, 20),
        )
        evidence = json.dumps({
            "checks": [{"kind": "test", "label": "Search ranking regression", "status": "passed"}],
            "artifacts": [{"kind": "report", "report_id": "ranking-report"}],
        })
        connection.execute(
            """INSERT INTO firstmate_execution_events
               (owner_user_id, event_id, objective_id, task_id, run_id, phase,
                occurred_at, payload_sha256, payload_json, evidence_json,
                activity_record_id, generation_state, generation_attempt_count,
                assistant_message_id, error_code, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("captain-a", "event-completed", "objective-search", "task-search", "run-search",
             "objective.completed", 20, "a" * 64, "{}", evidence, "activity-search",
             "completed", 1, None, None, 20, 20),
        )
        connection.execute(
            """INSERT INTO firstmate_decisions
               (decision_id, owner_user_id, source_instance_id, task_id,
                lifecycle_identity, revision, state, title, question, project,
                source_event_id, source_payload_sha256, source_observed_at,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("decision-search", "captain-a", "source", "task-search", "hold-search", 2,
             "pending", "Choose search weights", "Should title matches receive more weight?",
             "Magistrate", "decision-event", "b" * 64, 21, 21, 21),
        )
    assembler = MagiContextAssembler(store)
    encoded = assembler.assemble_chat(
        "captain-a", "How should semantic search ranking work?",
        scope=selected_scope, modality="voice",
        attachments=[{
            "upload_id": "upload_1234567890", "filename": "ranking.png",
            "media_type": "image/png", "size": 2048,
        }],
    )
    assert len(encoded) <= MAX_CONTEXT_CHARS
    payload = json.loads(encoded)
    assert payload["modality"] == "voice"
    assert payload["selected_multimodal_context"] == [{
        "upload_id": "upload_1234567890", "name": "ranking.png",
        "media_type": "image/png", "size": 2048,
    }]
    assert [item["id"] for item in payload["project_memory"]] == [
        store._entry_id("captain-a", selected_scope, "goal-search")
    ]
    assert "violet" not in encoded
    assert payload["fleet_outcomes"] == [{
        "objective_id": "objective-search",
        "title": "Improve semantic search ranking",
        "phase": "objective.completed",
        "updated_at": 20,
        "checks": [{"kind": "test", "label": "Search ranking regression", "status": "passed"}],
        "artifacts": [{"kind": "report", "report_id": "ranking-report"}],
    }]
    assert payload["attention"][0]["decision_id"] == "decision-search"
    assert "full conversation" not in encoded.lower()


class RememberingModel:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, system_context, request_id, tools=()):
        self.calls.append((list(messages), system_context, tuple(tools)))
        if messages[-1].role == "tool":
            result = json.loads(messages[-1].content)
            assert result["status"] == "remembered"
            return MagiModelResult("I’ll remember that preference for this project.")
        return MagiModelResult(
            None, finish_reason="tool_calls",
            tool_calls=(MagiModelToolCall(
                "call-remember", MAGI_REMEMBER, json.dumps({
                    "kind": "preference",
                    "title": "Test execution preference",
                    "content": "Prefer focused regression tests before the full suite.",
                    "project": "Magistrate",
                    "repository": "",
                }),
            ),),
        )


@pytest.mark.asyncio
async def test_explicit_chat_memory_is_persisted_and_retrieved_on_a_later_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chat-memory.sqlite3"))
    memory = ProjectMemoryStore()
    db.update_profile("captain-a", name="Captain A", email="captain-a@example.test")
    create_project("captain-a", name="Magistrate")
    model = RememberingModel()
    service = MagiChatService(
        model, store=MagiChatStore(), profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(memory_store=memory),
    )
    remembered = await service.submit(
        "captain-a", "remember-client-0001",
        "Remember that I prefer focused regression tests before the full suite.",
        allow_tools=True,
    )
    assert remembered["status"] == "completed"
    assert remembered["assistant_message"]["content"].startswith("I’ll remember")
    assert [definition.name for definition in model.calls[0][2]] == [
        FIRSTMATE_SUBMIT_OBJECTIVE, MAGI_REMEMBER, "magi.respond",
    ]

    next_provider = CapturingModel("I have that preference.")
    next_service = MagiChatService(
        next_provider, store=MagiChatStore(), profile_loader=lambda _: {},
    )
    await next_service.submit(
        "captain-a", "remember-client-0002", "Which regression tests do I prefer?",
    )
    assert "Prefer focused regression tests before the full suite." in next_provider.calls[0][1]


class CapturingModel:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def complete(self, messages, *, system_context, request_id, tools=()):
        self.calls.append((list(messages), system_context, request_id))
        return MagiModelResult(self.answer)


@pytest.mark.asyncio
async def test_memory_continues_across_service_and_provider_instances(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "continuity.sqlite3"))
    selected_scope = MemoryScope.for_owner("captain-a")
    put(
        ProjectMemoryStore(), "captain-a", selected_scope, "preference-tests",
        "Prefer focused regression tests before the full suite.", kind="preference",
    )
    first_provider = CapturingModel("First provider answer.")
    first_service = MagiChatService(
        first_provider, store=MagiChatStore(), profile_loader=lambda _: {},
    )
    first = await first_service.submit(
        "captain-a", "provider-one-0001", "Which regression tests should I run?",
    )
    assert first["status"] == "completed"
    assert "Prefer focused regression tests" in first_provider.calls[0][1]

    # New service and unrelated model object emulate a harness/provider switch.
    second_provider = CapturingModel("Second provider answer.")
    second_service = MagiChatService(
        second_provider, store=MagiChatStore(), profile_loader=lambda _: {},
    )
    second = await second_service.submit(
        "captain-a", "provider-two-0001", "Remind me which tests I prefer.",
    )
    assert second["status"] == "completed"
    assert "Prefer focused regression tests" in second_provider.calls[0][1]
    assert "First provider answer." in [message.content for message in second_provider.calls[0][0]]

    foreign_provider = CapturingModel("Foreign answer.")
    foreign = MagiChatService(
        foreign_provider, store=MagiChatStore(), profile_loader=lambda _: {},
    )
    await foreign.submit(
        "captain-b", "provider-foreign-0001", "Which regression tests should I run?",
    )
    assert "Prefer focused regression tests" not in foreign_provider.calls[0][1]


class Dispatcher:
    def __init__(self):
        self.calls = []

    async def submit(self, **request):
        self.calls.append(request)
        return ObjectiveDispatchReceipt(already_present=False)


class AcceptingCreditLedger:
    def reserve_objective(self, owner, objective_id, *, idempotency_key):
        return {"owner": owner, "objective_id": objective_id, "idempotency_key": idempotency_key}


@pytest.mark.asyncio
async def test_worker_receives_only_frozen_relevant_objective_context(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "worker.sqlite3"))
    selected_scope = MemoryScope.for_owner("captain-a", project_id="Magistrate")
    memory = ProjectMemoryStore()
    relevant = put(
        memory, "captain-a", selected_scope, "architecture-auth",
        "Architecture decision: authenticate API requests with short bearer sessions.",
        kind="architecture-decision",
    )
    put(
        memory, "captain-a", selected_scope, "unrelated-ios",
        "Preference: use spring animations in the iOS drawer.", kind="preference",
    )
    dispatcher = Dispatcher()
    tools = FirstmateObjectiveTools(
        store=ObjectiveSubmissionStore(MagiContextAssembler(memory)),
        dispatcher=dispatcher,
        credit_ledger=AcceptingCreditLedger(),
    )
    arguments = json.dumps({
        "objective": "Add bearer authentication to the API endpoint.",
        "project": "Magistrate",
        "constraints": ["Preserve Native Chat."],
        "acceptance_criteria": ["Unauthenticated requests are rejected."],
        "context_refs": [],
    })
    call = MagiModelToolCall("call-1", FIRSTMATE_SUBMIT_OBJECTIVE, arguments)
    context = MagiToolContext(
        owner_user_id="captain-a", conversation_id="mgc_context_1234",
        turn_id="mgt_context_1234", user_message_id="mgm_context_user_1234",
        assistant_message_id="mgm_context_assistant_1234", command_authorized=True,
    )
    await tools.execute(call, context=context, invocation_key="a" * 64)
    body = dispatcher.calls[0]["body"]
    context_line = next(line for line in body.splitlines() if line.startswith("Context: "))
    worker_context = json.loads(context_line.removeprefix("Context: "))
    assert [item["id"] for item in worker_context["objective_context"]] == [relevant["id"]]
    assert "spring animations" not in body
    assert "harness history" in body.lower() and "non-authoritative" in body.lower()

    record = next(iter(sqlite3.connect(db.DB_PATH).execute(
        "SELECT context_json, context_sha256 FROM magi_objective_submissions"
    )))
    assert json.loads(record[0]) == worker_context
    assert len(record[1]) == 64

    put(
        memory, "captain-a", selected_scope, "architecture-auth",
        "Architecture decision: use mutual TLS instead of bearer sessions.",
        kind="architecture-decision",
    )
    await tools.execute(call, context=context, invocation_key="a" * 64)
    assert len(dispatcher.calls) == 1
    frozen = next(iter(sqlite3.connect(db.DB_PATH).execute(
        "SELECT context_json, context_sha256 FROM magi_objective_submissions"
    )))
    assert frozen == record
