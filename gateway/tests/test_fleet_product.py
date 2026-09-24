import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from app import db
from app.main import app
from app.objective_cancellation import objective_cancellation_service
from conftest import TEST_HEADERS


client = TestClient(app)


def _seed_submission(owner="default_user", suffix="a"):
    objective_id = f"mgo_{suffix * 32}"
    task_id = f"magi-{suffix * 32}"
    now = int(time.time() * 1000)
    objective = (
        "Build a polished customer-facing Fleet detail experience with durable status, "
        "artifacts, decisions, and a cancellation request that never exposes terminal internals."
    )
    contract = json.dumps({
        "objective": objective, "project": "Magistrate", "constraints": [],
        "acceptance_criteria": ["Fleet is product safe"], "context_refs": [],
    }, separators=(",", ":"), sort_keys=True)
    db.init_db()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.execute(
            """INSERT OR REPLACE INTO magi_objective_submissions
               (objective_id, task_id, owner_user_id, invocation_key, conversation_id,
                turn_id, user_message_id, assistant_message_id, contract_json,
                contract_sha256, display_title, status, attempt_count, accepted_at,
                created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'accepted',1,?,?,?)""",
            (
                objective_id, task_id, owner, suffix * 64,
                f"mgc_{suffix * 8}", f"mgt_{suffix * 8}", f"mgm_{suffix * 8}u",
                f"mgm_{suffix * 8}a", contract, suffix * 64, now, now, now,
            ),
        )
    return objective_id, task_id, objective


def test_fleet_uses_stable_concise_titles_and_product_detail_contract():
    objective_id, task_id, objective = _seed_submission(suffix="b")
    payload = client.get("/api/v1/fleet", headers=TEST_HEADERS).json()
    item = next(row for row in payload["tasks"] if row["objective_id"] == objective_id)
    assert item["title"].endswith("…") and len(item["title"]) <= 72
    assert item["goal"] == objective
    assert item["status"] == "queued"
    assert item["activity"] == [{
        "phase": "objective.accepted", "label": "Objective queued", "occurred_at": None,
    }]
    assert item["workers"] == []
    assert item["artifacts"] == []
    assert item["decisions"] == []
    assert item["cancellation"] == {"state": "none", "requested_at": None, "allowed": True}
    # Product Fleet omits scheduler/run, pane, terminal, transcript, and process identities.
    assert task_id not in json.dumps(item)
    assert all(word not in item for word in (
        "id", "task_id", "run_id", "pane_id", "terminal_output", "pid", "transcript",
    ))


def test_cancellation_is_confirmed_durable_idempotent_supervisor_handoff(monkeypatch):
    objective_id, task_id, _ = _seed_submission(suffix="c")

    class Inbox:
        def __init__(self):
            self.notes = []

        async def note(self, text):
            self.notes.append(text)

    inbox = Inbox()
    monkeypatch.setattr(objective_cancellation_service, "inbox", inbox)
    body = {"idempotency_key": "cancel-product-objective-c"}
    first = client.post(
        f"/api/v1/fleet/objectives/{objective_id}/cancellation-requests",
        headers=TEST_HEADERS, json=body,
    )
    assert first.status_code == 202
    assert first.json()["status"] == "requested"
    assert first.json()["duplicate"] is False
    assert inbox.notes == [
        f"Cancellation request {first.json()['request_id']} targets authenticated Magi "
        f"objective {task_id}. Apply Firstmate's normal cancellation policy and publish "
        "objective.cancelled only after cancellation is observed."
    ]

    repeated = client.post(
        f"/api/v1/fleet/objectives/{objective_id}/cancellation-requests",
        headers=TEST_HEADERS, json=body,
    )
    assert repeated.status_code == 202
    assert repeated.json()["request_id"] == first.json()["request_id"]
    assert repeated.json()["duplicate"] is True
    assert len(inbox.notes) == 1

    item = next(
        row for row in client.get("/api/v1/fleet", headers=TEST_HEADERS).json()["tasks"]
        if row["objective_id"] == objective_id
    )
    assert item["cancellation"]["state"] == "requested"
    assert item["cancellation"]["allowed"] is False


def test_failed_cancellation_delivery_retries_same_durable_request(monkeypatch):
    objective_id, _, _ = _seed_submission(suffix="f")

    class RecoveringInbox:
        def __init__(self):
            self.calls = 0

        async def note(self, _text):
            from app.firstmate_intake import FirstmateIntakeError
            self.calls += 1
            if self.calls == 1:
                raise FirstmateIntakeError("runtime-unavailable")

    inbox = RecoveringInbox()
    monkeypatch.setattr(objective_cancellation_service, "inbox", inbox)
    body = {"idempotency_key": "cancel-retry-objective-f"}
    first = client.post(
        f"/api/v1/fleet/objectives/{objective_id}/cancellation-requests",
        headers=TEST_HEADERS, json=body,
    )
    assert first.status_code == 503
    assert "Firstmate could not be notified" in first.json()["detail"]
    with sqlite3.connect(db.DB_PATH) as connection:
        recorded = connection.execute(
            """SELECT status, notification_status FROM objective_cancellation_requests
               WHERE owner_user_id = 'default_user' AND objective_id = ?""",
            (objective_id,),
        ).fetchone()
    assert recorded == ("requested", "failed")

    retried = client.post(
        f"/api/v1/fleet/objectives/{objective_id}/cancellation-requests",
        headers=TEST_HEADERS, json=body,
    )
    assert retried.status_code == 202
    assert retried.json()["duplicate"] is True
    assert inbox.calls == 2
    with sqlite3.connect(db.DB_PATH) as connection:
        rows = connection.execute(
            """SELECT status FROM objective_cancellation_requests
               WHERE owner_user_id = 'default_user' AND objective_id = ?""",
            (objective_id,),
        ).fetchall()
    assert rows == [("requested",)]


@pytest.mark.asyncio
async def test_startup_recovers_a_persisted_undelivered_cancellation(monkeypatch):
    objective_id, _, _ = _seed_submission(suffix="a")

    class Inbox:
        def __init__(self):
            self.fail = True
            self.notes = []

        async def note(self, text):
            from app.firstmate_intake import FirstmateIntakeError
            self.notes.append(text)
            if self.fail:
                raise FirstmateIntakeError("runtime-unavailable")

    inbox = Inbox()
    monkeypatch.setattr(objective_cancellation_service, "inbox", inbox)
    response = client.post(
        f"/api/v1/fleet/objectives/{objective_id}/cancellation-requests",
        headers=TEST_HEADERS,
        json={"idempotency_key": "cancel-startup-objective-a"},
    )
    assert response.status_code == 503
    with sqlite3.connect(db.DB_PATH) as connection:
        updated_at = connection.execute(
            """SELECT updated_at FROM objective_cancellation_requests
               WHERE owner_user_id = 'default_user' AND objective_id = ?""",
            (objective_id,),
        ).fetchone()[0]
    inbox.fail = False

    skipped = await objective_cancellation_service.recover_pending(
        updated_before_ms=updated_at,
    )
    assert skipped == {"examined": 0, "recovered": 0, "failed": 0, "terminal": 0}
    recovery = await objective_cancellation_service.recover_pending(
        updated_before_ms=updated_at + 1,
    )
    assert recovery["recovered"] >= 1
    with sqlite3.connect(db.DB_PATH) as connection:
        row = connection.execute(
            """SELECT status, notification_status, notification_attempt_count, notified_at
               FROM objective_cancellation_requests
               WHERE owner_user_id = 'default_user' AND objective_id = ?""",
            (objective_id,),
        ).fetchone()
    assert row[0:3] == ("requested", "delivered", 2)
    assert row[3] is not None
    assert len(inbox.notes) == 2
