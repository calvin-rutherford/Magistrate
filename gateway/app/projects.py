"""Durable, principal-owned projects and repository bindings.

Project identity is opaque and every operation requires the authenticated owner.
Names are presentation metadata; callers never select a tenant or workspace.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from app.persistence import connect
import time
import unicodedata
from typing import Any, Optional
from urllib.parse import urlsplit

from app import db

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ID = re.compile(r"^(?:prj|repo)_[A-Za-z0-9_-]{16,64}$")


class ProjectError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _clean_text(value: object, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ProjectError(f"{field} must be text.")
    cleaned = " ".join(value.strip().split())
    if (required and not cleaned) or len(cleaned) > maximum or any(
        unicodedata.category(char).startswith("C") for char in cleaned
    ):
        raise ProjectError(f"{field} is invalid.")
    return cleaned


def _slug(value: Optional[str], name: str) -> str:
    candidate = value.strip().lower() if isinstance(value, str) else re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(candidate) > 64 or not _SLUG.fullmatch(candidate):
        raise ProjectError("slug must contain only lowercase letters, numbers, and single hyphen-separated words.")
    return candidate


def _connect() -> sqlite3.Connection:
    db.init_db()
    connection = connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_personal_workspace(owner_user_id: str, *, connection: sqlite3.Connection | None = None) -> str:
    workspace_id = "wsp_" + hashlib.sha256(f"personal\0{owner_user_id}".encode()).hexdigest()[:32]
    own = connection is None
    conn = connection or _connect()
    now = _now_ms()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO workspaces
               (workspace_id, owner_user_id, kind, name, created_at, updated_at)
               VALUES (?, ?, 'personal', 'Personal', ?, ?)""",
            (workspace_id, owner_user_id, now, now),
        )
        if own:
            conn.commit()
        return workspace_id
    finally:
        if own:
            conn.close()


def _public_project(row: sqlite3.Row, repositories: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "project.v1",
        "id": row["project_id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "kind": row["kind"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "repositories": repositories or [],
    }


def _public_repository(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["repository_id"],
        "provider": row["provider"],
        "provider_repository_id": row["provider_repository_id"],
        "full_name": row["full_name"],
        "html_url": row["html_url"],
        "default_branch": row["default_branch"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _owned_project(conn: sqlite3.Connection, owner_user_id: str, project_id: str, *, include_archived: bool = True) -> sqlite3.Row:
    if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
        raise ProjectError("Project not found.", 404)
    suffix = "" if include_archived else " AND status = 'active'"
    row = conn.execute(
        f"SELECT * FROM projects WHERE project_id = ? AND owner_user_id = ?{suffix}",
        (project_id, owner_user_id),
    ).fetchone()
    if row is None:
        # Do not reveal whether another principal owns the opaque id.
        raise ProjectError("Project not found.", 404)
    return row


def create_project(owner_user_id: str, *, name: str, slug: Optional[str] = None, description: str = "") -> dict[str, Any]:
    clean_name = _clean_text(name, field="name", maximum=120)
    clean_slug = _slug(slug, clean_name)
    clean_description = _clean_text(description, field="description", maximum=2000, required=False)
    project_id = "prj_" + secrets.token_urlsafe(18)
    now = _now_ms()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        workspace_id = ensure_personal_workspace(owner_user_id, connection=conn)
        try:
            conn.execute(
                """INSERT INTO projects
                   (project_id, workspace_id, owner_user_id, name, slug, description,
                    kind, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'standalone', 'active', ?, ?)""",
                (project_id, workspace_id, owner_user_id, clean_name, clean_slug, clean_description, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ProjectError("A project with that slug already exists.", 409) from exc
        row = _owned_project(conn, owner_user_id, project_id)
    return _public_project(row)


def list_projects(owner_user_id: str, *, include_archived: bool = False, limit: int = 100) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ProjectError("limit must be between 1 and 200.")
    with _connect() as conn:
        where = "owner_user_id = ?" + ("" if include_archived else " AND status = 'active'")
        rows = conn.execute(
            f"SELECT * FROM projects WHERE {where} ORDER BY updated_at DESC, project_id LIMIT ?",
            (owner_user_id, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        project_ids = [row["project_id"] for row in visible]
        repositories = (
            conn.execute(
                f"""SELECT * FROM project_repositories
                    WHERE owner_user_id = ? AND project_id IN ({','.join('?' for _ in project_ids)})
                    ORDER BY full_name""",
                (owner_user_id, *project_ids),
            ).fetchall()
            if project_ids else []
        )
    by_project: dict[str, list[dict[str, Any]]] = {}
    for repository in repositories:
        by_project.setdefault(repository["project_id"], []).append(_public_repository(repository))
    return {
        "schema_version": "projects.v1",
        "projects": [_public_project(row, by_project.get(row["project_id"], [])) for row in visible],
        "has_more": len(rows) > limit,
    }


def get_project(owner_user_id: str, project_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = _owned_project(conn, owner_user_id, project_id)
        repositories = conn.execute(
            "SELECT * FROM project_repositories WHERE owner_user_id = ? AND project_id = ? ORDER BY full_name",
            (owner_user_id, project_id),
        ).fetchall()
        objective_count = conn.execute(
            "SELECT COUNT(*) FROM magi_objective_submissions WHERE owner_user_id = ? AND project_id = ?",
            (owner_user_id, project_id),
        ).fetchone()[0]
        activity_count = conn.execute(
            "SELECT COUNT(*) FROM activity_records WHERE user_id = ? AND project = ?",
            (owner_user_id, row["name"]),
        ).fetchone()[0]
    return {
        **_public_project(row, [_public_repository(item) for item in repositories]),
        "summary": {"objectives": objective_count, "activity_records": activity_count},
    }


def update_project(
    owner_user_id: str,
    project_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _owned_project(conn, owner_user_id, project_id)
        clean_name = _clean_text(name, field="name", maximum=120) if name is not None else current["name"]
        clean_description = (
            _clean_text(description, field="description", maximum=2000, required=False)
            if description is not None else current["description"]
        )
        next_status = status if status is not None else current["status"]
        if next_status not in {"active", "archived"}:
            raise ProjectError("status must be active or archived.")
        conn.execute(
            """UPDATE projects SET name = ?, description = ?, status = ?, updated_at = ?
               WHERE project_id = ? AND owner_user_id = ?""",
            (clean_name, clean_description, next_status, _now_ms(), project_id, owner_user_id),
        )
        row = _owned_project(conn, owner_user_id, project_id)
    return _public_project(row)


def delete_project(owner_user_id: str, project_id: str) -> None:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned_project(conn, owner_user_id, project_id)
        # Keep objective/activity history truthful. The project cannot be
        # deleted while execution history still references it; archive instead.
        references = conn.execute(
            "SELECT COUNT(*) FROM magi_objective_submissions WHERE owner_user_id = ? AND project_id = ?",
            (owner_user_id, project_id),
        ).fetchone()[0]
        if references:
            raise ProjectError("A project with objective history must be archived instead of deleted.", 409)
        conn.execute("DELETE FROM project_memories WHERE owner_user_id = ? AND project_id = ?", (owner_user_id, project_id))
        conn.execute("DELETE FROM project_repositories WHERE owner_user_id = ? AND project_id = ?", (owner_user_id, project_id))
        conn.execute("DELETE FROM projects WHERE owner_user_id = ? AND project_id = ?", (owner_user_id, project_id))


def bind_github_repository(
    owner_user_id: str,
    project_id: str,
    *,
    full_name: str,
    html_url: str,
    provider_repository_id: Optional[str] = None,
    default_branch: Optional[str] = None,
) -> dict[str, Any]:
    full_name = _clean_text(full_name, field="full_name", maximum=201)
    if not _GITHUB_NAME.fullmatch(full_name):
        raise ProjectError("full_name must be a GitHub owner/repository name.")
    parsed = urlsplit(html_url)
    normalized_path = parsed.path.strip("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or normalized_path.removesuffix(".git").lower() != full_name.lower():
        raise ProjectError("html_url must be the matching https://github.com repository URL.")
    repository_id = "repo_" + secrets.token_urlsafe(18)
    provider_id = _clean_text(provider_repository_id, field="provider_repository_id", maximum=128, required=False) if provider_repository_id else None
    branch = _clean_text(default_branch, field="default_branch", maximum=255, required=False) if default_branch else None
    now = _now_ms()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned_project(conn, owner_user_id, project_id, include_archived=False)
        repository_count = conn.execute(
            "SELECT COUNT(*) FROM project_repositories WHERE owner_user_id = ? AND project_id = ?",
            (owner_user_id, project_id),
        ).fetchone()[0]
        if repository_count >= 20:
            raise ProjectError("A project may bind at most 20 repositories.", 409)
        try:
            conn.execute(
                """INSERT INTO project_repositories
                   (repository_id, owner_user_id, project_id, provider, provider_repository_id,
                    full_name, html_url, default_branch, created_at, updated_at)
                   VALUES (?, ?, ?, 'github', ?, ?, ?, ?, ?, ?)""",
                (repository_id, owner_user_id, project_id, provider_id, full_name, f"https://github.com/{normalized_path.removesuffix('.git')}", branch, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ProjectError("That repository is already bound to one of your projects.", 409) from exc
        conn.execute("UPDATE projects SET kind = 'github', updated_at = ? WHERE project_id = ? AND owner_user_id = ?", (now, project_id, owner_user_id))
        row = conn.execute(
            "SELECT * FROM project_repositories WHERE repository_id = ? AND owner_user_id = ?",
            (repository_id, owner_user_id),
        ).fetchone()
    return _public_repository(row)


def unbind_repository(owner_user_id: str, project_id: str, repository_id: str) -> None:
    if not isinstance(repository_id, str) or not _ID.fullmatch(repository_id):
        raise ProjectError("Repository not found.", 404)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned_project(conn, owner_user_id, project_id)
        changed = conn.execute(
            "DELETE FROM project_repositories WHERE repository_id = ? AND project_id = ? AND owner_user_id = ?",
            (repository_id, project_id, owner_user_id),
        ).rowcount
        if changed != 1:
            raise ProjectError("Repository not found.", 404)
        remaining = conn.execute(
            "SELECT 1 FROM project_repositories WHERE project_id = ? AND owner_user_id = ? LIMIT 1",
            (project_id, owner_user_id),
        ).fetchone()
        if not remaining:
            conn.execute("UPDATE projects SET kind = 'standalone', updated_at = ? WHERE project_id = ? AND owner_user_id = ?", (_now_ms(), project_id, owner_user_id))


def resolve_project_id(owner_user_id: str, project_reference: str, *, connection: sqlite3.Connection | None = None) -> Optional[str]:
    """Resolve an active project by opaque id, slug, or exact display name."""
    own = connection is None
    conn = connection or _connect()
    try:
        row = conn.execute(
            """SELECT project_id FROM projects
               WHERE owner_user_id = ? AND status = 'active'
                 AND (project_id = ? OR slug = ? OR name = ?)
               ORDER BY CASE WHEN project_id = ? THEN 0 WHEN slug = ? THEN 1 ELSE 2 END
               LIMIT 1""",
            (owner_user_id, project_reference, project_reference.lower(), project_reference, project_reference, project_reference.lower()),
        ).fetchone()
        return row[0] if row else None
    finally:
        if own:
            conn.close()
