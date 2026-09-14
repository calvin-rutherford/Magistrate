"""Regressions for the process-free Gateway observation boundary."""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import issue_session
import app.main as gateway
from app.firstmate_decisions import _event_id
from app.magi_firstmate_tools import (
    FIRSTMATE_SUBMIT_OBJECTIVE,
    FirstmateObjectiveTools,
    ObjectiveDispatchReceipt,
    ObjectiveSubmissionStore,
    parse_submit_objective_arguments,
)
from app.magi_model import MagiModelToolCall
from app.magi_tool_protocol import MagiToolContext


APP_ROOT = Path(__file__).parents[1] / "app"


@dataclass(frozen=True)
class RuntimeHarness:
    client: TestClient
    headers: dict[str, str]


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def submit(self, **request: str) -> ObjectiveDispatchReceipt:
        self.calls.append(request)
        return ObjectiveDispatchReceipt(already_present=False)


def objective_call(suffix: str) -> tuple[MagiModelToolCall, MagiToolContext]:
    contract = parse_submit_objective_arguments(json.dumps({
        "objective": "Keep Gateway observation independent of execution runtime.",
        "project": "Magistrate",
        "constraints": ["Do not inspect Herdr or run a fleet snapshot."],
        "acceptance_criteria": ["Repeated UI reads preserve the assigned task."],
        "context_refs": [f"message:mgm_{suffix}"],
    }))
    call = MagiModelToolCall(
        f"call_{suffix}", FIRSTMATE_SUBMIT_OBJECTIVE,
        contract.model_dump_json(),
    )
    context = MagiToolContext(
        owner_user_id="default_user",
        conversation_id=f"mgc_{suffix}",
        turn_id=f"mgt_{suffix}",
        user_message_id=f"mgm_{suffix}",
        assistant_message_id=f"mgm_assistant_{suffix}",
        command_authorized=True,
    )
    return call, context


@pytest.fixture
def native_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "runtime-read-boundary.sqlite3"))
    db.init_db()
    monkeypatch.setenv("MAGISTRATE_NATIVE_CHAT_ENABLED", "true")
    monkeypatch.setenv("MAGISTRATE_LEGACY_CHAT_ENABLED", "false")
    monkeypatch.setenv("MAGISTRATE_PI_OWNERSHIP_ENABLED", "false")
    monkeypatch.setenv("MAGISTRATE_DISABLE_NOTIFICATION_RECONCILER", "true")

    forbidden = AsyncMock(side_effect=AssertionError("runtime observation boundary crossed"))
    for target, name in (
        (gateway.herdr_client, "get_snapshot"),
        (gateway.herdr_client, "list_agents"),
        (gateway.herdr_client, "list_fleet_agents"),
        (gateway.herdr_client, "read_agent_output"),
        (gateway.herdr_client, "get_agent_history"),
        (gateway.herdr_client, "prompt_agent"),
        (gateway.herdr_client, "interrupt_agent"),
        (gateway.fm_client, "get_snapshot"),
        (gateway.fm_client, "get_attention_items"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        AsyncMock(side_effect=AssertionError("a read attempted to start a process")),
    )
    monkeypatch.setattr(
        gateway.github_service, "get_pull_requests", AsyncMock(return_value={"items": []}),
    )
    monkeypatch.setattr(
        gateway.github_service, "get_merged_pull_requests", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        gateway.jira_adapter, "get_assigned_issues", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        gateway.teams_adapter, "get_mentions", AsyncMock(return_value=[]),
    )
    token = issue_session("test-bootstrap-secret")["session_token"]
    return RuntimeHarness(
        TestClient(gateway.app), {"Authorization": f"Bearer {token}"},
    )


def _process_identity(pid: int) -> tuple[str, str, str, str, str]:
    # pid, parent, process group, session, and kernel start time are enough to
    # detect a stop/restart/reparent in this bounded helper process.
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    return fields[0], fields[3], fields[4], fields[5], fields[21]


def _read_routes(runtime: RuntimeHarness) -> None:
    requests = (
        ("get", "/api/v1/health", None),
        ("get", "/api/v1/runtime", None),
        ("get", "/api/v1/fleet", None),
        ("get", "/api/v1/agents", None),
        ("get", "/api/v1/magi/conversations/current", None),
        ("get", "/api/v1/activity?reconcile=true", None),
        ("get", "/api/v1/activity/replay?after=0", None),
        ("get", "/api/v1/activity/snapshot?reconcile=true", None),
        ("get", "/api/v1/recent-activity", None),
        ("get", "/api/v1/attention/unified", None),
        ("get", "/api/v1/notifications/events", None),
        ("post", "/api/v1/activity/catch-up", {
            "after": 0,
            "limit": 100,
            "reconcile": True,
        }),
    )
    for method, path, payload in requests:
        request = getattr(runtime.client, method)
        response = (
            request(path, headers=runtime.headers, json=payload)
            if payload is not None
            else request(path, headers=runtime.headers)
        )
        assert response.status_code == 200, (path, response.text)


def test_manual_runtime_start_and_stop_are_not_owned_or_reversed_by_reads(native_runtime):
    baseline = native_runtime.client.get(
        "/api/v1/runtime", headers=native_runtime.headers,
    ).json()["persisted_runtime"]
    helper = subprocess.Popen(
        ["/bin/sleep", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        before = _process_identity(helper.pid)
        for _ in range(5):
            for path in ("/api/v1/health", "/api/v1/runtime", "/api/v1/fleet", "/api/v1/agents"):
                assert native_runtime.client.get(
                    path, headers=native_runtime.headers,
                ).status_code == 200
        assert helper.poll() is None
        assert _process_identity(helper.pid) == before
        while_running = native_runtime.client.get(
            "/api/v1/runtime", headers=native_runtime.headers,
        ).json()["persisted_runtime"]
        assert while_running == baseline
        assert while_running["gateway_is_runtime_parent"] is False

        helper.terminate()
        stopped_returncode = helper.wait(timeout=5)
        _read_routes(native_runtime)
        assert helper.poll() == stopped_returncode
        assert not Path(f"/proc/{helper.pid}").exists()
        after_stop = native_runtime.client.get(
            "/api/v1/runtime", headers=native_runtime.headers,
        ).json()["persisted_runtime"]
        assert after_stop == baseline
    finally:
        if helper.poll() is None:
            helper.terminate()
            helper.wait(timeout=5)


@pytest.mark.asyncio
async def test_repeated_ui_reads_preserve_task_assignment(native_runtime):
    dispatcher = RecordingDispatcher()
    store = ObjectiveSubmissionStore()
    tools = FirstmateObjectiveTools(store=store, dispatcher=dispatcher)
    call, context = objective_call("read_stability")
    result = await tools.execute(call, context=context, invocation_key="a" * 64)
    objective_id = result.payload["objective_id"]
    before = store.get("default_user", objective_id)

    _read_routes(native_runtime)
    _read_routes(native_runtime)

    assert store.get("default_user", objective_id) == before
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["task_id"] == before["task_id"]


def test_observation_code_has_no_process_group_termination():
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "killpg", f"process-group kill in {path}:{node.lineno}"
            if isinstance(node, (ast.Call, ast.AsyncFunctionDef, ast.FunctionDef)):
                for keyword in getattr(node, "keywords", ()):
                    assert not (
                        keyword.arg == "start_new_session"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ), f"new process group in {path}:{node.lineno}"


@pytest.mark.asyncio
async def test_startup_with_herdr_stopped_does_not_observe_or_schedule_runtime(native_runtime, monkeypatch):
    monkeypatch.setattr(gateway, "validate_magi_chat_configuration", lambda: None)
    monkeypatch.setattr(gateway, "validate_friend_beta_configuration", lambda: None)
    monkeypatch.setattr(gateway.MagiChatStore, "recover_orphaned_pending", lambda self: 0)
    monkeypatch.setattr(
        gateway.firstmate_execution_service, "recover_pending", AsyncMock(return_value={}),
    )
    gateway._notification_reconciler_task = None
    gateway._pi_ownership_reconciler_task = None

    await gateway.start_notification_reconciler()

    assert gateway._notification_reconciler_task is None
    assert gateway._pi_ownership_reconciler_task is None
    assert not hasattr(gateway, "_activity_reconciler_task")
    health = native_runtime.client.get(
        "/api/v1/health", headers=native_runtime.headers,
    ).json()
    assert health["status"] in {"healthy", "degraded"}
    assert health["herdr_observation"] == "not-probed"
    assert health["herdr_socket_connected"] is False


def test_non_execution_routes_cannot_reach_firstmate_or_herdr_runtime(native_runtime):
    _read_routes(native_runtime)
    # Every terminal/process compatibility route is disabled as one boundary,
    # including worker targets rather than only the captain alias.
    assert native_runtime.client.get(
        "/api/v1/agents/worker-1/history", headers=native_runtime.headers,
    ).status_code == 404
    assert native_runtime.client.post(
        "/api/v1/agents/worker-1/interrupt", headers=native_runtime.headers,
    ).status_code == 404
    assert native_runtime.client.get(
        "/api/v1/captain/output", headers=native_runtime.headers,
    ).status_code == 404
    assert native_runtime.client.post(
        "/api/v1/voice/moves", headers=native_runtime.headers, json={
            "schema_version": "voice-move.v1",
            "utterance": "show status",
            "idempotency_key": "read-only-status",
        },
    ).status_code == 404


def test_pushed_decision_projection_replaces_snapshot_polling(native_runtime):
    semantic = {
        "task_id": "task-pushed-decision",
        "lifecycle_identity": "2026-09-13T20:00:00Z#2",
        "title": "Choose the bounded implementation",
        "question": "Which bounded implementation should continue?",
        "project": "Magistrate",
        "close_mode": "release",
    }
    observed_at = 1_789_330_000_000
    event = {
        "schema_version": "firstmate.decision-event.v1",
        "event_type": "decision.required",
        "source_instance_id": "firstmate:main",
        "source_event_id": _event_id(
            "firstmate:main", semantic["task_id"],
            semantic["lifecycle_identity"], semantic,
        ),
        **semantic,
        "observed_at": observed_at,
    }
    batch = {
        "schema_version": "firstmate.decision-events.v1",
        "source_instance_id": "firstmate:main",
        "observed_at": observed_at,
        "complete": True,
        "events": [event],
    }
    assert native_runtime.client.post(
        "/api/v1/firstmate/decision-events", json=batch,
    ).status_code == 401
    invalid = native_runtime.client.post(
        "/api/v1/firstmate/decision-events",
        headers=native_runtime.headers,
        json={**batch, "events": [{**event, "source_event_id": "fmde_" + "0" * 32}]},
    )
    assert invalid.status_code == 503
    accepted = native_runtime.client.post(
        "/api/v1/firstmate/decision-events",
        headers=native_runtime.headers,
        json=batch,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["pending_count"] == 1
    duplicate = native_runtime.client.post(
        "/api/v1/firstmate/decision-events",
        headers=native_runtime.headers,
        json=batch,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["pending_count"] == 1
    attention = native_runtime.client.get(
        "/api/v1/attention/unified", headers=native_runtime.headers,
    ).json()
    assert len(attention) == 1
    assert attention[0]["target_id"].startswith("fmd_")
    assert attention[0]["subtitle"] == semantic["question"]
    assert "task_id" not in attention[0]["context"]

    resolved = native_runtime.client.post(
        "/api/v1/firstmate/decision-events",
        headers=native_runtime.headers,
        json={
            "schema_version": "firstmate.decision-events.v1",
            "source_instance_id": "firstmate:main",
            "observed_at": observed_at + 1,
            "complete": True,
            "events": [],
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["pending_count"] == 0


@pytest.mark.asyncio
async def test_explicit_authorized_delegation_still_dispatches_once(native_runtime):
    dispatcher = RecordingDispatcher()
    tools = FirstmateObjectiveTools(
        store=ObjectiveSubmissionStore(), dispatcher=dispatcher,
    )
    call, context = objective_call("explicit_delegation")

    first = await tools.execute(call, context=context, invocation_key="b" * 64)
    replay = await tools.execute(call, context=context, invocation_key="b" * 64)

    assert first.payload == replay.payload
    assert first.payload["status"] == "accepted"
    assert len(dispatcher.calls) == 1
