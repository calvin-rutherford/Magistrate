"""PostgreSQL multi-instance startup and tenant persistence smoke contract."""
from __future__ import annotations

import os
import sys

if not os.getenv("MAGISTRATE_DATABASE_URL"):
    raise SystemExit("MAGISTRATE_DATABASE_URL is required")
if len(sys.argv) != 2:
    raise SystemExit("usage: postgres_smoke.py TENANT")

owner = sys.argv[1]

from app import db
from app.magi_chat_store import MagiChatStore
from app.projects import bind_github_repository, create_project, get_project, list_projects
from app.project_memory import MemoryScope, ProjectMemoryStore
from app.uploads import get_upload, save_upload


db.update_profile(owner, name=f"Postgres {owner}", email=f"{owner}@example.test")
project = create_project(owner, name=f"Project {owner}", slug=f"project-{owner}")
repository = bind_github_repository(
    owner,
    project["id"],
    full_name=f"magistrate-smoke/{owner}",
    html_url=f"https://github.com/magistrate-smoke/{owner}",
)
memory_scope = MemoryScope.for_project(
    owner, project["id"], repository_reference=repository["id"],
)
memory = ProjectMemoryStore().put(
    owner,
    memory_scope,
    memory_key="postgres-isolation",
    kind="architecture-decision",
    title=f"PostgreSQL isolation for {owner}",
    content=f"Only {owner} may retrieve this PostgreSQL project memory.",
    source_kind="postgres-smoke",
    source_id=f"memory-{owner}",
    actor_session_id=f"session-{owner}",
)
message = MagiChatStore().prepare_submission(
    owner, f"postgres-smoke-{owner}", f"private message for {owner}"
)
db.save_execution_credential(owner, "smoke", f"private-secret-{owner}")
upload = save_upload(owner, f"{owner}.txt", "text/plain", f"artifact-{owner}".encode())

assert get_project(owner, project["id"])["repositories"][0]["id"] == repository["id"]
assert [item["id"] for item in list_projects(owner)["projects"]] == [project["id"]]
assert MagiChatStore().submission(owner, f"postgres-smoke-{owner}")["user_message"]["id"] == message.user_message_id
assert [item["id"] for item in ProjectMemoryStore().search(
    owner, memory_scope, "PostgreSQL project memory",
    purpose="postgres-smoke", actor_session_id=f"session-{owner}",
)] == [memory["id"]]
assert get_upload(owner, upload["upload_id"])["filename"] == f"{owner}.txt"
health = db.database_health()
assert health["status"] == "healthy"
assert health["backend"] == "postgresql"
assert health["multi_instance_safe"] is True
print(f"postgres smoke passed for {owner}")
