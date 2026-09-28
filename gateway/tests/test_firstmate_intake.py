import sqlite3

import pytest

from app import db
from app.firstmate_intake import reconcile_pending_objective_intake
from app.magi_firstmate_tools import (
    FirstmateSubmitObjectiveContract,
    ObjectiveDispatchError,
    ObjectiveDispatchReceipt,
    ObjectiveSubmissionStore,
)
from app.magi_tool_protocol import MagiToolContext, MagiToolError


def _pending_claim(suffix: str):
    contract = FirstmateSubmitObjectiveContract(
        objective=f"Recover durable objective {suffix}",
        project="Magistrate",
        constraints=[],
        acceptance_criteria=["One deterministic task is eventually accepted"],
        context_refs=[],
    )
    context = MagiToolContext(
        owner_user_id=f"intake-owner-{suffix}",
        conversation_id=f"mgc_{suffix * 8}",
        turn_id=f"mgt_{suffix * 8}",
        user_message_id=f"mgm_{suffix * 8}u",
        assistant_message_id=f"mgm_{suffix * 8}a",
        command_authorized=True,
    )
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT OR IGNORE INTO user_profiles
               (user_id,name,email,created_at,updated_at) VALUES (?, '', '', 1, 1)""",
            (context.owner_user_id,),
        )
    claim = ObjectiveSubmissionStore().claim(
        context=context,
        invocation_key=(suffix.encode().hex() * 64)[:64],
        contract=contract,
    )
    return claim, contract, context


def test_fresh_submission_claim_is_leased_against_concurrent_duplicate_dispatch():
    claim, contract, context = _pending_claim("f")
    with pytest.raises(MagiToolError) as duplicate:
        ObjectiveSubmissionStore().claim(
            context=context,
            invocation_key=("f".encode().hex() * 64)[:64],
            contract=contract,
        )
    assert duplicate.value.code == "objective_submission_in_progress"
    assert duplicate.value.retryable is True
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM magi_objective_submissions WHERE objective_id = ?",
            (claim.objective_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_startup_cutoff_does_not_claim_a_fresh_in_process_submission():
    claim, _, context = _pending_claim("c")
    with sqlite3.connect(db.DB_PATH) as connection:
        updated_at = connection.execute(
            "SELECT updated_at FROM magi_objective_submissions "
            "WHERE owner_user_id = ? AND objective_id = ?",
            (context.owner_user_id, claim.objective_id),
        ).fetchone()[0]
    dispatcher = RecoveringDispatcher()

    await reconcile_pending_objective_intake(
        dispatcher, updated_before_ms=updated_at,
    )
    assert claim.task_id not in [call["task_id"] for call in dispatcher.calls]

    recovered = await reconcile_pending_objective_intake(
        dispatcher, updated_before_ms=updated_at + 1,
    )
    assert recovered["recovered"] >= 1
    assert [call["task_id"] for call in dispatcher.calls].count(claim.task_id) == 1


class RecoveringDispatcher:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    async def submit(self, **arguments):
        self.calls.append(arguments)
        if len(self.calls) <= self.failures:
            raise ObjectiveDispatchError("intake-wake-failed")
        return ObjectiveDispatchReceipt(already_present=len(self.calls) > 1)


@pytest.mark.asyncio
async def test_startup_replays_crash_window_once_with_deterministic_task_identity():
    claim, contract, context = _pending_claim("d")
    dispatcher = RecoveringDispatcher()

    recovered = await reconcile_pending_objective_intake(dispatcher)
    assert recovered["recovered"] >= 1
    calls = [call for call in dispatcher.calls if call["task_id"] == claim.task_id]
    assert len(calls) == 1
    assert calls[0]["project"] == contract.project

    with sqlite3.connect(db.DB_PATH) as connection:
        row = connection.execute(
            "SELECT status, attempt_count, accepted_at FROM magi_objective_submissions "
            "WHERE owner_user_id = ? AND objective_id = ?",
            (context.owner_user_id, claim.objective_id),
        ).fetchone()
    assert row[0] == "accepted"
    assert row[1] == 2
    assert row[2] is not None

    again = await reconcile_pending_objective_intake(dispatcher)
    assert again["examined"] == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_wake_recovers_after_restart_without_duplicate_objective():
    claim, _, context = _pending_claim("e")
    first_boot = RecoveringDispatcher(failures=1)
    failed = await reconcile_pending_objective_intake(first_boot)
    assert failed["failed"] >= 1

    with sqlite3.connect(db.DB_PATH) as connection:
        row = connection.execute(
            "SELECT status, last_error_code FROM magi_objective_submissions "
            "WHERE owner_user_id = ? AND objective_id = ?",
            (context.owner_user_id, claim.objective_id),
        ).fetchone()
    assert row == ("failed", "objective_dispatch_intake-wake-failed")

    next_boot = RecoveringDispatcher()
    recovered = await reconcile_pending_objective_intake(next_boot)
    assert recovered["recovered"] >= 1
    assert [call["task_id"] for call in first_boot.calls + next_boot.calls].count(claim.task_id) == 2
    with sqlite3.connect(db.DB_PATH) as connection:
        rows = connection.execute(
            "SELECT status FROM magi_objective_submissions "
            "WHERE owner_user_id = ? AND objective_id = ?",
            (context.owner_user_id, claim.objective_id),
        ).fetchall()
    assert rows == [("accepted",)]
