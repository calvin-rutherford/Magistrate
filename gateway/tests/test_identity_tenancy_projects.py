import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.account_lifecycle import delete_account, enforce_retention
from app.auth import issue_session
from app.magi_chat_store import MagiChatStore, MagiChatNotFound
from app.main import app
from app.oauth_transactions import OAuthTransactionStore
from app.uploads import get_upload, save_upload


client = TestClient(app)


def _tenant(monkeypatch, user_id: str) -> dict[str, str]:
    db.update_profile(user_id, name=user_id, email=f"{user_id}@example.test")
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", user_id)
    token = issue_session("test-bootstrap-secret")["session_token"]
    return {"Authorization": f"Bearer {token}"}


def test_projects_and_repositories_are_durable_and_tenant_opaque(monkeypatch):
    assert client.get("/api/v1/projects").status_code == 401
    original_owner = "default_user"
    owner_a = "isolation-project-a"
    owner_b = "isolation-project-b"
    headers_a = _tenant(monkeypatch, owner_a)
    headers_b = _tenant(monkeypatch, owner_b)
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", original_owner)

    created = client.post(
        "/api/v1/projects",
        headers=headers_a,
        json={"name": "Private Control Plane", "slug": "private-control-plane", "description": "A only"},
    )
    assert created.status_code == 201
    project = created.json()
    bound = client.post(
        f"/api/v1/projects/{project['id']}/repositories",
        headers=headers_a,
        json={
            "full_name": "acme/private-control-plane",
            "html_url": "https://github.com/acme/private-control-plane",
            "provider_repository_id": "R_private_1",
            "default_branch": "main",
        },
    )
    assert bound.status_code == 201

    assert [item["id"] for item in client.get("/api/v1/projects", headers=headers_a).json()["projects"]] == [project["id"]]
    assert client.get(f"/api/v1/projects/{project['id']}", headers=headers_b).status_code == 404
    assert client.patch(
        f"/api/v1/projects/{project['id']}", headers=headers_b, json={"name": "stolen"}
    ).status_code == 404
    assert client.delete(
        f"/api/v1/projects/{project['id']}/repositories/{bound.json()['id']}", headers=headers_b
    ).status_code == 404
    assert client.get("/api/v1/projects", headers=headers_b).json()["projects"] == []
    # Deployment-level provider sessions are owner-only until a provider-native
    # per-principal client replaces them; they cannot leak operator data.
    assert client.get("/api/v1/github/pulls", headers=headers_b).status_code == 403
    assert client.get("/api/v1/usage", headers=headers_b).status_code == 403
    recent = client.get("/api/v1/recent-activity", headers=headers_b)
    assert recent.status_code == 200
    assert recent.json()["sources"]["github"] == "unavailable"

    archived = client.patch(
        f"/api/v1/projects/{project['id']}", headers=headers_a, json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert client.get("/api/v1/projects", headers=headers_a).json()["projects"] == []
    assert len(client.get("/api/v1/projects?include_archived=true", headers=headers_a).json()["projects"]) == 1


def test_migration_failure_rolls_back_schema_and_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "migration.sqlite3")

    def fail(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE should_rollback (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO should_rollback(id) VALUES ('partial')")
        raise RuntimeError("injected migration failure")

    with pytest.raises(RuntimeError, match="injected"):
        db.apply_schema_migrations(connection, ((99, "failing-test-migration", fail),))
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
    ).fetchone() is None
    assert connection.execute("SELECT 1 FROM schema_migrations WHERE version = 99").fetchone() is None
    connection.close()


def test_account_deletion_erases_only_authenticated_tenant_across_domains(monkeypatch, tmp_path):
    owner_a = "erase-tenant-a"
    owner_b = "retain-tenant-b"
    headers_a = _tenant(monkeypatch, owner_a)
    headers_b = _tenant(monkeypatch, owner_b)
    monkeypatch.setenv("MAGISTRATE_BOOTSTRAP_USER_ID", "default_user")
    monkeypatch.setenv("MAGISTRATE_CHAT_UPLOAD_DIR", str(tmp_path / "uploads"))

    projects = {}
    for owner, headers in ((owner_a, headers_a), (owner_b, headers_b)):
        response = client.post(
            "/api/v1/projects", headers=headers,
            json={"name": f"Project {owner}", "slug": f"project-{owner}"},
        )
        assert response.status_code == 201
        projects[owner] = response.json()["id"]
        repository = client.post(
            f"/api/v1/projects/{projects[owner]}/repositories", headers=headers,
            json={"full_name": f"acme/{owner}", "html_url": f"https://github.com/acme/{owner}"},
        )
        assert repository.status_code == 201

    store = MagiChatStore()
    messages = {
        owner: store.prepare_submission(owner, f"client-message-{index:04d}", f"private message for {owner}")
        for index, owner in enumerate((owner_a, owner_b), start=1)
    }
    uploads = {
        owner: save_upload(owner, f"{owner}.txt", "text/plain", f"artifact-{owner}".encode())
        for owner in (owner_a, owner_b)
    }
    upload_a_path = Path(get_upload(owner_a, uploads[owner_a]["upload_id"])["path"])
    upload_b_path = Path(get_upload(owner_b, uploads[owner_b]["upload_id"])["path"])

    now = int(time.time() * 1000)
    with sqlite3.connect(db.DB_PATH) as connection:
        for owner in (owner_a, owner_b):
            prepared = messages[owner]
            project_id = projects[owner]
            connection.execute(
                """INSERT INTO magi_objective_submissions
                   (objective_id, task_id, owner_user_id, invocation_key, conversation_id, turn_id,
                    user_message_id, assistant_message_id, contract_json, contract_sha256,
                    display_title, project_id, status, attempt_count, accepted_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, 'accepted', 1, ?, ?, ?)""",
                (f"obj-{owner}", f"task-{owner}", owner, f"invoke-{owner}", prepared.conversation_id,
                 prepared.turn_id, prepared.user_message_id, prepared.assistant_message_id,
                 "a" * 64, f"Objective {owner}", project_id, now, now, now),
            )
            connection.execute(
                """INSERT INTO activity_records
                   (id,user_id,source_instance_id,source_event_id,record_key,sequence_index,revision,
                    kind,state,importance,title,summary,summary_truncated,project,observed_at,
                    refs_json,source_payload_sha256,created_at,updated_at)
                   VALUES (?,?,?,?,?,1,1,'objective.started','active','routine',?,?,0,?,?,'[]',?,?,?)""",
                (f"activity-{owner}", owner, "firstmate", f"event-{owner}", f"record-{owner}",
                 f"Activity {owner}", f"private activity {owner}", f"Project {owner}", now,
                 "b" * 64, now, now),
            )
            connection.execute(
                "INSERT INTO activity_changes(user_id,change_sequence,record_id,record_revision,changed_at) VALUES (?,1,?,1,?)",
                (owner, f"activity-{owner}", now),
            )
            connection.execute(
                "INSERT INTO project_memories(memory_id,owner_user_id,project_id,memory_key,value_enc,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (f"memory-{owner}", owner, project_id, "private", db.encrypt_token(f"memory {owner}"), now, now),
            )
            connection.execute(
                "INSERT INTO billing_accounts(owner_user_id,provider,status,created_at,updated_at) VALUES (?,'stripe','active',?,?)",
                (owner, now, now),
            )
            connection.execute(
                "INSERT INTO account_onboarding(user_id,welcome_completed_at,created_at,updated_at) VALUES (?,?,?,?)",
                (owner, now, now, now),
            )
            connection.execute(
                "INSERT INTO account_credit_ledger(credit_event_id,owner_user_id,project_id,amount_microunits,reason,provider_event_id,created_at) VALUES (?,?,?,1000,'grant',?,?)",
                (f"credit-{owner}", owner, project_id, f"provider-{owner}", now),
            )
    for owner in (owner_a, owner_b):
        db.save_execution_credential(owner, "openai", f"secret-{owner}")
        OAuthTransactionStore().create(owner, "github", "magistrate://account")

    fleet_b = client.get("/api/v1/fleet", headers=headers_b)
    assert fleet_b.status_code == 200
    assert owner_b in str(fleet_b.json())
    assert owner_a not in str(fleet_b.json())
    activity_b = client.get("/api/v1/activity", headers=headers_b)
    assert activity_b.status_code == 200
    assert f"private activity {owner_b}" in str(activity_b.json())
    assert f"private activity {owner_a}" not in str(activity_b.json())
    attention_b = client.get("/api/v1/attention", headers=headers_b)
    assert attention_b.status_code == 200
    assert owner_a not in str(attention_b.json())

    with pytest.raises(RuntimeError, match="injected account deletion failure"):
        delete_account(owner_a, confirmation=f"DELETE {owner_a}", fail_after_stage=True)
    assert upload_a_path.exists()
    assert client.get(f"/api/v1/projects/{projects[owner_a]}", headers=headers_a).status_code == 200

    rejected = client.request(
        "DELETE", "/api/v1/account", headers=headers_a,
        json={"confirmation": f"DELETE {owner_b}"},
    )
    assert rejected.status_code == 409
    assert client.get(f"/api/v1/projects/{projects[owner_a]}", headers=headers_a).status_code == 200

    response = client.request(
        "DELETE", "/api/v1/account", headers=headers_a,
        json={"confirmation": f"DELETE {owner_a}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert not upload_a_path.exists()
    assert upload_b_path.exists()

    assert client.get("/api/v1/projects", headers=headers_a).status_code == 401
    assert client.get("/api/v1/projects", headers=headers_b).status_code == 200
    assert client.get(f"/api/v1/projects/{projects[owner_b]}", headers=headers_b).status_code == 200
    assert get_upload(owner_a, uploads[owner_a]["upload_id"]) is None
    assert get_upload(owner_b, uploads[owner_b]["upload_id"]) is not None
    with pytest.raises(MagiChatNotFound):
        store.submission(owner_a, "client-message-0001")
    assert store.submission(owner_b, "client-message-0002")["user_message"]["content"] == f"private message for {owner_b}"

    with sqlite3.connect(db.DB_PATH) as connection:
        checks = (
            ("user_profiles", "user_id"), ("projects", "owner_user_id"),
            ("project_repositories", "owner_user_id"), ("magi_messages", "owner_user_id"),
            ("magi_objective_submissions", "owner_user_id"), ("activity_records", "user_id"),
            ("project_memories", "owner_user_id"), ("billing_accounts", "owner_user_id"),
            ("account_onboarding", "user_id"), ("account_credit_ledger", "owner_user_id"),
            ("execution_credentials", "user_id"),
            ("gateway_sessions", "user_id"), ("chat_uploads", "user_id"),
            ("oauth_transactions", "principal_id"),
        )
        for table, column in checks:
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (owner_a,)).fetchone()[0] == 0, table
            assert connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (owner_b,)).fetchone()[0] > 0, table


def test_retention_purges_only_expired_auth_control_rows():
    now = 10_000_000
    old = now - (31 * 24 * 3600)
    live = now + 3600
    db.update_profile("retention-owner", name="Retention", email="retention@example.test")
    OAuthTransactionStore().initialize()
    with sqlite3.connect(db.DB_PATH) as connection:
        connection.executemany(
            """INSERT INTO gateway_sessions
               (session_id,token_hash,user_id,scopes,issued_at,expires_at)
               VALUES (?,?,?,?,?,?)""",
            [
                ("retention-old", "1" * 64, "retention-owner", "read", old - 1, old),
                ("retention-live", "2" * 64, "retention-owner", "read", now, live),
            ],
        )
        connection.executemany(
            """INSERT INTO provider_auth_challenges
               (challenge_id,provider,action,nonce_hash,owner_user_id,client_platform,
                created_at,expires_at)
               VALUES (?,'google','sign_in',?,?,'native',?,?)""",
            [
                ("challenge-old", "3" * 64, "retention-owner", old - 1, old),
                ("challenge-live", "4" * 64, "retention-owner", now, live),
            ],
        )
        connection.executemany(
            "INSERT INTO oauth_transactions(state_hash,principal_id,provider,redirect_uri,expires_at) VALUES (?,?,'github','magistrate://account',?)",
            [("5" * 64, "retention-owner", old), ("6" * 64, "retention-owner", live)],
        )

    report = enforce_retention(now=now)
    assert report["sessions"] == 1
    assert report["challenges"] == 1
    assert report["oauth_transactions"] == 1
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute("SELECT session_id FROM gateway_sessions WHERE session_id LIKE 'retention-%'").fetchall() == [("retention-live",)]
        assert connection.execute("SELECT challenge_id FROM provider_auth_challenges WHERE challenge_id LIKE 'challenge-%'").fetchall() == [("challenge-live",)]
        assert connection.execute("SELECT state_hash FROM oauth_transactions WHERE principal_id='retention-owner'").fetchall() == [("6" * 64,)]


def test_database_health_reports_current_integrity():
    health = db.database_health()
    assert health == {
        "status": "healthy",
        "backend": "sqlite",
        "schema_version": db.SCHEMA_VERSION,
        "expected_schema_version": db.SCHEMA_VERSION,
        "integrity": "ok",
        "latency_ms": health["latency_ms"],
        "multi_instance_safe": False,
    }
