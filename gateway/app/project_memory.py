"""Durable, scoped and selectively retrieved Magi project memory.

Memory is provider-independent SQLite state.  Every read is qualified by the
authenticated owner and the complete tenant/org/workspace/project scope; repo
memory may inherit only project-wide entries from the same scope.  The store
never reads a harness transcript and never uses an LLM to decide authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from typing import Any, Iterable, Sequence

from app import db
from app.activity_store import _contains_sensitive_text
from app.persistence import connect

MEMORY_SCHEMA = "magi.project-memory.v1"
MEMORY_CONTEXT_SCHEMA = "magi.context-plane.v1"
MEMORY_KINDS = frozenset({
    "goal", "architecture-decision", "user-decision", "conversation-fact",
    "repository", "prior-objective", "completed-outcome", "artifact",
    "failed-approach", "question", "preference", "fleet-outcome",
})
MAX_MEMORY_CONTENT_CHARS = 8_000
MAX_MEMORY_TITLE_CHARS = 240
MAX_MEMORY_ENTRIES_PER_SCOPE = 2_000
MAX_MEMORY_REVISIONS_PER_ENTRY = 100
MAX_MEMORY_SEARCH_RESULTS = 20
MAX_MEMORY_SEARCH_CANDIDATES = 500
MAX_MEMORY_QUERY_CHARS = 1_000
MAX_MEMORY_TERMS = 128
MAX_CONTEXT_ENTRIES = 12
MAX_CONTEXT_CHARS = 16_000
MAX_CONTEXT_ITEM_CHARS = 1_200
MAX_AUDIT_RESULTS = 200
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_SAFE_ENTRY = re.compile(r"^pmem_[A-Za-z0-9_-]{16,64}$")
_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "of", "on", "or", "that", "the", "this",
    "to", "use", "was", "we", "what", "when", "where", "which", "with", "you",
})


class MemoryConflict(RuntimeError):
    pass


class MemoryNotFound(LookupError):
    pass


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: str
    organization_id: str
    workspace_id: str
    project_id: str
    repository_id: str = ""

    @classmethod
    def for_owner(
        cls,
        owner_user_id: str,
        *,
        organization_id: str = "personal",
        workspace_id: str = "default",
        project_id: str | None = None,
        repository_id: str = "",
    ) -> "MemoryScope":
        project = project_id or os.getenv("MAGISTRATE_MAGI_PROJECT", "Magistrate")
        tenant = "tenant-" + hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:32]
        scope = cls(tenant, organization_id, workspace_id, project, repository_id)
        scope.validate()
        return scope

    @classmethod
    def for_project(
        cls,
        owner_user_id: str,
        project_reference: str,
        *,
        repository_reference: str = "",
    ) -> "MemoryScope":
        """Resolve only owner-bound durable project and repository identities."""
        if not _valid_owner(owner_user_id):
            raise ValueError("A bounded authenticated memory owner is required.")
        if not isinstance(project_reference, str) or not _SAFE_SCOPE.fullmatch(project_reference):
            raise MemoryNotFound("Project memory scope was not found.")
        if repository_reference and (
            not isinstance(repository_reference, str)
            or not _SAFE_SCOPE.fullmatch(repository_reference)
        ):
            raise MemoryNotFound("Project memory scope was not found.")
        db.init_db()
        connection = connect(db.DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            project = connection.execute(
                """SELECT project_id, workspace_id FROM projects
                   WHERE owner_user_id = ? AND status = 'active'
                     AND (project_id = ? OR slug = ? OR name = ?)
                   ORDER BY CASE WHEN project_id = ? THEN 0 WHEN slug = ? THEN 1 ELSE 2 END
                   LIMIT 1""",
                (
                    owner_user_id, project_reference, project_reference.lower(),
                    project_reference, project_reference, project_reference.lower(),
                ),
            ).fetchone()
            if project is None:
                raise MemoryNotFound("Project memory scope was not found.")
            repository_id = ""
            if repository_reference:
                repository = connection.execute(
                    """SELECT repository_id FROM project_repositories
                       WHERE owner_user_id = ? AND project_id = ?
                         AND (repository_id = ? OR full_name = ? OR provider_repository_id = ?)
                       LIMIT 1""",
                    (
                        owner_user_id, project["project_id"], repository_reference,
                        repository_reference, repository_reference,
                    ),
                ).fetchone()
                if repository is None:
                    raise MemoryNotFound("Project memory scope was not found.")
                repository_id = str(repository["repository_id"])
        finally:
            connection.close()
        scope = cls.for_owner(
            owner_user_id,
            organization_id="personal",
            workspace_id=str(project["workspace_id"]),
            project_id=str(project["project_id"]),
            repository_id=repository_id,
        )
        scope.validate()
        return scope

    def validate(self) -> None:
        values = (
            self.tenant_id, self.organization_id, self.workspace_id, self.project_id,
        )
        if any(not isinstance(value, str) or not _SAFE_SCOPE.fullmatch(value) for value in values):
            raise ValueError("Memory scope contains an invalid identifier.")
        if self.repository_id and not _SAFE_SCOPE.fullmatch(self.repository_id):
            raise ValueError("Memory repository scope is invalid.")

    def public(self) -> dict[str, str]:
        # tenant_id is an internal authorization qualifier, not a public user id.
        return {
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
        }


def _now_ms() -> int:
    return int(time.time_ns() // 1_000_000)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _valid_owner(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 128
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _bounded_text(value: object, maximum: int, *, field: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > maximum
        or any(unicodedata.category(char).startswith("C") for char in value)
        or _contains_sensitive_text(value)
    ):
        raise ValueError(f"Memory {field} must be bounded, inert, and free of credential material.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"Memory {field} contains invalid Unicode.") from exc
    return value


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    found = re.findall(r"[\w][\w./:#-]{1,63}", normalized, flags=re.UNICODE)
    meaningful = (term for term in found if term not in _SEARCH_STOP_WORDS)
    # Stable de-duplication also bounds pathological repeated text.
    return tuple(dict.fromkeys(meaningful))[:MAX_MEMORY_TERMS]


def _connect():
    db.init_db()
    connection = connect(db.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _scope_params(owner_user_id: str, scope: MemoryScope) -> tuple[str, ...]:
    return (
        owner_user_id, scope.tenant_id, scope.organization_id,
        scope.workspace_id, scope.project_id,
    )


def _entry_hash(
    kind: str, title: str, content: str, importance: int,
    source_kind: str, source_id: str,
) -> str:
    return hashlib.sha256(_canonical_json({
        "kind": kind, "title": title, "content": content, "importance": importance,
        "source_kind": source_kind, "source_id": source_id,
    }).encode("utf-8")).hexdigest()


def _public_entry(row: sqlite3.Row, *, score: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": row["entry_id"],
        "kind": row["kind"],
        "title": row["title"],
        "content": row["content"],
        "importance": int(row["importance"]),
        "repository_id": row["repository_id"],
        "revision": int(row["revision"]),
        "source": {"kind": row["source_kind"], "id": row["source_id"]},
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }
    if score is not None:
        result["score"] = score
    return result


class ProjectMemoryStore:
    """Bounded memory, inverted search index, revisions, and hash-chained audit."""

    @staticmethod
    def _validate(owner_user_id: str, scope: MemoryScope) -> None:
        if not _valid_owner(owner_user_id):
            raise ValueError("A bounded authenticated memory owner is required.")
        if not isinstance(scope, MemoryScope):
            raise ValueError("A typed memory scope is required.")
        scope.validate()
        expected_tenant = MemoryScope.for_owner(
            owner_user_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            repository_id=scope.repository_id,
        ).tenant_id
        if scope.tenant_id != expected_tenant:
            raise ValueError("Memory tenant does not match the authenticated owner.")

    @staticmethod
    def _entry_id(owner_user_id: str, scope: MemoryScope, memory_key: str) -> str:
        digest = hashlib.sha256(
            ("magi-memory-v1\0" + "\0".join((
                owner_user_id, scope.tenant_id, scope.organization_id,
                scope.workspace_id, scope.project_id, scope.repository_id, memory_key,
            ))).encode("utf-8")
        ).hexdigest()
        return "pmem_" + digest[:40]

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        scope: MemoryScope,
        entry_id: str,
        revision: int,
        operation: str,
        actor_session_id: str,
        content_sha256: str,
        occurred_at: int,
    ) -> None:
        previous = connection.execute(
            """SELECT event_sha256 FROM project_memory_audit
               WHERE owner_user_id = ? AND tenant_id = ? AND organization_id = ?
                 AND workspace_id = ? AND project_id = ? AND repository_id = ?
               ORDER BY sequence DESC LIMIT 1""",
            (*_scope_params(owner_user_id, scope), scope.repository_id),
        ).fetchone()
        prior_hash = str(previous[0]) if previous else ""
        sequence = connection.execute(
            """SELECT COALESCE(MAX(sequence), 0) + 1 FROM project_memory_audit
               WHERE owner_user_id = ? AND tenant_id = ? AND organization_id = ?
                 AND workspace_id = ? AND project_id = ? AND repository_id = ?""",
            (*_scope_params(owner_user_id, scope), scope.repository_id),
        ).fetchone()[0]
        event = {
            "sequence": int(sequence), "entry_id": entry_id, "revision": revision,
            "operation": operation, "actor_session_id": actor_session_id,
            "content_sha256": content_sha256, "occurred_at": occurred_at,
            "previous_event_sha256": prior_hash,
        }
        event_hash = hashlib.sha256(_canonical_json(event).encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO project_memory_audit
               (owner_user_id, tenant_id, organization_id, workspace_id, project_id,
                repository_id, sequence, entry_id, revision, operation,
                actor_session_id, content_sha256, previous_event_sha256,
                event_sha256, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*_scope_params(owner_user_id, scope), scope.repository_id, int(sequence),
             entry_id, revision, operation, actor_session_id, content_sha256,
             prior_hash, event_hash, occurred_at),
        )

    def put(
        self,
        owner_user_id: str,
        scope: MemoryScope,
        *,
        memory_key: str,
        kind: str,
        title: str,
        content: str,
        importance: int = 3,
        source_kind: str = "user",
        source_id: str = "explicit",
        actor_session_id: str = "system",
        immutable: bool = False,
    ) -> dict[str, Any]:
        self._validate(owner_user_id, scope)
        if not isinstance(memory_key, str) or not _SAFE_KEY.fullmatch(memory_key):
            raise ValueError("Memory key is invalid.")
        if kind not in MEMORY_KINDS:
            raise ValueError("Memory kind is unsupported.")
        title = _bounded_text(title, MAX_MEMORY_TITLE_CHARS, field="title")
        content = _bounded_text(content, MAX_MEMORY_CONTENT_CHARS, field="content")
        if type(importance) is not int or not 1 <= importance <= 5:
            raise ValueError("Memory importance must be between 1 and 5.")
        if (
            not isinstance(source_kind, str) or not _SAFE_KEY.fullmatch(source_kind)
            or not isinstance(source_id, str) or not _SAFE_KEY.fullmatch(source_id)
            or not isinstance(actor_session_id, str) or not _SAFE_ACTOR.fullmatch(actor_session_id)
        ):
            raise ValueError("Memory provenance is invalid.")
        entry_id = self._entry_id(owner_user_id, scope, memory_key)
        content_hash = _entry_hash(
            kind, title, content, importance, source_kind, source_id,
        )
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM project_memory_entries WHERE entry_id = ? AND owner_user_id = ?",
                (entry_id, owner_user_id),
            ).fetchone()
            if existing is None:
                count = connection.execute(
                    """SELECT COUNT(*) FROM project_memory_entries
                       WHERE owner_user_id = ? AND tenant_id = ? AND organization_id = ?
                         AND workspace_id = ? AND project_id = ? AND repository_id = ?
                         AND deleted_at IS NULL""",
                    (*_scope_params(owner_user_id, scope), scope.repository_id),
                ).fetchone()[0]
                if int(count) >= MAX_MEMORY_ENTRIES_PER_SCOPE:
                    raise MemoryConflict("The bounded memory scope is full.")
                revision = 1
                connection.execute(
                    """INSERT INTO project_memory_entries
                       (entry_id, owner_user_id, tenant_id, organization_id, workspace_id,
                        project_id, repository_id, memory_key, kind, title, content,
                        importance, source_kind, source_id, revision, content_sha256,
                        created_at, updated_at, deleted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (entry_id, *_scope_params(owner_user_id, scope), scope.repository_id,
                     memory_key, kind, title, content, importance, source_kind, source_id,
                     revision, content_hash, now, now),
                )
                operation = "created"
            else:
                if (
                    existing["tenant_id"] != scope.tenant_id
                    or existing["organization_id"] != scope.organization_id
                    or existing["workspace_id"] != scope.workspace_id
                    or existing["project_id"] != scope.project_id
                    or existing["repository_id"] != scope.repository_id
                    or existing["memory_key"] != memory_key
                ):
                    raise MemoryConflict("Memory identity is already bound to another scope.")
                if existing["deleted_at"] is not None:
                    raise MemoryConflict("Deleted memory identities cannot be reused.")
                if existing["content_sha256"] == content_hash:
                    connection.commit()
                    return _public_entry(existing)
                if immutable:
                    raise MemoryConflict(
                        "Immutable memory identity is already bound to different content."
                    )
                revision = int(existing["revision"]) + 1
                # Reserve the final revision for an always-available tombstone.
                if revision >= MAX_MEMORY_REVISIONS_PER_ENTRY:
                    raise MemoryConflict("The bounded memory revision limit was reached.")
                connection.execute(
                    """UPDATE project_memory_entries
                       SET kind = ?, title = ?, content = ?, importance = ?, source_kind = ?,
                           source_id = ?, revision = ?, content_sha256 = ?, updated_at = ?
                       WHERE entry_id = ? AND owner_user_id = ?""",
                    (kind, title, content, importance, source_kind, source_id, revision,
                     content_hash, now, entry_id, owner_user_id),
                )
                operation = "updated"
            snapshot = _canonical_json({
                "kind": kind, "title": title, "content": content,
                "importance": importance, "source_kind": source_kind, "source_id": source_id,
            })
            connection.execute(
                """INSERT INTO project_memory_revisions
                   (entry_id, revision, snapshot_json, content_sha256, actor_session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry_id, revision, snapshot, content_hash, actor_session_id, now),
            )
            connection.execute("DELETE FROM project_memory_terms WHERE entry_id = ?", (entry_id,))
            connection.executemany(
                "INSERT INTO project_memory_terms (entry_id, term) VALUES (?, ?)",
                [(entry_id, term) for term in _tokens(title + "\n" + content)],
            )
            self._append_audit(
                connection, owner_user_id=owner_user_id, scope=scope,
                entry_id=entry_id, revision=revision, operation=operation,
                actor_session_id=actor_session_id, content_sha256=content_hash,
                occurred_at=now,
            )
            row = connection.execute(
                "SELECT * FROM project_memory_entries WHERE entry_id = ?", (entry_id,),
            ).fetchone()
            connection.commit()
            return _public_entry(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete(
        self, owner_user_id: str, scope: MemoryScope, entry_id: str, *, actor_session_id: str,
    ) -> None:
        self._validate(owner_user_id, scope)
        if not isinstance(entry_id, str) or not _SAFE_ENTRY.fullmatch(entry_id):
            raise MemoryNotFound("Project memory not found.")
        if not isinstance(actor_session_id, str) or not _SAFE_ACTOR.fullmatch(actor_session_id):
            raise ValueError("Memory actor identity is invalid.")
        now = _now_ms()
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM project_memory_entries
                   WHERE entry_id = ? AND owner_user_id = ? AND tenant_id = ?
                     AND organization_id = ? AND workspace_id = ? AND project_id = ?
                     AND repository_id = ? AND deleted_at IS NULL""",
                (entry_id, *_scope_params(owner_user_id, scope), scope.repository_id),
            ).fetchone()
            if row is None:
                raise MemoryNotFound("Project memory not found.")
            revision = int(row["revision"]) + 1
            connection.execute(
                """UPDATE project_memory_entries SET revision = ?, deleted_at = ?, updated_at = ?
                   WHERE entry_id = ?""", (revision, now, now, entry_id),
            )
            connection.execute("DELETE FROM project_memory_terms WHERE entry_id = ?", (entry_id,))
            tombstone_hash = hashlib.sha256(
                f"deleted\0{entry_id}\0{revision}\0{row['content_sha256']}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                """INSERT INTO project_memory_revisions
                   (entry_id, revision, snapshot_json, content_sha256, actor_session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry_id, revision, '{"deleted":true}', tombstone_hash, actor_session_id, now),
            )
            self._append_audit(
                connection, owner_user_id=owner_user_id, scope=scope,
                entry_id=entry_id, revision=revision, operation="deleted",
                actor_session_id=actor_session_id, content_sha256=tombstone_hash,
                occurred_at=now,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def search(
        self,
        owner_user_id: str,
        scope: MemoryScope,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 10,
        purpose: str = "interactive-search",
        actor_session_id: str = "system",
    ) -> list[dict[str, Any]]:
        self._validate(owner_user_id, scope)
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_MEMORY_QUERY_CHARS:
            raise ValueError("Memory search query is invalid.")
        query_terms = _tokens(query)
        if not query_terms:
            raise ValueError("Memory search query has no searchable terms.")
        selected_kinds = set(kinds or MEMORY_KINDS)
        if not selected_kinds or not selected_kinds.issubset(MEMORY_KINDS):
            raise ValueError("Memory search kind is unsupported.")
        if type(limit) is not int or not 1 <= limit <= MAX_MEMORY_SEARCH_RESULTS:
            raise ValueError("Memory search limit is invalid.")
        if not isinstance(purpose, str) or not _SAFE_KEY.fullmatch(purpose):
            raise ValueError("Memory retrieval purpose is invalid.")
        if not isinstance(actor_session_id, str) or not _SAFE_ACTOR.fullmatch(actor_session_id):
            raise ValueError("Memory retrieval actor is invalid.")
        placeholders = ",".join("?" for _ in query_terms)
        kind_placeholders = ",".join("?" for _ in selected_kinds)
        repo_values = ("", scope.repository_id) if scope.repository_id else ("",)
        repo_placeholders = ",".join("?" for _ in repo_values)
        with _connect() as connection:
            rows = connection.execute(
                f"""SELECT entry.*, COUNT(DISTINCT term.term) AS matched_terms
                    FROM project_memory_entries AS entry
                    JOIN project_memory_terms AS term ON term.entry_id = entry.entry_id
                    WHERE entry.owner_user_id = ? AND entry.tenant_id = ?
                      AND entry.organization_id = ? AND entry.workspace_id = ?
                      AND entry.project_id = ? AND entry.repository_id IN ({repo_placeholders})
                      AND entry.deleted_at IS NULL AND entry.kind IN ({kind_placeholders})
                      AND term.term IN ({placeholders})
                    GROUP BY entry.entry_id
                    ORDER BY matched_terms DESC, entry.importance DESC, entry.updated_at DESC
                    LIMIT ?""",
                (*_scope_params(owner_user_id, scope), *repo_values, *sorted(selected_kinds),
                 *query_terms, MAX_MEMORY_SEARCH_CANDIDATES),
            ).fetchall()
            ranked: list[tuple[int, sqlite3.Row]] = []
            now = _now_ms()
            for row in rows:
                matched = int(row["matched_terms"])
                exact_title = sum(term in _tokens(str(row["title"])) for term in query_terms)
                repo_specific = int(bool(scope.repository_id) and row["repository_id"] == scope.repository_id)
                recent = int(now - int(row["updated_at"]) < 30 * 24 * 3600 * 1000)
                score = matched * 100 + exact_title * 25 + int(row["importance"]) * 5 + repo_specific * 4 + recent
                ranked.append((score, row))
            ranked.sort(key=lambda pair: (pair[0], int(pair[1]["updated_at"]), pair[1]["entry_id"]), reverse=True)
            selected = ranked[:limit]
            retrieval_id = "pmr_" + secrets.token_urlsafe(18)
            connection.execute(
                """INSERT INTO project_memory_retrievals
                   (retrieval_id, owner_user_id, tenant_id, organization_id, workspace_id,
                    project_id, repository_id, purpose, actor_session_id, query_sha256,
                    selected_ids_json, result_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (retrieval_id, *_scope_params(owner_user_id, scope), scope.repository_id,
                 purpose, actor_session_id, hashlib.sha256(query.encode("utf-8")).hexdigest(),
                 _canonical_json([row["entry_id"] for _, row in selected]), len(selected), _now_ms()),
            )
        return [_public_entry(row, score=score) for score, row in selected]

    def audit(
        self, owner_user_id: str, scope: MemoryScope, *, limit: int = 100,
    ) -> dict[str, Any]:
        self._validate(owner_user_id, scope)
        if type(limit) is not int or not 1 <= limit <= MAX_AUDIT_RESULTS:
            raise ValueError("Memory audit limit is invalid.")
        with _connect() as connection:
            rows = connection.execute(
                """SELECT * FROM project_memory_audit
                   WHERE owner_user_id = ? AND tenant_id = ? AND organization_id = ?
                     AND workspace_id = ? AND project_id = ? AND repository_id = ?
                   ORDER BY sequence DESC LIMIT ?""",
                (*_scope_params(owner_user_id, scope), scope.repository_id, limit),
            ).fetchall()
            retrievals = connection.execute(
                """SELECT retrieval_id, purpose, actor_session_id, selected_ids_json,
                          result_count, created_at
                   FROM project_memory_retrievals
                   WHERE owner_user_id = ? AND tenant_id = ? AND organization_id = ?
                     AND workspace_id = ? AND project_id = ? AND repository_id = ?
                   ORDER BY created_at DESC, retrieval_id DESC LIMIT ?""",
                (*_scope_params(owner_user_id, scope), scope.repository_id, limit),
            ).fetchall()
        events = [{
            "sequence": int(row["sequence"]), "entry_id": row["entry_id"],
            "revision": int(row["revision"]), "operation": row["operation"],
            "actor_session_id": row["actor_session_id"],
            "content_sha256": row["content_sha256"],
            "previous_event_sha256": row["previous_event_sha256"],
            "event_sha256": row["event_sha256"], "occurred_at": int(row["occurred_at"]),
        } for row in reversed(rows)]
        valid = True
        previous = events[0]["previous_event_sha256"] if events else ""
        for event in events:
            material = {
                "sequence": event["sequence"], "entry_id": event["entry_id"],
                "revision": event["revision"], "operation": event["operation"],
                "actor_session_id": event["actor_session_id"],
                "content_sha256": event["content_sha256"], "occurred_at": event["occurred_at"],
                "previous_event_sha256": previous,
            }
            valid = valid and event["previous_event_sha256"] == previous and hashlib.sha256(
                _canonical_json(material).encode("utf-8")
            ).hexdigest() == event["event_sha256"]
            previous = event["event_sha256"]
        return {
            "schema_version": MEMORY_SCHEMA,
            "scope": scope.public(),
            "events": list(reversed(events)),
            "retrievals": [{
                "retrieval_id": row["retrieval_id"], "purpose": row["purpose"],
                "actor_session_id": row["actor_session_id"],
                "selected_entry_ids": json.loads(row["selected_ids_json"]),
                "result_count": int(row["result_count"]), "created_at": int(row["created_at"]),
            } for row in retrievals],
            "chain_valid": valid,
        }

    def selected_context(
        self,
        owner_user_id: str,
        scope: MemoryScope,
        query: str,
        *,
        purpose: str,
        actor_session_id: str = "system",
        limit: int = MAX_CONTEXT_ENTRIES,
    ) -> list[dict[str, Any]]:
        if not _tokens(query):
            return []
        entries = self.search(
            owner_user_id, scope, query, limit=min(limit, MAX_CONTEXT_ENTRIES),
            purpose=purpose, actor_session_id=actor_session_id,
        )
        used = 0
        selected: list[dict[str, Any]] = []
        for entry in entries:
            content = entry["content"]
            if len(content) > MAX_CONTEXT_ITEM_CHARS:
                content = content[: MAX_CONTEXT_ITEM_CHARS - 1].rstrip() + "…"
            item = {
                "id": entry["id"], "kind": entry["kind"], "title": entry["title"],
                "content": content, "repository_id": entry["repository_id"],
                "revision": entry["revision"],
            }
            encoded_size = len(_canonical_json(item))
            if used + encoded_size > MAX_CONTEXT_CHARS:
                continue
            selected.append(item)
            used += encoded_size
        return selected


class MagiContextAssembler:
    """Build a bounded inert fact document without dumping full conversations."""

    def __init__(self, memory: ProjectMemoryStore | None = None) -> None:
        self.memory = memory or ProjectMemoryStore()

    def assemble_chat(
        self,
        owner_user_id: str,
        query: str,
        *,
        scope: MemoryScope,
        modality: str,
        attachments: Sequence[dict[str, Any]] | None = None,
        actor_session_id: str = "system",
    ) -> str:
        memories = self.memory.selected_context(
            owner_user_id, scope, query, purpose="provider-chat",
            actor_session_id=actor_session_id, limit=8,
        )
        tokens = set(_tokens(query))
        with _connect() as connection:
            project = connection.execute(
                """SELECT project_id, slug, name FROM projects
                   WHERE owner_user_id = ? AND project_id = ? LIMIT 1""",
                (owner_user_id, scope.project_id),
            ).fetchone()
            project_aliases = tuple(dict.fromkeys(
                [scope.project_id] + (
                    [str(project["slug"]), str(project["name"])] if project else []
                )
            ))
            alias_placeholders = ",".join("?" for _ in project_aliases)
            objective_rows = connection.execute(
                """SELECT objective_id, task_id, contract_json, project_id, status, updated_at
                   FROM magi_objective_submissions
                   WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT 40""",
                (owner_user_id,),
            ).fetchall()
            fleet_rows = connection.execute(
                f"""SELECT objective.objective_id, objective.task_id, objective.title,
                           objective.project, objective.terminal_phase, objective.updated_at,
                           submission.project_id,
                           (SELECT phase FROM firstmate_execution_events event
                            WHERE event.owner_user_id = objective.owner_user_id
                              AND event.objective_id = objective.objective_id
                            ORDER BY event.occurred_at DESC, event.created_at DESC LIMIT 1) AS latest_phase,
                           (SELECT evidence_json FROM firstmate_execution_events event
                            WHERE event.owner_user_id = objective.owner_user_id
                              AND event.objective_id = objective.objective_id
                              AND event.phase = 'objective.completed'
                            ORDER BY event.occurred_at DESC, event.created_at DESC LIMIT 1) AS evidence_json
                    FROM firstmate_execution_objectives objective
                    LEFT JOIN magi_objective_submissions submission
                      ON submission.owner_user_id = objective.owner_user_id
                     AND submission.objective_id = objective.objective_id
                    WHERE objective.owner_user_id = ?
                      AND (submission.project_id = ? OR objective.project IN ({alias_placeholders}))
                    ORDER BY objective.updated_at DESC LIMIT 20""",
                (owner_user_id, scope.project_id, *project_aliases),
            ).fetchall()
            decisions = connection.execute(
                f"""SELECT decision_id, title, question, state, revision, updated_at
                    FROM firstmate_decisions
                    WHERE owner_user_id = ?
                      AND (project IN ({alias_placeholders}) OR project IS NULL)
                      AND state IN ('pending','answering')
                    ORDER BY updated_at DESC LIMIT 5""",
                (owner_user_id, *project_aliases),
            ).fetchall()
        recent_intent: list[dict[str, Any]] = []
        for row in objective_rows:
            try:
                contract = json.loads(row["contract_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                not isinstance(contract.get("objective"), str)
                or (
                    row["project_id"] != scope.project_id
                    and contract.get("project") not in project_aliases
                )
            ):
                continue
            objective_tokens = set(_tokens(contract["objective"]))
            if tokens and not tokens.intersection(objective_tokens) and len(recent_intent) >= 2:
                continue
            recent_intent.append({
                "objective_id": row["objective_id"], "objective": contract["objective"][:1000],
                "status": row["status"], "updated_at": int(row["updated_at"]),
            })
            if len(recent_intent) == 3:
                break
        fleet: list[dict[str, Any]] = []
        for row in fleet_rows:
            if tokens and not tokens.intersection(_tokens(str(row["title"]))) and row["terminal_phase"] is not None:
                continue
            outcome: dict[str, Any] = {
                "objective_id": row["objective_id"], "title": row["title"],
                "phase": row["terminal_phase"] or row["latest_phase"] or "objective.accepted",
                "updated_at": int(row["updated_at"]),
            }
            if row["evidence_json"]:
                try:
                    evidence = json.loads(row["evidence_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    evidence = {}
                if isinstance(evidence, dict):
                    raw_checks = evidence.get("checks", [])
                    raw_artifacts = evidence.get("artifacts", [])
                    outcome["checks"] = [
                        {"kind": item.get("kind"), "label": item.get("label"), "status": item.get("status")}
                        for item in (raw_checks[:8] if isinstance(raw_checks, list) else [])
                        if isinstance(item, dict)
                    ]
                    outcome["artifacts"] = [
                        item for item in (
                            raw_artifacts[:8] if isinstance(raw_artifacts, list) else []
                        ) if isinstance(item, dict)
                    ]
            fleet.append(outcome)
            if len(fleet) == 5:
                break
        attention = [{
            "decision_id": row["decision_id"], "title": row["title"],
            "question": row["question"], "state": row["state"],
            "revision": int(row["revision"]),
        } for row in decisions]
        selected_media = [{
            "upload_id": item.get("upload_id"), "name": item.get("filename", item.get("name")),
            "media_type": item.get("media_type"), "size": item.get("size"),
        } for item in (attachments or ())[:10]]
        payload = {
            "schema_version": MEMORY_CONTEXT_SCHEMA,
            "scope": scope.public(),
            "modality": modality,
            "project_memory": memories,
            "recent_intent": recent_intent,
            "fleet_outcomes": fleet[:5],
            "attention": attention,
            "selected_multimodal_context": selected_media,
        }
        encoded = _canonical_json(payload)
        # Each component is already bounded; this final fail-closed guard avoids
        # silently passing a partial JSON document to a provider.
        if len(encoded) > MAX_CONTEXT_CHARS:
            payload["project_memory"] = payload["project_memory"][:4]
            payload["recent_intent"] = payload["recent_intent"][:2]
            payload["fleet_outcomes"] = payload["fleet_outcomes"][:3]
            payload["attention"] = payload["attention"][:3]
            encoded = _canonical_json(payload)
        if len(encoded) > MAX_CONTEXT_CHARS:
            raise RuntimeError("The bounded Magi context document exceeded its contract.")
        return encoded

    def assemble_worker(
        self, owner_user_id: str, scope: MemoryScope, objective: str,
    ) -> dict[str, Any]:
        memories = self.memory.selected_context(
            owner_user_id, scope, objective, purpose="objective-worker", limit=10,
        )
        payload = {
            "schema_version": MEMORY_CONTEXT_SCHEMA,
            "authority": "magistrate-persisted-context",
            "scope": scope.public(),
            "objective_context": memories,
        }
        encoded = _canonical_json(payload)
        if len(encoded) > MAX_CONTEXT_CHARS:
            raise RuntimeError("The bounded objective context exceeded its contract.")
        return payload
