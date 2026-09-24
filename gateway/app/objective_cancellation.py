"""Safe, idempotent cancellation requests for structured objectives.

A request is a durable hand-off to Firstmate's own supervisor inbox.  It never
signals a process, sends terminal keys, or claims cancellation before a pushed
``objective.cancelled`` execution event is observed.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from typing import Any

from app import db
from app.firstmate_intake import FirstmateIntakeError, FirstmateSupervisorInbox

_SAFE_OBJECTIVE_ID = re.compile(r"^mgo_[0-9a-f]{32}$")
_SAFE_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_CANCELLATION_DELIVERY_ATTEMPTS = 8


class ObjectiveCancellationError(RuntimeError):
    def __init__(self, code: str, detail: str, status_code: int):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class ObjectiveCancellationService:
    def __init__(self, inbox: FirstmateSupervisorInbox | None = None) -> None:
        self.inbox = inbox or FirstmateSupervisorInbox()

    @staticmethod
    def _public(row: sqlite3.Row, *, duplicate: bool) -> dict[str, Any]:
        return {
            "schema_version": "objective-cancellation-request.v1",
            "request_id": row["request_id"],
            "objective_id": row["objective_id"],
            "status": row["status"],
            "requested_at": row["created_at"],
            "duplicate": duplicate,
        }

    async def _notify(self, row: sqlite3.Row, *, duplicate: bool) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with sqlite3.connect(db.DB_PATH, timeout=10) as delivery_connection:
            delivery_connection.row_factory = sqlite3.Row
            delivery_connection.execute(
                """UPDATE objective_cancellation_requests
                   SET notification_status = 'pending', error_code = NULL,
                       notification_attempt_count = notification_attempt_count + 1,
                       updated_at = ?
                   WHERE request_id = ? AND status = 'requested'""",
                (now, row["request_id"]),
            )
            current = delivery_connection.execute(
                "SELECT * FROM objective_cancellation_requests WHERE request_id = ?",
                (row["request_id"],),
            ).fetchone()
        if current is None:
            raise ObjectiveCancellationError(
                "store_unavailable", "Cancellation could not be recorded.", 503,
            )
        if current["status"] != "requested":
            return self._public(current, duplicate=duplicate)
        try:
            await self.inbox.note(
                f"Cancellation request {current['request_id']} targets authenticated Magi "
                f"objective {current['task_id']}. Apply Firstmate's normal cancellation "
                "policy and publish objective.cancelled only after cancellation is observed."
            )
        except FirstmateIntakeError as exc:
            with sqlite3.connect(db.DB_PATH) as failed_connection:
                failed_connection.execute(
                    """UPDATE objective_cancellation_requests
                       SET error_code = 'intake_unavailable',
                           notification_status = 'failed', updated_at = ?
                       WHERE request_id = ? AND status = 'requested'""",
                    (int(time.time() * 1000), current["request_id"]),
                )
            raise ObjectiveCancellationError(
                "intake_unavailable",
                "Cancellation was recorded but Firstmate could not be notified. Retry safely.",
                503,
            ) from exc
        delivered_at = int(time.time() * 1000)
        with sqlite3.connect(db.DB_PATH, timeout=10) as delivered_connection:
            delivered_connection.row_factory = sqlite3.Row
            delivered_connection.execute(
                """UPDATE objective_cancellation_requests
                   SET notification_status = 'delivered', notified_at = ?, updated_at = ?
                   WHERE request_id = ?""",
                (delivered_at, delivered_at, current["request_id"]),
            )
            delivered = delivered_connection.execute(
                "SELECT * FROM objective_cancellation_requests WHERE request_id = ?",
                (current["request_id"],),
            ).fetchone()
        return self._public(delivered, duplicate=duplicate)

    async def recover_pending(
        self, *, maximum: int = 100, updated_before_ms: int | None = None,
    ) -> dict[str, int]:
        """Replay only known-undelivered requests during explicit startup recovery."""
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 500:
            raise ValueError("maximum must be between 1 and 500")
        cutoff = int(time.time() * 1000) + 1 if updated_before_ms is None else updated_before_ms
        if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff < 0:
            raise ValueError("updated_before_ms must be a non-negative integer")
        db.init_db()
        with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT request.*,
                          execution.terminal_phase
                   FROM objective_cancellation_requests AS request
                   LEFT JOIN firstmate_execution_objectives AS execution
                     ON execution.owner_user_id = request.owner_user_id
                    AND execution.objective_id = request.objective_id
                   WHERE request.notification_status IN ('pending','failed')
                     AND request.notification_attempt_count < ?
                     AND request.updated_at < ?
                   ORDER BY request.created_at, request.request_id LIMIT ?""",
                (MAX_CANCELLATION_DELIVERY_ATTEMPTS, cutoff, maximum),
            ).fetchall()
        counts = {"examined": len(rows), "recovered": 0, "failed": 0, "terminal": 0}
        for row in rows:
            if row["terminal_phase"] is not None:
                now = int(time.time() * 1000)
                observed = row["terminal_phase"] == "objective.cancelled"
                with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
                    connection.execute(
                        """UPDATE objective_cancellation_requests
                           SET status = ?, error_code = ?, notification_status = 'delivered',
                               updated_at = ? WHERE request_id = ?""",
                        (
                            "observed" if observed else "failed",
                            None if observed else "objective_terminal",
                            now, row["request_id"],
                        ),
                    )
                counts["terminal"] += 1
                continue
            with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute(
                    """UPDATE objective_cancellation_requests
                       SET status = 'requested', error_code = NULL
                       WHERE request_id = ? AND status = 'failed'""",
                    (row["request_id"],),
                )
                retry = connection.execute(
                    "SELECT * FROM objective_cancellation_requests WHERE request_id = ?",
                    (row["request_id"],),
                ).fetchone()
            try:
                await self._notify(retry, duplicate=True)
            except ObjectiveCancellationError:
                counts["failed"] += 1
            else:
                counts["recovered"] += 1
        return counts

    async def request(
        self,
        *,
        owner_user_id: str,
        actor_session_id: str,
        objective_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not _SAFE_OBJECTIVE_ID.fullmatch(objective_id or ""):
            raise ObjectiveCancellationError("invalid_target", "That objective is unavailable.", 404)
        if not _SAFE_IDEMPOTENCY.fullmatch(idempotency_key or ""):
            raise ObjectiveCancellationError(
                "invalid_idempotency", "A valid cancellation request key is required.", 422,
            )
        db.init_db()
        now = int(time.time() * 1000)
        connection = sqlite3.connect(db.DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM objective_cancellation_requests
                   WHERE owner_user_id = ? AND idempotency_key = ?""",
                (owner_user_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["objective_id"] != objective_id:
                    raise ObjectiveCancellationError(
                        "idempotency_conflict",
                        "That cancellation request key belongs to another objective.", 409,
                    )
                if existing["status"] == "failed":
                    terminal = connection.execute(
                        """SELECT terminal_phase FROM firstmate_execution_objectives
                           WHERE owner_user_id = ? AND objective_id = ?""",
                        (owner_user_id, objective_id),
                    ).fetchone()
                    if terminal is not None and terminal[0] is not None:
                        raise ObjectiveCancellationError(
                            "already_terminal", "That objective has already finished.", 409,
                        )
                    # Compatibility for an undelivered row written before
                    # notification state became independent of request state.
                    connection.execute(
                        """UPDATE objective_cancellation_requests
                           SET status = 'requested', error_code = NULL,
                               notification_status = 'failed', updated_at = ?
                           WHERE request_id = ? AND status = 'failed'""",
                        (now, existing["request_id"]),
                    )
                    existing = connection.execute(
                        "SELECT * FROM objective_cancellation_requests WHERE request_id = ?",
                        (existing["request_id"],),
                    ).fetchone()
                connection.commit()
                if existing["status"] == "requested" and existing["notification_status"] == "failed":
                    return await self._notify(existing, duplicate=True)
                return self._public(existing, duplicate=True)
            active = connection.execute(
                """SELECT * FROM objective_cancellation_requests
                   WHERE owner_user_id = ? AND objective_id = ? AND status = 'requested'""",
                (owner_user_id, objective_id),
            ).fetchone()
            if active is not None:
                connection.commit()
                return self._public(active, duplicate=True)
            target = connection.execute(
                """SELECT submission.task_id, execution.terminal_phase
                   FROM magi_objective_submissions AS submission
                   LEFT JOIN firstmate_execution_objectives AS execution
                     ON execution.owner_user_id = submission.owner_user_id
                    AND (execution.objective_id = submission.objective_id
                         OR execution.task_id = submission.task_id)
                   WHERE submission.owner_user_id = ? AND submission.objective_id = ?""",
                (owner_user_id, objective_id),
            ).fetchone()
            if target is None:
                # A structured producer may have accepted an objective that
                # predates the submission ledger; preserve owner qualification.
                target = connection.execute(
                    """SELECT task_id, terminal_phase
                       FROM firstmate_execution_objectives
                       WHERE owner_user_id = ? AND objective_id = ?""",
                    (owner_user_id, objective_id),
                ).fetchone()
            if target is None:
                raise ObjectiveCancellationError("not_found", "That objective is unavailable.", 404)
            if target["terminal_phase"] is not None:
                raise ObjectiveCancellationError(
                    "already_terminal", "That objective has already finished.", 409,
                )
            task_id = str(target["task_id"])
            request_id = "ocr_" + hashlib.sha256(
                f"objective-cancel-v1\0{owner_user_id}\0{idempotency_key}".encode("utf-8")
            ).hexdigest()[:32]
            connection.execute(
                """INSERT INTO objective_cancellation_requests
                   (request_id, owner_user_id, objective_id, task_id,
                    actor_session_id, idempotency_key, status, notification_status,
                    notification_attempt_count, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,'requested','pending',0,?,?)""",
                (
                    request_id, owner_user_id, objective_id, task_id,
                    actor_session_id, idempotency_key, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM objective_cancellation_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            connection.commit()
        except ObjectiveCancellationError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ObjectiveCancellationError(
                "request_conflict", "A cancellation request is already pending.", 409,
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise ObjectiveCancellationError(
                "store_unavailable", "Cancellation could not be recorded.", 503,
            ) from exc
        finally:
            connection.close()

        return await self._notify(row, duplicate=False)


objective_cancellation_service = ObjectiveCancellationService()
