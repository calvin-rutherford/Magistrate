"""Verify two PostgreSQL smoke tenants remain isolated, then erase one."""
from __future__ import annotations

import os

if not os.getenv("MAGISTRATE_DATABASE_URL"):
    raise SystemExit("MAGISTRATE_DATABASE_URL is required")

from app import db
from app.account_lifecycle import delete_account
from app.magi_chat_store import MagiChatNotFound, MagiChatStore
from app.persistence import connect
from app.projects import ProjectError, list_projects, get_project
from app.project_memory import MemoryScope, ProjectMemoryStore
from app.push_receipts import PushDeliveryStore

projects_a = list_projects("tenant-a")["projects"]
projects_b = list_projects("tenant-b")["projects"]
assert len(projects_a) == len(projects_b) == 1
assert projects_a[0]["id"] != projects_b[0]["id"]
try:
    get_project("tenant-b", projects_a[0]["id"])
except ProjectError as exc:
    assert exc.status_code == 404
else:
    raise AssertionError("tenant-b read tenant-a project")
try:
    MagiChatStore().submission("tenant-b", "postgres-smoke-tenant-a")
except MagiChatNotFound:
    pass
else:
    raise AssertionError("tenant-b read tenant-a message")

scope_a = MemoryScope.for_project(
    "tenant-a", projects_a[0]["id"],
    repository_reference=projects_a[0]["repositories"][0]["id"],
)
scope_b = MemoryScope.for_project(
    "tenant-b", projects_b[0]["id"],
    repository_reference=projects_b[0]["repositories"][0]["id"],
)
assert ProjectMemoryStore().search(
    "tenant-b", scope_b, "tenant-a",
    purpose="postgres-isolation", actor_session_id="postgres-verify",
) == []
assert len(ProjectMemoryStore().search(
    "tenant-a", scope_a, "tenant-a",
    purpose="postgres-isolation", actor_session_id="postgres-verify",
)) == 1

with connect(db.DB_PATH) as connection:
    hosted = connection.execute(
        """SELECT owner_user_id,tenant_key,isolation_key,backend_execution_id
           FROM hosted_execution_runs ORDER BY owner_user_id"""
    ).fetchall()
assert [row[0] for row in hosted] == ["tenant-a", "tenant-b"]
assert len({row[1] for row in hosted}) == 2
assert len({row[2] for row in hosted}) == 2
assert len({row[3] for row in hosted}) == 2

# Exercise deletion ordering with live bearer authority and PostgreSQL FKs.
with connect(db.DB_PATH) as connection:
    connection.execute(
        """INSERT INTO gateway_sessions
           (session_id, token_hash, user_id, scopes, issued_at, expires_at)
           VALUES ('postgres-delete-session', ?, 'tenant-a', 'read,account', 1, 4102444800)""",
        ("f" * 64,),
    )

assert PushDeliveryStore(db.DB_PATH).summary("tenant-a") == {"pending": 1}
assert PushDeliveryStore(db.DB_PATH).summary("tenant-b") == {"pending": 1}
delete_account("tenant-a", confirmation="DELETE tenant-a")
assert PushDeliveryStore(db.DB_PATH).summary("tenant-a") == {}
assert PushDeliveryStore(db.DB_PATH).summary("tenant-b") == {"pending": 1}
with connect(db.DB_PATH) as connection:
    assert connection.execute(
        "SELECT COUNT(*) FROM hosted_execution_runs WHERE owner_user_id='tenant-a'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM hosted_execution_runs WHERE owner_user_id='tenant-b'"
    ).fetchone()[0] == 1
assert list_projects("tenant-a")["projects"] == []
assert list_projects("tenant-b")["projects"][0]["id"] == projects_b[0]["id"]
assert len(ProjectMemoryStore().search(
    "tenant-b", scope_b, "tenant-b",
    purpose="postgres-after-erasure", actor_session_id="postgres-verify",
)) == 1
assert MagiChatStore().submission("tenant-b", "postgres-smoke-tenant-b")["user_message"]["content"] == "private message for tenant-b"
print("postgres tenant isolation and selective account erasure passed")
