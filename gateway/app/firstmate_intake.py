"""Bounded writes to Firstmate's existing durable supervisor inbox.

The inbox is not a second scheduler: ``fm-inbox.sh note`` stores one note and
appends Firstmate's native ``check`` wake.  Firstmate remains the sole owner of
capacity, task intake, worker spawning, cancellation, and restart recovery.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import stat as stat_module
import sqlite3
import time
from typing import Any

from app import db
from app.firstmate_client import FirstmateClient
from app.firstmate_producer import ProducerContractError

MAX_INBOX_OUTPUT_BYTES = 16 * 1024
MAX_INBOX_NOTE_CHARS = 500
INBOX_TIMEOUT_SECONDS = 10.0
MAX_STARTUP_INTAKE_RETRIES = 8
MAX_STARTUP_INTAKE_RECORDS = 100


class FirstmateIntakeError(RuntimeError):
    pass


class _OutputTooLarge(RuntimeError):
    pass


async def _read_bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(min(8192, maximum + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > maximum:
            raise _OutputTooLarge


class FirstmateSupervisorInbox:
    def __init__(
        self,
        firstmate: FirstmateClient | None = None,
        *,
        timeout_seconds: float = INBOX_TIMEOUT_SECONDS,
    ) -> None:
        self.firstmate = firstmate or FirstmateClient()
        self.timeout_seconds = timeout_seconds
        self.command = Path(self.firstmate.fm_root) / "bin" / "fm-inbox.sh"

    def _environment(self) -> dict[str, str]:
        if self.firstmate.fm_root_is_explicit:
            try:
                self.firstmate.validate_producer_contract()
            except ProducerContractError as exc:
                raise FirstmateIntakeError("untrusted-command") from exc
        else:
            production = os.getenv("MAGISTRATE_ENV", "").strip().lower() not in {
                "dev", "development", "test", "testing",
            }
            if production:
                raise FirstmateIntakeError("unpinned-command")
            try:
                info = os.lstat(self.command)
            except OSError as exc:
                raise FirstmateIntakeError("missing-command") from exc
            if (
                not stat_module.S_ISREG(info.st_mode)
                or stat_module.S_ISLNK(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_mode & stat_module.S_IWOTH
                or not os.access(self.command, os.X_OK)
            ):
                raise FirstmateIntakeError("untrusted-command")
        tool_path = self.firstmate.get_trusted_tool_path()
        runtime_home = self.firstmate.get_trusted_runtime_home()
        if tool_path is None or runtime_home is None:
            raise FirstmateIntakeError("runtime-unavailable")
        return {
            "FM_HOME": self.firstmate.fm_home,
            "FM_ROOT_OVERRIDE": self.firstmate.fm_root,
            "PATH": tool_path,
            "HOME": runtime_home,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
        }

    async def note(self, text: str) -> None:
        if (
            not isinstance(text, str) or not text.strip() or text != text.strip()
            or len(text) > MAX_INBOX_NOTE_CHARS or "\x00" in text
            or any(ord(character) < 32 for character in text)
        ):
            raise FirstmateIntakeError("invalid-note")
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.command), "note", text,
                cwd=self.firstmate.fm_home,
                env=self._environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr, returncode = await asyncio.wait_for(
                    asyncio.gather(
                        _read_bounded(process.stdout, MAX_INBOX_OUTPUT_BYTES),
                        _read_bounded(process.stderr, MAX_INBOX_OUTPUT_BYTES),
                        process.wait(),
                    ),
                    timeout=self.timeout_seconds,
                )
            except (asyncio.TimeoutError, _OutputTooLarge) as exc:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                raise FirstmateIntakeError("runtime-bound-exceeded") from exc
            if returncode != 0 or stderr or not stdout.startswith(b"queued "):
                raise FirstmateIntakeError("note-refused")
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        except FirstmateIntakeError:
            raise
        except (OSError, ValueError) as exc:
            raise FirstmateIntakeError("runtime-unavailable") from exc


async def reconcile_pending_objective_intake(
    dispatcher: Any | None = None,
    *,
    maximum: int = MAX_STARTUP_INTAKE_RECORDS,
    updated_before_ms: int | None = None,
) -> dict[str, int]:
    """Retry interrupted objective publication once during Gateway startup.

    The deterministic task identity makes ``tasks-axi add`` idempotent.  A
    crash before the acceptance commit can therefore replay queue publication
    and the native Firstmate inbox wake without creating another task/worker.
    This is an explicit startup recovery seam, never a read-path or timer.
    """
    # Runtime imports avoid a module cycle: objective tools use this module's
    # bounded inbox adapter for their normal dispatch path.
    from app.magi_firstmate_tools import (
        FirstmateSubmitObjectiveContract,
        ObjectiveClaim,
        ObjectiveDispatchError,
        ObjectiveSubmissionStore,
        TasksAxiObjectiveDispatcher,
        _task_body,
        _task_title,
    )

    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 500:
        raise ValueError("maximum must be between 1 and 500")
    cutoff = int(time.time() * 1000) + 1 if updated_before_ms is None else updated_before_ms
    if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff < 0:
        raise ValueError("updated_before_ms must be a non-negative integer")
    db.init_db()
    with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM magi_objective_submissions
               WHERE status IN ('submitting', 'failed') AND attempt_count < ?
                 AND updated_at < ?
               ORDER BY created_at, objective_id LIMIT ?""",
            (MAX_STARTUP_INTAKE_RETRIES, cutoff, maximum),
        ).fetchall()

    counts = {"examined": len(rows), "recovered": 0, "failed": 0}
    if not rows:
        return counts
    objective_dispatcher = dispatcher or TasksAxiObjectiveDispatcher()
    store = ObjectiveSubmissionStore()
    for row in rows:
        try:
            contract = FirstmateSubmitObjectiveContract.model_validate_json(row["contract_json"])
        except (ValueError, TypeError):
            with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
                connection.execute(
                    """UPDATE magi_objective_submissions
                       SET status = 'failed', attempt_count = ?,
                           last_error_code = 'objective_contract_invalid', updated_at = ?
                       WHERE objective_id = ? AND owner_user_id = ?
                         AND status IN ('submitting', 'failed')""",
                    (
                        MAX_STARTUP_INTAKE_RETRIES, int(time.time() * 1000),
                        row["objective_id"], row["owner_user_id"],
                    ),
                )
            counts["failed"] += 1
            continue

        attempt = int(row["attempt_count"]) + 1
        with sqlite3.connect(db.DB_PATH, timeout=10) as connection:
            changed = connection.execute(
                """UPDATE magi_objective_submissions
                   SET status = 'submitting', attempt_count = ?, last_error_code = NULL,
                       updated_at = ?
                   WHERE objective_id = ? AND owner_user_id = ?
                     AND status IN ('submitting', 'failed') AND attempt_count = ?""",
                (
                    attempt, int(time.time() * 1000), row["objective_id"],
                    row["owner_user_id"], int(row["attempt_count"]),
                ),
            ).rowcount
        if changed != 1:
            continue
        claim = ObjectiveClaim(
            objective_id=str(row["objective_id"]),
            task_id=str(row["task_id"]),
            contract_json=str(row["contract_json"]),
            contract_sha256=str(row["contract_sha256"]),
            accepted=False,
            duplicate=True,
            attempt=attempt,
        )
        try:
            await objective_dispatcher.submit(
                task_id=claim.task_id,
                title=_task_title(contract),
                project=contract.project,
                body=_task_body(claim.objective_id, claim.contract_json),
            )
            store.accept(str(row["owner_user_id"]), claim)
        except ObjectiveDispatchError as exc:
            reason = str(exc)
            store.fail(
                str(row["owner_user_id"]), claim,
                f"objective_dispatch_{reason}" if reason else "objective_dispatch_failed",
            )
            counts["failed"] += 1
        else:
            counts["recovered"] += 1
    return counts
