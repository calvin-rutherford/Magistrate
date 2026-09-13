"""Structured Firstmate execution events and verified-completion bridge.

This module accepts only a closed event vocabulary. It never reads a terminal,
worker transcript, hook payload, tool output, or Herdr state. Events are scoped
by the authenticated principal supplied by the route/tool layer, projected into
the existing canonical Activity ledger, and kept distinct from Chat. Only an
``objective.completed`` event carrying validated verification evidence may
wake a new provider-native Magi assistant message.
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
import unicodedata
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import db
from app.activity_store import (
    _contains_sensitive_text,
    _upsert_activity,
    canonical_json,
    safe_pull_request_url,
)
from app.magi_chat_service import (
    MagiChatService,
    MagiOutcomeArtifact,
    MagiOutcomeCheck,
    MagiVerifiedOutcome,
)

FIRSTMATE_EXECUTION_EVENT_SCHEMA = "firstmate.execution-event.v1"
FIRSTMATE_COMPLETION_EVIDENCE_SCHEMA = "firstmate.completion-evidence.v1"
FIRSTMATE_EXECUTION_RESULT_SCHEMA = "firstmate.execution-event-result.v1"
FIRSTMATE_EXECUTION_SOURCE = "firstmate:execution"
MAX_FIRSTMATE_EXECUTION_EVENT_BYTES = 64 * 1024
MAX_COMPLETION_CHECKS = 32
MAX_COMPLETION_ARTIFACTS = 16
MAX_RECOVERY_WAKEUPS = 100
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_TASK_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_COMMIT_PATTERN = r"^[0-9a-f]{7,64}$"
_TERMINAL_PHASES = frozenset({"objective.completed", "objective.failed", "objective.cancelled"})
_PHASE_ACTIVITY = {
    "objective.accepted": (
        "objective.accepted", "active", "Objective was accepted for asynchronous execution."
    ),
    "worker.started": (
        "worker.started", "active", "Execution capacity started for the objective."
    ),
    "implementation.started": (
        "implementation.started", "active", "Implementation is underway."
    ),
    "tests.started": (
        "tests.started", "active", "Verification tests are running."
    ),
    "tests.passed": (
        "tests.passed", "completed", "Verification tests passed."
    ),
    "tests.failed": (
        "tests.failed", "failed", "Verification tests failed; the objective remains active."
    ),
    "review.started": (
        "review.started", "active", "Review is underway."
    ),
    "objective.completed": (
        "objective.completed", "completed", "Objective completed with structured verification evidence."
    ),
    "objective.failed": (
        "objective.failed", "failed", "Objective execution failed."
    ),
    "objective.cancelled": (
        "objective.cancelled", "cancelled", "Objective execution was cancelled."
    ),
}


class FirstmateExecutionConflict(RuntimeError):
    """An immutable event or objective identity was reused inconsistently."""


class FirstmateExecutionNotFound(LookupError):
    """An owner-scoped objective, event, or native-chat origin was not found."""


def _has_control_text(value: str) -> bool:
    return any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    )


def _validated_display_text(value: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or _has_control_text(value)
        or _contains_sensitive_text(value)
    ):
        raise ValueError("Execution facts contain invalid bounded display text.")
    return value


class _StrictExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def validate_unicode_scalars(cls, value):
        if isinstance(value, str) and (
            any(0xD800 <= ord(character) <= 0xDFFF for character in value)
            or _has_control_text(value)
        ):
            raise ValueError("Execution-event strings must contain inert Unicode scalar text.")
        return value


class FirstmateObjectiveFacts(_StrictExecutionContract):
    title: str = Field(min_length=1, max_length=240)
    project: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validated_display_text(value, maximum=240)

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str | None):
        return None if value is None else _validated_display_text(value, maximum=160)


class FirstmateNativeChatOrigin(_StrictExecutionContract):
    conversation_id: str = Field(
        min_length=8, max_length=128, pattern=r"^mgc_[A-Za-z0-9_-]+$"
    )
    user_message_id: str = Field(
        min_length=8, max_length=128, pattern=r"^mgm_[A-Za-z0-9_-]+$"
    )


class FirstmateVerificationCheck(_StrictExecutionContract):
    check_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    kind: Literal["acceptance", "test", "typecheck", "lint", "build", "review", "deployment"]
    label: str = Field(min_length=1, max_length=160)
    status: Literal["passed"]

    @field_validator("check_id")
    @classmethod
    def validate_check_id(cls, value: str) -> str:
        if _contains_sensitive_text(value):
            raise ValueError("Completion evidence contains an unsafe check identity.")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return _validated_display_text(value, maximum=160)


class FirstmatePullRequestArtifact(_StrictExecutionContract):
    kind: Literal["pull-request"]
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if safe_pull_request_url(value) != value or _contains_sensitive_text(value):
            raise ValueError("Completion evidence contains an unsafe pull-request URL.")
        return value


class FirstmateReportArtifact(_StrictExecutionContract):
    kind: Literal["report"]
    report_id: str = Field(
        min_length=1, max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    )

    @field_validator("report_id")
    @classmethod
    def validate_report_id(cls, value: str) -> str:
        if _contains_sensitive_text(value):
            raise ValueError("Completion evidence contains an unsafe report identity.")
        return value


class FirstmateCommitArtifact(_StrictExecutionContract):
    kind: Literal["commit"]
    commit_sha: str = Field(min_length=7, max_length=64, pattern=_COMMIT_PATTERN)


FirstmateCompletionArtifact = Annotated[
    Union[
        FirstmatePullRequestArtifact,
        FirstmateReportArtifact,
        FirstmateCommitArtifact,
    ],
    Field(discriminator="kind"),
]


class FirstmateCompletionEvidence(_StrictExecutionContract):
    schema_version: Literal["firstmate.completion-evidence.v1"]
    result: Literal["completed"]
    verification: Literal["verified"]
    checks: list[FirstmateVerificationCheck] = Field(
        min_length=1, max_length=MAX_COMPLETION_CHECKS
    )
    artifacts: list[FirstmateCompletionArtifact] = Field(
        default_factory=list, max_length=MAX_COMPLETION_ARTIFACTS
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> "FirstmateCompletionEvidence":
        check_ids = [check.check_id for check in self.checks]
        artifact_ids = [canonical_json(artifact.model_dump(mode="json")) for artifact in self.artifacts]
        public_ref_count = sum(
            isinstance(artifact, (FirstmatePullRequestArtifact, FirstmateReportArtifact))
            for artifact in self.artifacts
        )
        if (
            len(set(check_ids)) != len(check_ids)
            or len(set(artifact_ids)) != len(artifact_ids)
            or public_ref_count > 8
        ):
            raise ValueError("Completion evidence repeats or exceeds a public evidence identity.")
        encoded = canonical_json(self.model_dump(mode="json")).encode("utf-8")
        if len(encoded) > MAX_FIRSTMATE_EXECUTION_EVENT_BYTES:
            raise ValueError("Completion evidence exceeds its bounded contract.")
        return self


class _FirstmateExecutionEventBase(_StrictExecutionContract):
    schema_version: Literal["firstmate.execution-event.v1"]
    event_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    objective_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    task_id: str = Field(min_length=1, max_length=200, pattern=_TASK_PATTERN)
    run_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    occurred_at_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER)

    @model_validator(mode="after")
    def validate_event_size(self) -> "_FirstmateExecutionEventBase":
        encoded = canonical_json(self.model_dump(mode="json")).encode("utf-8")
        if len(encoded) > MAX_FIRSTMATE_EXECUTION_EVENT_BYTES:
            raise ValueError("The Firstmate execution event exceeds its bounded contract.")
        return self


class FirstmateObjectiveAcceptedEvent(_FirstmateExecutionEventBase):
    phase: Literal["objective.accepted"]
    objective: FirstmateObjectiveFacts
    chat: FirstmateNativeChatOrigin


class FirstmateProgressEvent(_FirstmateExecutionEventBase):
    phase: Literal[
        "worker.started",
        "implementation.started",
        "tests.started",
        "tests.passed",
        "tests.failed",
        "review.started",
    ]


class FirstmateObjectiveCompletedEvent(_FirstmateExecutionEventBase):
    phase: Literal["objective.completed"]
    evidence: FirstmateCompletionEvidence


class FirstmateObjectiveTerminalEvent(_FirstmateExecutionEventBase):
    phase: Literal["objective.failed", "objective.cancelled"]


FirstmateExecutionEventContract = Annotated[
    Union[
        FirstmateObjectiveAcceptedEvent,
        FirstmateProgressEvent,
        FirstmateObjectiveCompletedEvent,
        FirstmateObjectiveTerminalEvent,
    ],
    Field(discriminator="phase"),
]


class FirstmateCompletionWakeContract(_StrictExecutionContract):
    retry_failed: Literal[True]


def _now_ms() -> int:
    return int(time.time_ns() // 1_000_000)


def _connect() -> sqlite3.Connection:
    db.init_db()
    connection = sqlite3.connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _event_payload(event: _FirstmateExecutionEventBase) -> tuple[str, str]:
    payload = canonical_json(event.model_dump(mode="json"))
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _activity_refs(event: _FirstmateExecutionEventBase) -> list[dict[str, str]]:
    if not isinstance(event, FirstmateObjectiveCompletedEvent):
        return []
    refs: list[dict[str, str]] = []
    for artifact in event.evidence.artifacts:
        if isinstance(artifact, FirstmatePullRequestArtifact):
            refs.append({"kind": "pull-request", "url": artifact.url})
        elif isinstance(artifact, FirstmateReportArtifact):
            refs.append({"kind": "report", "id": artifact.report_id})
    return refs[:8]


def _public_event(row: sqlite3.Row, *, duplicate: bool) -> dict[str, Any]:
    generation = None
    if row["phase"] == "objective.completed":
        generation = {
            "state": row["generation_state"],
            "attempt": row["generation_attempt_count"],
            "assistant_message_id": row["assistant_message_id"],
            "error_code": row["error_code"],
        }
    return {
        "schema_version": FIRSTMATE_EXECUTION_RESULT_SCHEMA,
        "status": "duplicate" if duplicate else "accepted",
        "event": {
            "event_id": row["event_id"],
            "objective_id": row["objective_id"],
            "task_id": row["task_id"],
            "run_id": row["run_id"],
            "phase": row["phase"],
            "occurred_at_ms": row["occurred_at"],
        },
        "completion_message": generation,
    }


class FirstmateExecutionStore:
    """Immutable execution-event and objective-correlation persistence."""

    def ingest(
        self, owner_user_id: str, event: _FirstmateExecutionEventBase,
    ) -> dict[str, Any]:
        if (
            not isinstance(owner_user_id, str)
            or not owner_user_id
            or len(owner_user_id) > 128
            or _has_control_text(owner_user_id)
        ):
            raise ValueError("A bounded authenticated principal is required.")
        payload, payload_hash = _event_payload(event)
        evidence_json = (
            canonical_json(event.evidence.model_dump(mode="json"))
            if isinstance(event, FirstmateObjectiveCompletedEvent) else None
        )
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                """SELECT * FROM firstmate_execution_events
                   WHERE owner_user_id = ? AND event_id = ?""",
                (owner_user_id, event.event_id),
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["payload_sha256"] != payload_hash
                    or duplicate["payload_json"] != payload
                ):
                    raise FirstmateExecutionConflict(
                        "That Firstmate execution event id is already bound to different facts."
                    )
                connection.commit()
                return _public_event(duplicate, duplicate=True)

            objective = connection.execute(
                """SELECT * FROM firstmate_execution_objectives
                   WHERE owner_user_id = ? AND objective_id = ?""",
                (owner_user_id, event.objective_id),
            ).fetchone()
            if isinstance(event, FirstmateObjectiveAcceptedEvent):
                if objective is not None:
                    raise FirstmateExecutionConflict(
                        "That Firstmate objective already has a different accepted event."
                    )
                task_collision = connection.execute(
                    """SELECT objective_id FROM firstmate_execution_objectives
                       WHERE owner_user_id = ? AND task_id = ?""",
                    (owner_user_id, event.task_id),
                ).fetchone()
                if task_collision is not None:
                    raise FirstmateExecutionConflict(
                        "That Firstmate task is already correlated to another objective."
                    )
                origin = connection.execute(
                    """SELECT id FROM magi_messages
                       WHERE id = ? AND owner_user_id = ? AND conversation_id = ?
                         AND role = 'user' AND status = 'completed'""",
                    (
                        event.chat.user_message_id,
                        owner_user_id,
                        event.chat.conversation_id,
                    ),
                ).fetchone()
                if origin is None:
                    raise FirstmateExecutionNotFound(
                        "The owner-scoped native Magi objective origin was not found."
                    )
                now = _now_ms()
                connection.execute(
                    """INSERT INTO firstmate_execution_objectives
                       (owner_user_id, objective_id, task_id, run_id, accepted_event_id,
                        conversation_id, origin_message_id, title, project,
                        terminal_event_id, terminal_phase, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)""",
                    (
                        owner_user_id, event.objective_id, event.task_id, event.run_id,
                        event.event_id, event.chat.conversation_id,
                        event.chat.user_message_id, event.objective.title,
                        event.objective.project, now, now,
                    ),
                )
                objective = connection.execute(
                    """SELECT * FROM firstmate_execution_objectives
                       WHERE owner_user_id = ? AND objective_id = ?""",
                    (owner_user_id, event.objective_id),
                ).fetchone()
            else:
                if objective is None:
                    raise FirstmateExecutionNotFound(
                        "The owner-scoped Firstmate objective was not found."
                    )
                if objective["task_id"] != event.task_id or objective["run_id"] != event.run_id:
                    raise FirstmateExecutionConflict(
                        "Firstmate execution causality does not match the accepted objective."
                    )
                if objective["terminal_event_id"] is not None:
                    raise FirstmateExecutionConflict(
                        "The Firstmate objective already has a terminal execution event."
                    )

            assert objective is not None
            activity_kind, activity_state, summary = _PHASE_ACTIVITY[event.phase]
            projected = _upsert_activity(
                connection,
                user_id=owner_user_id,
                source_instance_id=FIRSTMATE_EXECUTION_SOURCE,
                source_event_id=event.event_id,
                record_key=f"execution:{event.event_id}",
                kind=activity_kind,
                state=activity_state,
                importance="routine",
                title=objective["title"],
                summary=summary,
                summary_truncated=False,
                source_payload_sha256=payload_hash,
                task_id=event.task_id,
                decision_key=None,
                objective_id=event.objective_id,
                run_id=event.run_id,
                project=objective["project"],
                occurred_at=event.occurred_at_ms,
                observed_at=_now_ms(),
                refs=_activity_refs(event),
            )
            now = _now_ms()
            generation_state = "queued" if event.phase == "objective.completed" else None
            connection.execute(
                """INSERT INTO firstmate_execution_events
                   (owner_user_id, event_id, objective_id, task_id, run_id, phase,
                    occurred_at, payload_sha256, payload_json, evidence_json, activity_record_id,
                    generation_state, generation_attempt_count, assistant_message_id,
                    error_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?)""",
                (
                    owner_user_id, event.event_id, event.objective_id, event.task_id,
                    event.run_id, event.phase, event.occurred_at_ms, payload_hash,
                    payload, evidence_json, projected["id"], generation_state, now, now,
                ),
            )
            if event.phase in _TERMINAL_PHASES:
                connection.execute(
                    """UPDATE firstmate_execution_objectives
                       SET terminal_event_id = ?, terminal_phase = ?, updated_at = ?
                       WHERE owner_user_id = ? AND objective_id = ?""",
                    (
                        event.event_id, event.phase, now,
                        owner_user_id, event.objective_id,
                    ),
                )
            stored = connection.execute(
                """SELECT * FROM firstmate_execution_events
                   WHERE owner_user_id = ? AND event_id = ?""",
                (owner_user_id, event.event_id),
            ).fetchone()
            connection.commit()
            return _public_event(stored, duplicate=False)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def inspect(self, owner_user_id: str, event_id: str) -> dict[str, Any]:
        with _connect() as connection:
            row = connection.execute(
                """SELECT * FROM firstmate_execution_events
                   WHERE owner_user_id = ? AND event_id = ?""",
                (owner_user_id, event_id),
            ).fetchone()
        if row is None:
            raise FirstmateExecutionNotFound("Firstmate execution event not found.")
        return _public_event(row, duplicate=True)

    def claim_completion(
        self, owner_user_id: str, event_id: str, *, retry_failed: bool,
    ) -> dict[str, Any] | None:
        if type(retry_failed) is not bool:
            raise ValueError("Completion retry state must be explicit.")
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT event.*, objective.conversation_id, objective.origin_message_id,
                          objective.title, objective.project
                   FROM firstmate_execution_events AS event
                   JOIN firstmate_execution_objectives AS objective
                     ON objective.owner_user_id = event.owner_user_id
                    AND objective.objective_id = event.objective_id
                   WHERE event.owner_user_id = ? AND event.event_id = ?""",
                (owner_user_id, event_id),
            ).fetchone()
            if row is None:
                raise FirstmateExecutionNotFound("Firstmate execution event not found.")
            if row["phase"] != "objective.completed" or not row["evidence_json"]:
                raise FirstmateExecutionConflict(
                    "Only a verified objective completion can wake a Magi completion message."
                )
            state = row["generation_state"]
            eligible = state == "queued" or (state == "failed" and retry_failed)
            if not eligible:
                connection.commit()
                return None
            updated = connection.execute(
                """UPDATE firstmate_execution_events
                   SET generation_state = 'generating',
                       generation_attempt_count = generation_attempt_count + 1,
                       error_code = NULL, updated_at = ?
                   WHERE owner_user_id = ? AND event_id = ? AND generation_state = ?""",
                (_now_ms(), owner_user_id, event_id, state),
            ).rowcount
            if updated != 1:
                connection.commit()
                return None
            claim = dict(row)
            claim["generation_attempt_count"] = int(row["generation_attempt_count"]) + 1
            connection.commit()
            return claim
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_completion(
        self,
        owner_user_id: str,
        event_id: str,
        *,
        state: Literal["completed", "failed"],
        assistant_message_id: str | None,
        error_code: str | None = None,
    ) -> None:
        if state == "completed":
            if not isinstance(assistant_message_id, str) or not assistant_message_id:
                raise ValueError("A completed generation requires its canonical assistant identity.")
            error_code = None
        else:
            assistant_message_id = assistant_message_id if isinstance(assistant_message_id, str) else None
            error_code = error_code if error_code in {"magi_generation_failed"} else "magi_generation_failed"
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM firstmate_execution_events
                   WHERE owner_user_id = ? AND event_id = ?""",
                (owner_user_id, event_id),
            ).fetchone()
            if row is None:
                raise FirstmateExecutionNotFound("Firstmate execution event not found.")
            if row["generation_state"] == "completed":
                if state != "completed" or row["assistant_message_id"] != assistant_message_id:
                    raise FirstmateExecutionConflict("Completion-message state cannot regress.")
                connection.commit()
                return
            if row["generation_state"] != "generating":
                raise FirstmateExecutionConflict("Completion-message generation was not claimed.")
            if assistant_message_id is not None:
                assistant = connection.execute(
                    """SELECT message.id FROM magi_generated_messages AS generated
                       JOIN magi_messages AS message ON message.id = generated.message_id
                       WHERE generated.owner_user_id = ? AND generated.generation_key = ?
                         AND generated.message_id = ? AND message.owner_user_id = ?
                         AND message.role = 'assistant'""",
                    (owner_user_id, event_id, assistant_message_id, owner_user_id),
                ).fetchone()
                if assistant is None:
                    raise FirstmateExecutionNotFound(
                        "Generated native Magi assistant message not found."
                    )
            connection.execute(
                """UPDATE firstmate_execution_events
                   SET generation_state = ?, assistant_message_id = ?, error_code = ?, updated_at = ?
                   WHERE owner_user_id = ? AND event_id = ?""",
                (
                    state, assistant_message_id, error_code, _now_ms(),
                    owner_user_id, event_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def recover_interrupted(self) -> int:
        """Requeue process-local generation claims after native pending recovery."""
        with _connect() as connection:
            result = connection.execute(
                """UPDATE firstmate_execution_events
                   SET generation_state = 'queued', error_code = NULL, updated_at = ?
                   WHERE generation_state = 'generating'""",
                (_now_ms(),),
            )
        return result.rowcount

    def queued_completions(self, *, limit: int = MAX_RECOVERY_WAKEUPS) -> list[tuple[str, str]]:
        if not 1 <= limit <= MAX_RECOVERY_WAKEUPS:
            raise ValueError("Completion recovery limit is invalid.")
        with _connect() as connection:
            rows = connection.execute(
                """SELECT owner_user_id, event_id FROM firstmate_execution_events
                   WHERE generation_state = 'queued'
                   ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
        return [(row["owner_user_id"], row["event_id"]) for row in rows]


class FirstmateExecutionService:
    """Project events to Activity and wake bounded native completion reports."""

    def __init__(
        self,
        magi_chat: MagiChatService,
        *,
        store: FirstmateExecutionStore | None = None,
    ) -> None:
        self.magi_chat = magi_chat
        self.store = store or FirstmateExecutionStore()
        self._generation_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._recovery_task: asyncio.Task[None] | None = None

    @staticmethod
    def _outcome_facts(claim: dict[str, Any]) -> MagiVerifiedOutcome:
        evidence = FirstmateCompletionEvidence.model_validate_json(claim["evidence_json"])
        checks = tuple(MagiOutcomeCheck(
            check_id=check.check_id,
            kind=check.kind,
            label=check.label,
            status=check.status,
        ) for check in evidence.checks)
        artifacts: list[MagiOutcomeArtifact] = []
        for artifact in evidence.artifacts:
            if isinstance(artifact, FirstmatePullRequestArtifact):
                artifacts.append(MagiOutcomeArtifact(kind="pull-request", value=artifact.url))
            elif isinstance(artifact, FirstmateReportArtifact):
                artifacts.append(MagiOutcomeArtifact(kind="report", value=artifact.report_id))
            elif isinstance(artifact, FirstmateCommitArtifact):
                artifacts.append(MagiOutcomeArtifact(kind="commit", value=artifact.commit_sha))
        return MagiVerifiedOutcome(
            title=claim["title"],
            project=claim["project"],
            completed_at_ms=claim["occurred_at"],
            checks=checks,
            artifacts=tuple(artifacts),
        )

    async def _generate_completion(
        self, owner_user_id: str, event_id: str, claim: dict[str, Any],
    ) -> None:
        assistant_message_id: str | None = None
        try:
            result = await self.magi_chat.generate_verified_outcome(
                owner_user_id,
                event_id,
                self._outcome_facts(claim),
                conversation_id=claim["conversation_id"],
                reply_to_message_id=claim["origin_message_id"],
                retry_failed=claim["generation_attempt_count"] > 1,
            )
            message = result.get("assistant_message")
            assistant_message_id = message.get("id") if isinstance(message, dict) else None
            if result.get("status") == "completed" and assistant_message_id:
                await asyncio.to_thread(
                    self.store.finish_completion,
                    owner_user_id,
                    event_id,
                    state="completed",
                    assistant_message_id=assistant_message_id,
                )
            else:
                await asyncio.to_thread(
                    self.store.finish_completion,
                    owner_user_id,
                    event_id,
                    state="failed",
                    assistant_message_id=assistant_message_id,
                    error_code="magi_generation_failed",
                )
        except asyncio.CancelledError:
            # The durable generating claim is requeued on process startup. A
            # caller disconnect does not cancel this task because wake() shields it.
            raise
        except Exception:
            try:
                await asyncio.to_thread(
                    self.store.finish_completion,
                    owner_user_id,
                    event_id,
                    state="failed",
                    assistant_message_id=assistant_message_id,
                    error_code="magi_generation_failed",
                )
            except (FirstmateExecutionConflict, FirstmateExecutionNotFound, ValueError):
                pass
        finally:
            current = asyncio.current_task()
            key = (owner_user_id, event_id)
            if self._generation_tasks.get(key) is current:
                self._generation_tasks.pop(key, None)

    async def wake(
        self,
        owner_user_id: str,
        event_id: str,
        *,
        retry_failed: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        claim = await asyncio.to_thread(
            self.store.claim_completion,
            owner_user_id,
            event_id,
            retry_failed=retry_failed,
        )
        key = (owner_user_id, event_id)
        if claim is not None:
            task = asyncio.create_task(
                self._generate_completion(owner_user_id, event_id, claim)
            )
            self._generation_tasks[key] = task
        else:
            task = self._generation_tasks.get(key)
        if wait and task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
        return await asyncio.to_thread(self.store.inspect, owner_user_id, event_id)

    async def ingest(
        self, owner_user_id: str, event: _FirstmateExecutionEventBase,
    ) -> dict[str, Any]:
        stored = await asyncio.to_thread(self.store.ingest, owner_user_id, event)
        if event.phase == "objective.completed":
            woken = await self.wake(owner_user_id, event.event_id)
            # Preserve whether this call accepted the immutable source event;
            # inspect() intentionally reports later wake/read calls as replay.
            return {**woken, "status": stored["status"]}
        return stored

    async def _drain_recovery(
        self, initial_tasks: list[asyncio.Task[None]],
    ) -> None:
        """Drain every durable wake with bounded provider concurrency."""
        tasks = initial_tasks
        try:
            while tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                queued = await asyncio.to_thread(self.store.queued_completions)
                tasks = []
                for owner_user_id, event_id in queued:
                    await self.wake(owner_user_id, event_id, wait=False)
                    task = self._generation_tasks.get((owner_user_id, event_id))
                    if task is not None:
                        tasks.append(task)
        finally:
            if self._recovery_task is asyncio.current_task():
                self._recovery_task = None

    async def recover_pending(self) -> int:
        """Requeue interrupted claims and start a bounded durable queue drain."""
        if self._recovery_task is not None and not self._recovery_task.done():
            return 0
        recovered = await asyncio.to_thread(self.store.recover_interrupted)
        queued = await asyncio.to_thread(self.store.queued_completions)
        tasks: list[asyncio.Task[None]] = []
        for owner_user_id, event_id in queued:
            await self.wake(owner_user_id, event_id, wait=False)
            task = self._generation_tasks.get((owner_user_id, event_id))
            if task is not None:
                tasks.append(task)
        if tasks:
            self._recovery_task = asyncio.create_task(self._drain_recovery(tasks))
        return recovered + len(queued)
