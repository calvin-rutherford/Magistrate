"""Persisted Fleet and runtime projections.

Observation is deliberately separated from execution.  These reads use only
Magistrate's objective-submission and structured execution/decision ledgers;
they never invoke Firstmate, Herdr, a shell, or a terminal adapter.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Optional

from app import db

FLEET_PROJECTION_SCHEMA = "magistrate.fleet-projection.v1"
RUNTIME_PROJECTION_SCHEMA = "magistrate.runtime-projection.v1"
_MAX_OBJECTIVES = 2_000
_MAX_EXECUTION_EVENTS = 20_000
_TERMINAL_PHASES = frozenset({
    "objective.completed", "objective.failed", "objective.cancelled",
})
_WORKING_PHASES = frozenset({
    "worker.started", "implementation.started", "tests.started",
    "tests.passed", "tests.failed", "review.started",
})


def _connect() -> sqlite3.Connection:
    db.init_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _iso_timestamp(value: Optional[int]) -> Optional[str]:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return None


def _submission_contract(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return {}
    if not isinstance(value, dict):
        return {}
    objective = value.get("objective")
    project = value.get("project")
    return {
        "title": objective if isinstance(objective, str) and 0 < len(objective) <= 4_000 else None,
        "project": project if isinstance(project, str) and 0 < len(project) <= 160 else None,
    }


def _phase_status(phase: Optional[str], terminal_phase: Optional[str]) -> tuple[str, str]:
    phase = terminal_phase or phase
    if phase == "objective.completed":
        return "done", "completed"
    if phase == "objective.failed":
        return "failed", "failed"
    if phase == "objective.cancelled":
        return "cancelled", "cancelled"
    if phase in _WORKING_PHASES:
        return "in_flight", "working"
    return "queued", "queued"


class StructuredRuntimeProjection:
    """Build owner-qualified compatibility views from canonical SQLite facts."""

    @staticmethod
    def _validate_owner(owner_user_id: str) -> None:
        if (
            not isinstance(owner_user_id, str)
            or not owner_user_id
            or len(owner_user_id) > 128
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in owner_user_id)
        ):
            raise ValueError("A bounded authenticated principal is required.")

    def _tasks(self, owner_user_id: str) -> list[dict[str, Any]]:
        self._validate_owner(owner_user_id)
        with closing(_connect()) as connection:
            submissions = connection.execute(
                """SELECT * FROM magi_objective_submissions
                   WHERE owner_user_id = ? ORDER BY created_at, objective_id LIMIT ?""",
                (owner_user_id, _MAX_OBJECTIVES + 1),
            ).fetchall()
            objectives = connection.execute(
                """SELECT * FROM firstmate_execution_objectives
                   WHERE owner_user_id = ? ORDER BY created_at, objective_id LIMIT ?""",
                (owner_user_id, _MAX_OBJECTIVES + 1),
            ).fetchall()
            events = connection.execute(
                """SELECT * FROM firstmate_execution_events
                   WHERE owner_user_id = ?
                   ORDER BY occurred_at, created_at, event_id LIMIT ?""",
                (owner_user_id, _MAX_EXECUTION_EVENTS + 1),
            ).fetchall()
            decisions = connection.execute(
                """SELECT task_id, COUNT(*) AS pending_count
                   FROM firstmate_decisions
                   WHERE owner_user_id = ? AND state IN ('pending','answering')
                   GROUP BY task_id""",
                (owner_user_id,),
            ).fetchall()
        if (
            len(submissions) > _MAX_OBJECTIVES
            or len(objectives) > _MAX_OBJECTIVES
            or len(events) > _MAX_EXECUTION_EVENTS
        ):
            raise RuntimeError("The persisted Fleet projection exceeds its bounded capacity.")

        events_by_objective: dict[str, list[sqlite3.Row]] = {}
        for event in events:
            events_by_objective.setdefault(str(event["objective_id"]), []).append(event)
        pending_by_task = {str(row["task_id"]): int(row["pending_count"]) for row in decisions}
        execution_by_objective = {str(row["objective_id"]): row for row in objectives}
        execution_by_task = {str(row["task_id"]): row for row in objectives}
        tasks: list[dict[str, Any]] = []
        represented_executions: set[str] = set()

        for submission in submissions:
            contract = _submission_contract(submission["contract_json"])
            objective_id = str(submission["objective_id"])
            task_id = str(submission["task_id"])
            execution = execution_by_objective.get(objective_id) or execution_by_task.get(task_id)
            if execution is not None:
                represented_executions.add(str(execution["objective_id"]))
            task_events = events_by_objective.get(str(execution["objective_id"]), []) if execution else []
            latest = task_events[-1] if task_events else None
            submission_status = str(submission["status"])
            if execution is not None:
                state, status = _phase_status(
                    str(latest["phase"]) if latest else "objective.accepted",
                    str(execution["terminal_phase"]) if execution["terminal_phase"] else None,
                )
            elif submission_status == "failed":
                state, status = "failed", "failed"
            elif submission_status == "submitting":
                state, status = "queued", "submitting"
            else:
                state, status = "queued", "queued"
            if pending_by_task.get(task_id) and status not in {"done", "failed", "cancelled"}:
                state, status = "in_flight", "blocked"
            latest_at = int(latest["occurred_at"]) if latest is not None else None
            title = (
                str(execution["title"]) if execution is not None
                else contract.get("title") or task_id
            )
            project = (
                execution["project"] if execution is not None and execution["project"]
                else contract.get("project")
            )
            tasks.append({
                "id": task_id,
                "task_id": task_id,
                "objective_id": str(execution["objective_id"]) if execution is not None else objective_id,
                "run_id": str(execution["run_id"]) if execution is not None else None,
                "title": title,
                "project": project,
                "state": state,
                "status": status,
                "submission_status": submission_status,
                "latest_phase": str(latest["phase"]) if latest is not None else None,
                "last_event_at": latest_at,
                "accepted_at": submission["accepted_at"],
                "created_at": int(submission["created_at"]),
                "updated_at": max(
                    int(submission["updated_at"]),
                    int(execution["updated_at"]) if execution is not None else 0,
                    int(latest["updated_at"]) if latest is not None else 0,
                ),
                "terminal": status in {"done", "failed", "cancelled"},
                "source": "magistrate-structured",
                "current_state": {
                    "source": (
                        "firstmate.execution-event.v1" if latest is not None
                        else "firstmate.submit_objective"
                    ),
                    "state": status,
                    "observed_at": latest_at,
                },
            })

        for execution in objectives:
            objective_id = str(execution["objective_id"])
            if objective_id in represented_executions:
                continue
            task_id = str(execution["task_id"])
            task_events = events_by_objective.get(objective_id, [])
            latest = task_events[-1] if task_events else None
            state, status = _phase_status(
                str(latest["phase"]) if latest else "objective.accepted",
                str(execution["terminal_phase"]) if execution["terminal_phase"] else None,
            )
            if pending_by_task.get(task_id) and status not in {"done", "failed", "cancelled"}:
                state, status = "in_flight", "blocked"
            latest_at = int(latest["occurred_at"]) if latest is not None else None
            tasks.append({
                "id": task_id,
                "task_id": task_id,
                "objective_id": objective_id,
                "run_id": str(execution["run_id"]),
                "title": str(execution["title"]),
                "project": execution["project"],
                "state": state,
                "status": status,
                "submission_status": None,
                "latest_phase": str(latest["phase"]) if latest is not None else "objective.accepted",
                "last_event_at": latest_at,
                "accepted_at": int(execution["created_at"]),
                "created_at": int(execution["created_at"]),
                "updated_at": max(
                    int(execution["updated_at"]),
                    int(latest["updated_at"]) if latest is not None else 0,
                ),
                "terminal": status in {"done", "failed", "cancelled"},
                "source": "magistrate-structured",
                "current_state": {
                    "source": "firstmate.execution-event.v1",
                    "state": status,
                    "observed_at": latest_at,
                },
            })

        tasks.sort(key=lambda item: (int(item["updated_at"]), str(item["task_id"])), reverse=True)
        return tasks

    def fleet(self, owner_user_id: str) -> dict[str, Any]:
        tasks = self._tasks(owner_user_id)
        runtime = self._runtime_from_tasks(tasks)
        return {
            "schema": FLEET_PROJECTION_SCHEMA,
            "source": "persisted-structured-state",
            "available": True,
            "live_process_probe": False,
            "tasks": tasks,
            "tasks_count": len(tasks),
            "last_event_at": runtime["last_event_at"],
            "persisted_runtime_status": runtime["status"],
        }

    def agents(self, owner_user_id: str) -> list[dict[str, Any]]:
        # This compatibility endpoint exposes objective/run identities, never
        # pane or PID identities. Terminal objectives are historical Activity,
        # not live workers.
        return [{
            "id": task["task_id"],
            "name": task["title"],
            "harness": None,
            "model": None,
            "status": task["status"],
            "pane_id": None,
            "tab_id": None,
            "workspace_id": None,
            "workspace_role": "worker",
            "objective_id": task["objective_id"],
            "task_id": task["task_id"],
            "run_id": task["run_id"],
            "last_event_at": task["last_event_at"],
            "runtime_sources": {"harness": None, "model": None},
            "display_name_source": "magistrate-structured",
        } for task in self._tasks(owner_user_id) if not task["terminal"]]

    @staticmethod
    def _runtime_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        working = [task for task in tasks if task["status"] in {"working", "blocked"}]
        queued = [task for task in tasks if task["status"] in {"queued", "submitting"}]
        event_times = [task["last_event_at"] for task in tasks if task["last_event_at"] is not None]
        update_times = [task["updated_at"] for task in tasks]
        status = "active" if working else "queued" if queued else "idle" if tasks else "unobserved"
        return {
            "schema_version": RUNTIME_PROJECTION_SCHEMA,
            "status": status,
            "observation_mode": "persisted-structured-events",
            "live_process_probe": False,
            "gateway_is_runtime_parent": False,
            "active_objectives": len(working) + len(queued),
            "active_workers": len(working),
            "known_objectives": len(tasks),
            "last_event_at": max(event_times) if event_times else None,
            "last_state_update_at": max(update_times) if update_times else None,
        }

    def runtime(self, owner_user_id: str) -> dict[str, Any]:
        return self._runtime_from_tasks(self._tasks(owner_user_id))

    def migration_context(self, owner_user_id: str, task_id: str) -> Optional[dict[str, Any]]:
        task = next((item for item in self._tasks(owner_user_id) if item["task_id"] == task_id), None)
        if task is None:
            return None
        return {
            "task_id": task["task_id"],
            "objective_id": task["objective_id"],
            "run_id": task["run_id"],
            "project": task["project"],
            "progress": task["latest_phase"] or task["status"],
            "worktree": None,
            "branch": None,
            "brief": task["title"],
            "preservation_plan": ["objective", "structured progress"],
            "not_preserved": ["in-flight process", "terminal state"],
        }

    def recent_activity(self, owner_user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        items: list[dict[str, Any]] = []
        for task in self._tasks(owner_user_id):
            if task["status"] == "done":
                activity_type, description = "task_completed", "Completed task"
                occurred_at = task["last_event_at"]
                suffix = "completed"
            elif task["status"] == "failed":
                activity_type, description = "task_failed", "Task failed"
                occurred_at = task["last_event_at"] or task["updated_at"]
                suffix = "failed"
            elif task["status"] == "cancelled":
                activity_type, description = "task_cancelled", "Task cancelled"
                occurred_at = task["last_event_at"] or task["updated_at"]
                suffix = "cancelled"
            else:
                activity_type, description = "task_requested", "Task requested"
                occurred_at = task["accepted_at"] or task["created_at"]
                suffix = "requested"
            timestamp = _iso_timestamp(occurred_at)
            if timestamp is None:
                continue
            items.append({
                "id": f"firstmate:{task['task_id']}:{suffix}",
                "type": activity_type,
                "title": task["title"],
                "description": description,
                "occurred_at": timestamp,
                "source": "firstmate",
                "project": task["project"] or "Firstmate",
                "url": None,
                "pull_request_number": None,
            })
        items.sort(key=lambda item: item["occurred_at"], reverse=True)
        return items[:limit]
